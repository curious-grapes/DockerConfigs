# ESP32-CAM Proxy

A lightweight Python proxy that sits between your ESP32-CAM and the rest of your network. It maintains a **single upstream MJPEG connection** to the hardware, fans the stream out to **unlimited simultaneous clients**, and **automatically disconnects from the camera when nobody is watching** to reduce its load.

---

## Why this exists

The ESP32-CAM firmware (`CameraWebServer`) only supports **one concurrent stream client**. A second browser tab or app trying to connect will get rejected or freeze the first viewer. This proxy solves that by acting as the sole consumer of the camera stream and re-broadcasting it to as many clients as needed.

---

## Quick start

**1. Clone / copy the three files into a directory.**

**2. Edit `docker-compose.yml`** — set your camera's IP:

```yaml
ESP32_HOST: "192.168.8.9"
```

**3. Build and run:**

```bash
docker compose up --build
```

**4. Open the stream in any browser or player:**

```
http://<your-server>:8034/stream
```

---

## Endpoints

| Endpoint | Description |
|---|---|
| `/stream` | MJPEG live stream — open in a browser, VLC, or any MJPEG-capable client |
| `/snapshot` | Single JPEG frame — returns the latest cached frame instantly, or fetches one directly from the camera |
| `/health` | JSON status: client count, upstream connection state, configured host |

### Example snapshot usage

```bash
# Save a snapshot to disk
curl http://localhost:8034/snapshot -o snapshot.jpg

# Display in browser
http://localhost:8034/snapshot
```

### Health check response

```json
{
  "status": "ok",
  "clients": 3,
  "upstream_active": true,
  "esp32_host": "192.168.8.9"
}
```

---

## Configuration

All settings are environment variables in `docker-compose.yml`. No code changes needed.

### Network

| Variable | Default | Description |
|---|---|---|
| `ESP32_HOST` | `192.168.8.9` | IP address or hostname of the ESP32-CAM |
| `ESP32_PORT_CAM` | `80` | Camera control + snapshot port |
| `ESP32_PORT_STREAM` | `81` | MJPEG stream port |
| `PROXY_PORT` | `8034` | Port this proxy listens on |

### Camera settings

These are pushed to the ESP32-CAM via its `/control` endpoint on every container start.

| Variable | Default | Description |
|---|---|---|
| `RESOLUTION` | `VGA` | See resolution table below |
| `QUALITY` | `10` | JPEG quality: `0`–`63` (lower = higher quality, larger file) |
| `BRIGHTNESS` | `0` | `-2` to `+2` |
| `SATURATION` | `0` | `-2` to `+2` |
| `CONTRAST` | `0` | `-2` to `+2` |
| `VFLIP` | `0` | Vertical flip: `0` = off, `1` = on |
| `HMIRROR` | `0` | Horizontal mirror: `0` = off, `1` = on |

### Resolution options

Both named strings and raw numeric values are accepted.

| Name | Value | Pixels |
|---|---|---|
| `96x96` | `0` | 96 × 96 |
| `QQVGA` | `1` | 160 × 120 |
| `QCIF` | `2` | 176 × 144 |
| `HQVGA` | `3` | 240 × 176 |
| `240x240` | `4` | 240 × 240 |
| `QVGA` | `5` | 320 × 240 |
| `CIF` | `6` | 400 × 296 |
| `HVGA` | `7` | 480 × 320 |
| **`VGA`** | **`8`** | **640 × 480** ← default |
| `SVGA` | `9` | 800 × 600 |
| `XGA` | `10` | 1024 × 768 |
| `HD` | `11` | 1280 × 720 |
| `SXGA` | `12` | 1280 × 1024 |
| `UXGA` | `13` | 1600 × 1200 |

---

## How it works

### Stream fan-out (`StreamHub`)

- The first client to hit `/stream` triggers an upstream connection to `http://ESP32_HOST:81/stream`.
- Every subsequent client just subscribes to the same in-progress stream — no additional load on the camera.
- Each client gets its own `asyncio.Queue` (capacity 2). If a client is too slow to consume frames, the oldest frame in its queue is dropped rather than blocking the broadcast loop.
- When the last client disconnects, the upstream connection is closed. The ESP32-CAM is no longer being polled or streamed to.
- The next client to connect will re-establish the upstream connection transparently.

### Snapshots

`/snapshot` returns the most recently received frame from the cache — it responds instantly even if no stream is currently active. If no cached frame exists yet (fresh start, no prior stream), it fetches one directly from `http://ESP32_HOST:80/capture`.

### Camera initialisation

On container startup, the proxy sends each configured setting to `http://ESP32_HOST:80/control?var=<name>&val=<value>`. If the camera is unreachable (still booting), it retries up to 5 times with a 3-second delay between attempts before giving up and continuing anyway.

---

## Dependencies

- Python 3.12
- [`aiohttp`](https://docs.aiohttp.org/) — async HTTP client/server (the only runtime dependency)

The Docker image is based on `python:3.12-slim` and weighs in under 100 MB.