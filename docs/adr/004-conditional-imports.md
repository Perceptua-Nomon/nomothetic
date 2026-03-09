# ADR-004: Conditional Imports for Linux-Only Dependencies

**Status:** Accepted  
**Date:** 2024-01-20  
**Deciders:** Perceptua  

---

## Context

Several dependencies (`picamera2`, `spidev`, `pigpio`) only work on Raspberry Pi / Linux. The package must remain importable on Windows and macOS for development and testing. Options evaluated:

1. **`try/except ImportError` at module level, set symbol to `None`** — module is always importable; error raised at instantiation
2. **Platform guard at import time** (`if sys.platform == "linux"`)  — imports unconditionally fail on Windows
3. **Separate subpackages** (`nomothetic.pi.camera`) — adds complexity, breaks simple imports
4. **Stub/mock fallback implementations** — maintains API but masks real hardware errors

## Decision

Use **`try/except ImportError` at module level**, setting unavailable symbols to `None`. Raise `RuntimeError` at class instantiation if the symbol is `None`.

```python
try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None  # type: ignore

class Camera:
    def __init__(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("picamera2 not available — requires Raspberry Pi")
```

## Rationale

- `import nomothetic` and `import nomothetic.camera` succeed on all platforms — test files can import the module without hardware
- Tests mock the symbols at the point where the module imported them: `@patch("nomothetic.camera.Picamera2")`
- The error is raised at the right time (instantiation) with a clear message
- `pyproject.toml` puts `picamera2` and `spidev` in the `[pi]` optional dependency group (`pip install "nomothetic[pi]"`) — they are never installed unless explicitly requested

## Trade-offs

- mypy may flag `None` comparisons; suppressed with `# type: ignore` only on the `= None` assignment line
- Developers must remember to keep the `None` check at the top of `__init__` for every hardware-dependent class

## Consequences

- All tests run on Windows/macOS without hardware
- `make test` is the canonical test command and must always pass on the development machine
- Any new hardware-dependent module must follow this exact pattern
