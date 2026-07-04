"""AI chat-command endpoints (Anthropic key management + the command relay).

Mounted on the device router (so every endpoint inherits device JWT auth) with
the ``/api/ai`` prefix:

* ``GET    /api/ai/key``     — is a key configured, and from where (never the key).
* ``PUT    /api/ai/key``     — store a user-supplied Anthropic API key (0600 file).
* ``DELETE /api/ai/key``     — remove the stored key.
* ``POST   /api/ai/command`` — run one chat command through the Claude tool loop.

The heavy lifting lives in :mod:`nomothetic.ai_command`; this module only maps
HTTP shapes onto it.  The command endpoint is rate limited (``ai_limiter`` on
``app.state``) because each call can fan out into several provider requests.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from nomothetic.ai_command import (
    AiCommandService,
    AiKeyStore,
    AiKeyStoreError,
    AiProviderAuthError,
    AiProviderError,
    AiUnavailableError,
    validate_api_key_format,
)
from nomothetic.rate_limit import ai_rate_limit

logger = logging.getLogger(__name__)


def _now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AiChatMessage(BaseModel):
    """One prior conversation turn (plain text only; tool blocks stay server-side)."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)


class AiCommandRequest(BaseModel):
    """A chat command: prior turns plus the new user message, oldest first."""

    messages: list[AiChatMessage] = Field(..., min_length=1, max_length=40)

    @model_validator(mode="after")
    def _check_roles(self) -> "AiCommandRequest":
        """Enforce the Messages API turn shape: starts and ends with the user, alternating."""
        if self.messages[0].role != "user":
            raise ValueError("first message must have role 'user'")
        if self.messages[-1].role != "user":
            raise ValueError("last message must have role 'user'")
        for prev, nxt in zip(self.messages, self.messages[1:]):
            if prev.role == nxt.role:
                raise ValueError("message roles must alternate between 'user' and 'assistant'")
        return self


class AiActionRecord(BaseModel):
    """One tool call the model made while handling the command."""

    tool: str
    input: dict[str, Any]
    ok: bool
    result: Any = None
    error: Optional[str] = None


class AiCommandResponse(BaseModel):
    """The model's reply plus a record of every robot action it took."""

    reply: str
    actions: list[AiActionRecord]
    model: str
    stop_reason: str
    timestamp: str


class AiKeySetRequest(BaseModel):
    """Payload to store a user-supplied Anthropic API key."""

    api_key: str = Field(..., min_length=1, max_length=512)


class AiKeyStatusResponse(BaseModel):
    """Whether an AI key is configured and where it comes from — never the key itself."""

    configured: bool
    source: Optional[str] = Field(default=None, description='"stored", "env", or null')
    timestamp: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_ai_router() -> APIRouter:
    """Build the AI command router.

    The :class:`~nomothetic.ai_command.AiCommandService` and
    :class:`~nomothetic.ai_command.AiKeyStore` are read from ``app.state`` at
    call time so test fixtures can inject fakes.

    Returns
    -------
    APIRouter
        Router with ``/api/ai/key`` (GET/PUT/DELETE) and ``/api/ai/command``.
    """
    router = APIRouter(prefix="/api/ai", tags=["AI"])

    def _key_store(request: Request) -> AiKeyStore:
        store: Optional[AiKeyStore] = getattr(request.app.state, "ai_key_store", None)
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI key store not configured",
            )
        return store

    def _service(request: Request) -> AiCommandService:
        service: Optional[AiCommandService] = getattr(request.app.state, "ai_service", None)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI command service not configured",
            )
        return service

    def _key_status(store: AiKeyStore) -> AiKeyStatusResponse:
        source = store.source()
        return AiKeyStatusResponse(
            configured=source is not None,
            source=source,
            timestamp=_now(),
        )

    @router.get("/key", response_model=AiKeyStatusResponse)
    async def get_key_status(request: Request):
        """Report whether an Anthropic API key is configured (and its source)."""
        store = _key_store(request)
        return await asyncio.to_thread(_key_status, store)

    @router.put("/key", response_model=AiKeyStatusResponse)
    async def set_key(body: AiKeySetRequest, request: Request):
        """Store a user-supplied Anthropic API key in the device's 0600 key file.

        Raises
        ------
        HTTPException
            400 if the key does not look like an Anthropic API key,
            503 if it could not be persisted.
        """
        store = _key_store(request)
        try:
            key = validate_api_key_format(body.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        try:
            await asyncio.to_thread(store.save, key)
        except AiKeyStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return await asyncio.to_thread(_key_status, store)

    @router.delete("/key", response_model=AiKeyStatusResponse)
    async def clear_key(request: Request):
        """Remove the stored API key (the env fallback, if any, remains).

        Raises
        ------
        HTTPException
            503 if the key file exists but could not be removed.
        """
        store = _key_store(request)
        try:
            await asyncio.to_thread(store.clear)
        except AiKeyStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return await asyncio.to_thread(_key_status, store)

    @router.post(
        "/command",
        response_model=AiCommandResponse,
        dependencies=[Depends(ai_rate_limit)],
    )
    async def run_ai_command(body: AiCommandRequest, request: Request):
        """Run one chat command through the Claude robot-tool loop.

        Raises
        ------
        HTTPException
            503 if no API key is configured or AI support is not installed,
            502 if the AI provider rejects the key or the request fails,
            429 when rate limited.
        """
        service = _service(request)
        store = _key_store(request)
        resolved = await asyncio.to_thread(store.resolve)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No AI API key configured. Store one via PUT /api/ai/key.",
            )
        api_key, _source = resolved
        try:
            result = await service.run_command(
                [message.model_dump() for message in body.messages],
                api_key=api_key,
            )
        except AiUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except AiProviderAuthError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except AiProviderError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        logger.info(
            "AI command completed: %d action(s), stop_reason=%s",
            len(result["actions"]),
            result["stop_reason"],
        )
        return AiCommandResponse(timestamp=_now(), **result)

    return router
