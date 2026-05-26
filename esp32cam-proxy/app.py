"""
ESP32-CAM Proxy Server
- Single upstream connection to the ESP32-CAM
- Fans out the MJPEG stream to multiple clients
- Closes upstream when no viewers are connected
- Endpoints: /stream  (MJPEG)  and  /snapshot  (single JPEG)
"""

import asyncio
import logging
import os
import time
from collections import deque

import aiohttp
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment (see docker-compose.yml)
# ---------------------------------------------------------------------------

ESP32_HOST  = os.environ.get("ESP32_HOST", "192.168.8.9")
ESP32_PORT_CAM    = int(os.environ.get("ESP32_PORT_CAM", "80"))   # control + capture
ESP32_PORT_STREAM = int(os.environ.get("ESP32_PORT_STREAM", "81")) # MJPEG stream

PROXY_PORT  = int(os.environ.get("PROXY_PORT", "8034"))

# Camera settings applied once at startup via /control endpoint
CAMERA_SETTINGS = {
    "framesize":  os.environ.get("RESOLUTION",  "8"),      # VGA = 8
    "quality":    os.environ.get("QUALITY",     "10"),
    "brightness": os.environ.get("BRIGHTNESS",  "0"),
    "saturation": os.environ.get("SATURATION",  "0"),
    "contrast":   os.environ.get("CONTRAST",    "0"),
    "vflip":      os.environ.get("VFLIP",       "0"),
    "hmirror":    os.environ.get("HMIRROR",     "0"),
}

# Named resolution → framesize numeric value (matches ESP32-CAM firmware)
RESOLUTION_MAP = {
    "96x96":  "0", "QQVGA": "1", "QCIF":  "2", "HQVGA": "3",
    "240x240":"4", "QVGA": "5",  "CIF":   "6", "HVGA":  "7",
    "VGA":    "8", "SVGA": "9",  "XGA":   "10","HD":    "11",
    "SXGA":   "12","UXGA": "13",
}

BOUNDARY = b"123456789000000000000987654321"

# ---------------------------------------------------------------------------
# Shared streaming state
# ---------------------------------------------------------------------------

class StreamHub:
    """
    Manages a single upstream MJPEG connection to the ESP32-CAM and fans
    the frames out to all connected proxy clients.
    """

    def __init__(self):
        self._clients: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._upstream_task: asyncio.Task | None = None
        self._last_frame: bytes | None = None          # serve immediately on join
        self._last_frame_ct: bytes = b"image/jpeg"

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def add_client(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        async with self._lock:
            self._clients.add(q)
            log.info("Client joined  – total %d", len(self._clients))
            if self._upstream_task is None or self._upstream_task.done():
                self._upstream_task = asyncio.create_task(self._upstream_loop())
        return q

    async def remove_client(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._clients.discard(q)
            log.info("Client left   – total %d", len(self._clients))

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------

    def _distribute(self, frame: bytes, content_type: bytes) -> None:
        self._last_frame = frame
        self._last_frame_ct = content_type
        dead = []
        for q in self._clients:
            try:
                # Drop the oldest frame if the queue is full (slow consumer)
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait((frame, content_type))
            except Exception:
                dead.append(q)
        for q in dead:
            self._clients.discard(q)

    # ------------------------------------------------------------------
    # Upstream MJPEG reader
    # ------------------------------------------------------------------

    async def _upstream_loop(self) -> None:
        url = f"http://{ESP32_HOST}:{ESP32_PORT_STREAM}/stream"
        log.info("Connecting to upstream: %s", url)

        connector = aiohttp.TCPConnector(limit=1)
        timeout   = aiohttp.ClientTimeout(connect=10, sock_read=15)

        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log.error("Upstream returned HTTP %d", resp.status)
                        return

                    ct_header = resp.headers.get("Content-Type", "")
                    # Extract boundary from upstream Content-Type header
                    bnd = BOUNDARY   # fallback
                    for part in ct_header.split(";"):
                        part = part.strip()
                        if part.startswith("boundary="):
                            bnd = part[len("boundary="):].strip().encode()

                    await self._read_mjpeg(resp, bnd)

        except asyncio.CancelledError:
            log.info("Upstream task cancelled")
        except Exception as exc:
            log.warning("Upstream error: %s", exc)
        finally:
            log.info("Upstream connection closed")

    async def _read_mjpeg(self, resp: aiohttp.ClientResponse, boundary: bytes) -> None:
        """
        Parse the raw MJPEG multipart stream, extract frames, and distribute.
        Stops automatically when no clients remain.
        """
        buf = b""
        dash_boundary = b"--" + boundary

        async for chunk in resp.content.iter_any():
            # Check for no viewers; stop upstream to save ESP32 resources
            if not self._clients:
                log.info("No clients – stopping upstream")
                return

            buf += chunk

            # Process all complete parts present in buf
            while True:
                # Locate the start of a part
                start = buf.find(dash_boundary)
                if start == -1:
                    # Keep only the tail (boundary might span two chunks)
                    buf = buf[-(len(dash_boundary) + 1):]
                    break

                # Locate header/body separator
                header_end = buf.find(b"\r\n\r\n", start)
                if header_end == -1:
                    break
                header_end += 4   # include the \r\n\r\n itself

                # Parse headers
                raw_headers = buf[start + len(dash_boundary):header_end].decode(errors="replace")
                content_type = b"image/jpeg"
                content_length = None
                for line in raw_headers.splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "content-type":
                            content_type = v.encode()
                        elif k == "content-length":
                            try:
                                content_length = int(v)
                            except ValueError:
                                pass

                if content_length is not None:
                    body_start = header_end
                    body_end   = body_start + content_length
                    if len(buf) < body_end:
                        break   # need more data
                    frame = buf[body_start:body_end]
                    buf   = buf[body_end:]
                else:
                    # No Content-Length → find next boundary
                    next_b = buf.find(dash_boundary, header_end)
                    if next_b == -1:
                        break
                    frame = buf[header_end:next_b].rstrip(b"\r\n")
                    buf   = buf[next_b:]

                if frame:
                    self._distribute(frame, content_type)

    # ------------------------------------------------------------------
    # Snapshot helper
    # ------------------------------------------------------------------

    async def get_snapshot(self) -> tuple[bytes, bytes]:
        """
        Return the most recent cached frame, or fetch one directly from
        the camera's /capture endpoint.
        """
        if self._last_frame:
            return self._last_frame, self._last_frame_ct

        url = f"http://{ESP32_HOST}:{ESP32_PORT_CAM}/capture"
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.read()
                ct   = resp.headers.get("Content-Type", "image/jpeg").encode()
                return data, ct


# Single shared hub
hub = StreamHub()


# ---------------------------------------------------------------------------
# Camera initialisation
# ---------------------------------------------------------------------------

async def apply_camera_settings(app: web.Application) -> None:
    """Push env-configured settings to the ESP32-CAM on startup."""
    settings = dict(CAMERA_SETTINGS)

    # Resolve named resolution → numeric framesize
    fs = settings.get("framesize", "")
    if fs.upper() in RESOLUTION_MAP:
        settings["framesize"] = RESOLUTION_MAP[fs.upper()]

    url_base = f"http://{ESP32_HOST}:{ESP32_PORT_CAM}/control"
    timeout  = aiohttp.ClientTimeout(total=5)

    # Retry a few times; the camera may need a moment after boot
    for attempt in range(1, 6):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for var, val in settings.items():
                    async with session.get(url_base, params={"var": var, "val": val}) as r:
                        if r.status == 200:
                            log.info("Set %s=%s → OK", var, val)
                        else:
                            log.warning("Set %s=%s → HTTP %d", var, val, r.status)
            log.info("Camera settings applied")
            return
        except Exception as exc:
            log.warning("Attempt %d – could not reach ESP32 (%s). Retrying in 3 s…", attempt, exc)
            await asyncio.sleep(3)

    log.error("Could not apply camera settings after 5 attempts – proceeding anyway")


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def handle_stream(request: web.Request) -> web.StreamResponse:
    """MJPEG proxy endpoint – one persistent response per client."""
    resp = web.StreamResponse()
    resp.content_type = f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}"
    resp.headers["Cache-Control"]               = "no-cache"
    resp.headers["Access-Control-Allow-Origin"] = "*"

    await resp.prepare(request)

    q = await hub.add_client()
    try:
        while True:
            try:
                frame, content_type = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                # Send a keepalive comment to detect dead connections early
                try:
                    await resp.write(b"--" + BOUNDARY + b"\r\n\r\n")
                except Exception:
                    break
                continue

            part = (
                b"--" + BOUNDARY + b"\r\n"
                + b"Content-Type: " + content_type + b"\r\n"
                + b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                + b"\r\n"
                + frame
                + b"\r\n"
            )
            try:
                await resp.write(part)
            except (ConnectionResetError, asyncio.CancelledError, Exception):
                break
    finally:
        await hub.remove_client(q)

    return resp


async def handle_snapshot(request: web.Request) -> web.Response:
    """Return a single JPEG frame."""
    try:
        frame, content_type = await hub.get_snapshot()
        return web.Response(
            body=frame,
            content_type=content_type.decode(),
            headers={
                "Cache-Control":               "no-cache",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as exc:
        log.error("Snapshot failed: %s", exc)
        raise web.HTTPBadGateway(reason=f"Could not reach ESP32-CAM: {exc}")


async def handle_health(request: web.Request) -> web.Response:
    clients = len(hub._clients)
    upstream = hub._upstream_task is not None and not hub._upstream_task.done()
    return web.json_response({
        "status":          "ok",
        "clients":         clients,
        "upstream_active": upstream,
        "esp32_host":      ESP32_HOST,
    })


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(apply_camera_settings)

    app.router.add_get("/stream",   handle_stream)
    app.router.add_get("/snapshot", handle_snapshot)
    app.router.add_get("/health",   handle_health)

    return app


if __name__ == "__main__":
    log.info("Starting ESP32-CAM proxy on port %d", PROXY_PORT)
    log.info("Upstream ESP32-CAM: %s (cam:%d / stream:%d)",
             ESP32_HOST, ESP32_PORT_CAM, ESP32_PORT_STREAM)
    web.run_app(create_app(), port=PROXY_PORT)