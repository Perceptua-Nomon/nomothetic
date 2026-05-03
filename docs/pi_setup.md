# Raspberry Pi Setup

End-to-end guide for building and deploying the `nomopractic` Rust daemon
and installing `nomothetic` on the Raspberry Pi.

## Prerequisites

### Hardware

- **Raspberry Pi Zero 2W** running Debian (bookworm/trixie)
- **SunFounder Robot HAT V4** attached (I2C bus 1, address `0x14`)

### Software — on the Pi

- **Python ≥ 3.9**
- **uv** — fast Python package manager (replaces pip/venv):
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Rust toolchain** — install with `rustup` (see [Installing Rust on the Pi](#installing-rust-on-the-pi) below)
- **BlueZ** — Bluetooth stack for BLE GATT server (usually pre-installed on
  Raspberry Pi OS). Required if BLE is enabled in nomopractic:
  ```bash
  # Verify BlueZ is installed
  bluetoothctl --version

  # If not installed:
  sudo apt install -y bluez

  # Enable and start the Bluetooth service
  sudo systemctl enable --now bluetooth

  # Verify the controller is powered on
  bluetoothctl show | grep Powered
  # → Powered: yes
  ```
  **Note:** On Pi Zero 2W, WiFi and BLE share the BCM43436s antenna.
  Simultaneous WiFi + BLE is supported but may reduce range for both.
- **Both repos cloned**: `Perceptua-Nomon/nomothetic` and `Perceptua-Nomon/nomopractic`

### Software — on your dev machine (optional, for cross-compilation)

- **uv** — fast Python package manager:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # or: brew install uv / pip install uv / winget install astral-sh.uv
  ```
- Rust toolchain with the aarch64 target: `rustup target add aarch64-unknown-linux-gnu`
- [`cross`](https://github.com/cross-rs/cross) for Docker-based cross-compilation: `cargo install cross`

---

## Installing Rust on the Pi

The Pi Zero 2W has only 512 MB of RAM. The Rust compiler regularly exceeds
this during linking, so you must configure swap space before installing.

### Set up swap with rpi-swap

Raspberry Pi OS ships with
[rpi-swap](https://github.com/raspberrypi/rpi-swap), which manages swap
configuration through drop-in files at `/etc/rpi/swap.conf.d/`. Create a
drop-in that allocates a fixed 2 GB swapfile:

```bash
sudo mkdir -p /etc/rpi/swap.conf.d/

sudo tee /etc/rpi/swap.conf.d/80-rust-build.conf > /dev/null <<EOF
[Main]
Mechanism=swapfile

[File]
FixedSizeMiB=2048
EOF

sudo reboot
```

After rebooting, verify swap is active:

```bash
free -h
#               total        used        free      shared  buff/cache   available
# Mem:          432Mi       ...
# Swap:         2.0Gi       ...
```

### Install Rust via rustup

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustc --version
```

Accept the default installation (option 1) when prompted.

### Clean up swap (optional)

Once Rust is installed and you no longer need the extra swap for compilation,
remove the drop-in and reboot to restore the default swap configuration:

```bash
sudo rm /etc/rpi/swap.conf.d/80-rust-build.conf
sudo reboot
```

If you plan to compile Rust code directly on the Pi regularly (rather than
cross-compiling), keep the swap drop-in in place.

---

## 1 — Build & Deploy nomopractic

### Cross-compile from your dev machine

```bash
cd nomopractic/

# Install cross (one-time)
cargo install cross

# Build the release binary for aarch64
make release          # runs: cross build --target aarch64-unknown-linux-gnu --release
```

The binary lands at
`target/aarch64-unknown-linux-gnu/release/nomopractic`.

### Copy to the Pi

```bash
PI=nomon@<pi-hostname>

scp target/aarch64-unknown-linux-gnu/release/nomopractic  $PI:/tmp/nomopractic
scp config.toml                                            $PI:/tmp/nomopractic.config.toml
scp systemd/nomopractic.service                            $PI:/tmp/nomopractic.service

ssh $PI 'sudo mv /tmp/nomopractic /usr/local/bin/ && \
         sudo mkdir -p /etc/nomopractic && \
         sudo mv /tmp/nomopractic.config.toml /etc/nomopractic/config.toml && \
         sudo mv /tmp/nomopractic.service /etc/systemd/system/'
```

### Create the runtime group and socket directory

```bash
# On the Pi
sudo groupadd -f nomon
sudo usermod -aG nomon $USER          # allow your user to connect
sudo mkdir -p /run/nomopractic
sudo chown root:nomon /run/nomopractic
```

### Start the daemon

**Option A — systemd (recommended for production):**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nomopractic
sudo systemctl status nomopractic
```

**Option B — foreground (useful for debugging):**

```bash
sudo nomopractic --config /etc/nomopractic/config.toml
```

You should see:

```
INFO nomopractic::ipc: IPC listener started path="/run/nomopractic/nomopractic.sock"
```

---

## 2 — Verify with socat

Before writing Python code, confirm the socket is alive:

```bash
# Install socat if not already present
sudo apt install -y socat

echo '{"id":"1","method":"health","params":{}}' \
  | socat - UNIX-CONNECT:/run/nomopractic/nomopractic.sock
```

Expected response:

```json
{"id":"1","ok":true,"result":{"schema_version":"1.0.0","status":"ok","version":"0.1.0","hat_address":"0x14","i2c_bus":1,"uptime_s":5}}
```

---

## 3 — Install nomothetic on the Pi

`picamera2` transitively depends on `python-prctl`, which requires the
`libcap` development headers to build from source. Install them first:

```bash
sudo apt install -y libcap-dev
```

Then install the package:

```bash
cd nomothetic/
uv sync --extra pi
```

To install additional extras (e.g. REST API and telemetry):

```bash
uv sync --extra pi --extra api --extra telemetry
```

---

## 4 — Talk to the daemon from Python

### Health check

```python
from nomothetic.hat import HatClient

with HatClient() as hat:
    result = hat.health()
    print(f"Daemon v{result.version}, up {result.uptime_s}s")
```

The client defaults to `/run/nomopractic/nomopractic.sock`. Override with:

```python
hat = HatClient(socket_path="/tmp/nomopractic.sock", timeout_s=5.0)
```

Or set the `NOMON_HAT_SOCKET_PATH` environment variable.

### Read battery voltage

```python
with HatClient() as hat:
    voltage = hat.get_battery_voltage()
    print(f"Battery: {voltage:.2f} V")
```

### Move a servo

```python
with HatClient() as hat:
    # Set servo on PWM channel 0 to 90°
    hat.set_servo_angle(channel=0, angle_deg=90.0, ttl_ms=1000)

    # Or set a raw pulse width
    hat.set_servo_pulse_us(channel=0, pulse_us=1500, ttl_ms=1000)
```

The `ttl_ms` parameter is a safety lease — if the Python process crashes or
stops sending commands, the daemon automatically idles the servo after the TTL
expires. For continuous motion, refresh the command in a loop faster than the
TTL interval.

### Reset the HAT microcontroller

```python
with HatClient() as hat:
    hat.reset_mcu()
```

---

## 5 — Raw socket (without HatClient)

If `nomothetic.hat` is not yet installed or you want to test the protocol
directly from Python:

```python
from __future__ import annotations

import json
import socket
from typing import Any, Optional

SOCK_PATH = "/run/nomopractic/nomopractic.sock"

def send_request(method: str, params: Optional[dict[str, Any]] = None, req_id: str = "1") -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(SOCK_PATH)
        request = json.dumps({"id": req_id, "method": method, "params": params or {}})
        s.sendall((request + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data)

# Health check
resp = send_request("health")
print(resp)
# {"id": "1", "ok": true, "result": {"schema_version": "1.0.0", ...}}

# Battery voltage (Phase 2+)
resp = send_request("get_battery_voltage", req_id="2")
print(f"Battery: {resp['result']['voltage_v']:.2f} V")

# Servo angle (Phase 3+)
resp = send_request("set_servo_angle", {"channel": 0, "angle_deg": 45.0, "ttl_ms": 500}, req_id="3")
print(resp)
```

---

## 6 — Configuration reference

### nomopractic config (`/etc/nomopractic/config.toml`)

```toml
i2c_bus = 1
hat_address = 0x14
socket_path = "/run/nomopractic/nomopractic.sock"
socket_mode = 432        # 0o660 in decimal
log_level = "info"
servo_default_ttl_ms = 500
watchdog_poll_ms = 100
```

Every field can be overridden with an environment variable:

| Variable | Example |
|----------|---------|
| `NOMON_HAT_I2C_BUS` | `1` |
| `NOMON_HAT_ADDRESS` | `0x14` |
| `NOMON_HAT_SOCKET_PATH` | `/tmp/nomopractic.sock` |
| `NOMON_HAT_SOCKET_MODE` | `0660` |
| `NOMON_HAT_LOG_LEVEL` | `debug` |
| `NOMON_HAT_SERVO_DEFAULT_TTL_MS` | `1000` |
| `NOMON_HAT_WATCHDOG_POLL_MS` | `50` |

### Verbose logging

```bash
RUST_LOG=debug nomopractic --config /etc/nomopractic/config.toml
```

---

## 7 — Development without hardware

For local development and testing without a Raspberry Pi or HAT, use a
temporary socket path:

```bash
# Terminal 1 — start the daemon (I2C calls will fail, but health works)
NOMON_HAT_SOCKET_PATH=/tmp/nomopractic.sock cargo run -- --config config.toml

# Terminal 2 — send a health check
echo '{"id":"1","method":"health","params":{}}' \
  | socat - UNIX-CONNECT:/tmp/nomopractic.sock
```

On a machine without I2C hardware, the daemon will start and respond to
`health` requests. Methods that access I2C (`get_battery_voltage`,
`set_servo_*`, `reset_mcu`) will return `HARDWARE_ERROR` responses — this is
expected and useful for testing the IPC layer in isolation.

Run the integration test suite (no hardware needed):

```bash
cd nomopractic/
cargo test
```

---

## 8 — BLE Pairing (Optional)

BLE pairing allows the nomotactic mobile app to pair with the robot without
an existing WiFi connection. The robot uses OS-level Bluetooth passkey pairing
(ADR-004) — no custom secret exchange at the application layer.

> **Note:** BLE and WiFi share the BCM43436s antenna on Pi Zero 2W.
> Simultaneous operation is supported but may reduce range for both.

### How it works

1. nomopractic reads the 6-digit numeric passkey from
   `/var/lib/nomon/pairing_secret` at startup and prints it in the startup log.
2. The mobile user selects the device from a BLE scan. The OS shows a native
   Bluetooth passkey dialog — the user enters the 6-digit code.
3. The OS completes bonding with link-layer encryption (BlueZ, no custom secret
   exchange at the application layer).
4. nomotactic calls the `authenticate` IPC method over BLE → receives a JWT
   → stores it in expo-secure-store for use over HTTPS after WiFi provisioning.

### Pairing secret file

| Property | Value |
|----------|-------|
| Path | `/var/lib/nomon/pairing_secret` (default) |
| Mode | `0640` (`rw-r-----`) |
| Owner | `nomon:nomon` |
| Env override | `NOMON_PAIRING_SECRET_PATH` |

The systemd service (`nomothetic-api.service`) automatically creates
`/var/lib/nomon/` on startup.

### Verify BLE is working

```bash
# Check BlueZ is running
sudo systemctl status bluetooth

# Check the controller is powered on
bluetoothctl show | grep Powered
# → Powered: yes

# Verify the pairing secret file exists
ls -la /var/lib/nomon/pairing_secret
```

### Troubleshooting BLE

| Symptom | Cause | Fix |
|---------|-------|-----|
| BLE not advertising | BlueZ service not running | `sudo systemctl start bluetooth` |
| `Powered: no` in bluetoothctl | Bluetooth disabled in firmware | Add `dtoverlay=disable-bt` is absent from `/boot/config.txt`; reboot |
| BLE passkey rejected | Wrong 6-digit code entered, or passkey file missing | Check `/var/lib/nomon/pairing_secret` contains exactly 6 digits |
| `authenticate` returns INTERNAL_ERROR | `NOMON_JWT_SECRET` env var not set | Set `NOMON_JWT_SECRET` in nomopractic's environment |
| Weak BLE signal | Antenna shared with WiFi | Move app closer to the Pi; reduce WiFi traffic |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | Daemon not running | `sudo systemctl start nomopractic` |
| `Permission denied` on socket | User not in `nomon` group | `sudo usermod -aG nomon $USER` then re-login |
| `No such file or directory` on socket | Socket path doesn't exist or daemon crashed | Check `journalctl -u nomopractic` |
| `HARDWARE_ERROR` on servo/battery | I2C bus not available | Verify HAT connection: `sudo i2cdetect -y 1` should show `0x14` |
| `UNKNOWN_METHOD` response | Method not yet implemented in current phase | Check [roadmap](https://github.com/Perceptua-Nomon/nomopractic/blob/main/docs/roadmap.md) for method availability |
| Servo stops moving after ~500 ms | TTL lease expired (by design) | Increase `ttl_ms` or send commands in a loop |

---

## Further reading

- [getting_started.md](getting_started.md) — Start both servers and interact remotely
- [hat_ipc_schema.md](hat_ipc_schema.md) — Full IPC protocol specification
- [hat_python_client.md](hat_python_client.md) — `HatClient` class interface
- [architecture.md](architecture.md) — System architecture overview
- [pi_hardware.md](pi_hardware.md) — Pi hardware discovery notes
