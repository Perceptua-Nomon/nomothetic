# Getting Started

A newcomer guide for cloning the repos, setting up the environment on a
Raspberry Pi, and starting the nomon servers so you can interact with devices
remotely.

---

## What you need

- **Raspberry Pi Zero 2W** (or Pi 4/5) running Raspberry Pi OS bookworm/trixie
- **SunFounder Robot HAT V4** attached (I2C bus 1, address `0x14`)
- **Camera module** attached (CSI ribbon or USB)
- A workstation with SSH access to the Pi

---

## Step 1 — Clone the repositories

On the Pi:

```bash
git clone https://github.com/Perceptua-Nomon/nomopractic.git
git clone https://github.com/Perceptua-Nomon/nomothetic.git
```

---

## Steps 2–5 — Build, deploy, and install

Follow **[raspberry_pi_setup.md](raspberry_pi_setup.md)** in full:

1. **Install Rust on the Pi** (or cross-compile from your workstation) — Section: *Installing Rust on the Pi*
2. **Build & deploy nomopractic** — Section: *1 — Build & Deploy nomopractic*
3. **Start the nomopractic daemon** — Section: *1 — Build & Deploy nomopractic → Start the daemon*
4. **Install nomothetic on the Pi** — Section: *3 — Install nomothetic on the Pi*

After completing those steps, verify the daemon is alive:

```bash
echo '{"id":"1","method":"health","params":{}}' \
  | socat - UNIX-CONNECT:/run/nomopractic/nomopractic.sock
# → {"id":"1","ok":true,"result":{"status":"ok","version":"...",...}}
```

---

## Step 6 — Start the REST API server

The REST API exposes camera control and HAT endpoints over HTTPS.

On the Pi, create a launcher script or run directly:

```python
from nomothetic.api import APIServer

server = APIServer(
    host="0.0.0.0",   # bind on all interfaces so remote clients can connect
    port=8443,
    use_ssl=True,      # auto-generates a self-signed cert in .certs/
)
server.run()
```

Or run the bundled example:

```bash
cd nomothetic/
python examples/api_server.py
```

The server starts at **`https://<pi-address>:8443`**.

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/api/camera/status` | Camera + recording state |
| `POST` | `/api/camera/capture` | Capture a still image |
| `POST` | `/api/camera/record/start` | Start video recording |
| `POST` | `/api/camera/record/stop` | Stop video recording |
| `GET` | `/api/hat/battery` | Read battery voltage |
| `POST` | `/api/hat/servo` | Set servo angle |
| `POST` | `/api/hat/reset` | Assert MCU reset |

Interactive API docs are available at `https://<pi-address>:8443/docs`.

> **Self-signed certificate**: your browser and curl will warn about the
> certificate. Pass `-k` / `--insecure` to curl, or import the cert from
> `.certs/cert.pem` into your browser's trust store.

---

## Step 7 — Start the MJPEG streaming server

The streaming server serves a live camera feed viewable in any browser.

```python
from nomothetic.streaming import StreamServer

stream = StreamServer(
    host="0.0.0.0",   # bind on all interfaces
    port=8000,
    width=1280,
    height=720,
    fps=30,
)
stream.start()
```

Open **`http://<pi-address>:8000`** in a browser to watch the live feed.

The `/stream` endpoint serves raw MJPEG and can be consumed by any MJPEG
client (VLC, ffmpeg, OpenCV `VideoCapture`, etc.):

```bash
# VLC
vlc http://<pi-address>:8000/stream

# OpenCV
cap = cv2.VideoCapture("http://<pi-address>:8000/stream")
```

---

## Running both servers together

To keep both servers running in the same process, start the streaming server
in a background thread and block on the API server:

```python
from nomothetic.api import APIServer
from nomothetic.streaming import StreamServer

stream = StreamServer(host="0.0.0.0", port=8000)
stream.start_background()

api = APIServer(host="0.0.0.0", port=8443)
api.run()   # blocks until Ctrl+C
```

---

## Quick remote test

Once both servers are running, from your workstation:

```bash
PI=<pi-address>

# Health check
curl -sk https://$PI:8443/ | python3 -m json.tool

# Battery voltage
curl -sk https://$PI:8443/api/hat/battery | python3 -m json.tool

# Capture a still image
curl -sk -X POST https://$PI:8443/api/camera/capture \
  -H 'Content-Type: application/json' \
  -d '{"filename": "test.jpg"}' | python3 -m json.tool

# Live stream in browser
open http://$PI:8000    # macOS; use xdg-open on Linux
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Connection refused` on port 8443 | API server not running; check for Python errors |
| `Connection refused` on port 8000 | Streaming server not running |
| `503 Service Unavailable` from HAT endpoint | nomopractic daemon not running — `sudo systemctl start nomopractic` |
| `HARDWARE_ERROR` from battery/servo | HAT not connected or I2C not detected — `sudo i2cdetect -y 1` should show `0x14` |
| Certificate warning in browser | Expected for self-signed certs — click through or import `.certs/cert.pem` |

For deeper troubleshooting, see [raspberry_pi_setup.md](raspberry_pi_setup.md#troubleshooting).
