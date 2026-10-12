import asyncio
import hashlib
import io
import time
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

VOICES = {
    "AriaNeural": {"id": "en-US-AriaNeural", "label": "Aria — female, expressive (US)"},
    "JennyNeural": {"id": "en-US-JennyNeural", "label": "Jenny — female, warm (US)"},
    "GuyNeural": {"id": "en-US-GuyNeural", "label": "Guy — male, clear (US)"},
    "SoniaNeural": {"id": "en-GB-SoniaNeural", "label": "Sonia — female (UK)"},
    "RyanNeural": {"id": "en-GB-RyanNeural", "label": "Ryan — male (UK)"},
    "NatashaNeural": {"id": "en-AU-NatashaNeural", "label": "Natasha — female (Australia)"},
}

CACHE_MAX = 400
_cache: dict[tuple[str, str], bytes] = {}
_cache_order: list[tuple[str, str]] = []

BLOCK_AFTER = 3
BLOCK_SECONDS = 120


class Upstream:
    """Microsoft's free voice service sometimes refuses this server (HTTP 403).
    Remember that, so the app finds out instantly instead of waiting on retries."""
    state = "unknown"  # "ok" | "blocked" | "unknown"
    fail_streak = 0
    blocked_until = 0.0


UP = Upstream()
GATE = asyncio.Semaphore(2)
PROBE_LOCK = asyncio.Lock()

app = FastAPI()


class SpeakRequest(BaseModel):
    text: str
    voice: str


async def synth(text: str, voice_id: str, attempts: int = 2) -> bytes:
    last = None
    for a in range(attempts):
        try:
            buf = io.BytesIO()
            async for chunk in edge_tts.Communicate(text, voice_id).stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            data = buf.getvalue()
            if data:
                return data
        except Exception as e:
            last = e
        if a + 1 < attempts:
            await asyncio.sleep(0.25)
    raise last or RuntimeError("no audio")


def mark_ok():
    UP.fail_streak = 0
    UP.state = "ok"
    UP.blocked_until = 0.0


def mark_failed():
    UP.fail_streak += 1
    if UP.fail_streak >= BLOCK_AFTER:
        UP.state = "blocked"
        UP.blocked_until = time.time() + BLOCK_SECONDS


async def probe():
    async with PROBE_LOCK:
        if UP.state == "ok" or (UP.state == "blocked" and time.time() < UP.blocked_until):
            return
        try:
            await asyncio.wait_for(synth("Ready.", VOICES["AriaNeural"]["id"], attempts=3), timeout=10)
            mark_ok()
        except Exception:
            UP.fail_streak = max(UP.fail_streak, BLOCK_AFTER)
            UP.state = "blocked"
            UP.blocked_until = time.time() + BLOCK_SECONDS


@app.get("/api/health")
async def health():
    if UP.state == "unknown" or (UP.state == "blocked" and time.time() >= UP.blocked_until):
        await probe()
    retry = max(0, int(UP.blocked_until - time.time())) if UP.state == "blocked" else 0
    return {"ready": True, "neural": UP.state, "retry_after": retry}


@app.get("/api/voices")
def list_voices():
    return [{"id": k, "label": v["label"]} for k, v in VOICES.items()]


@app.post("/api/speak")
async def speak(req: SpeakRequest):
    if req.voice not in VOICES:
        raise HTTPException(400, "Unknown voice")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Empty text")

    key = (req.voice, text)
    cached = _cache.get(key)
    if cached is not None:
        return Response(content=cached, media_type="audio/mpeg")

    now = time.time()
    if UP.state == "blocked" and now < UP.blocked_until:
        raise HTTPException(503, "voices-blocked", headers={"Retry-After": str(int(UP.blocked_until - now) + 1)})

    async with GATE:
        try:
            audio_bytes = await asyncio.wait_for(synth(text, VOICES[req.voice]["id"]), timeout=25)
        except Exception:
            mark_failed()
            raise HTTPException(502, "voice-service-error")
    mark_ok()

    if key not in _cache and len(_cache_order) >= CACHE_MAX:
        oldest = _cache_order.pop(0)
        _cache.pop(oldest, None)
    _cache[key] = audio_bytes
    _cache_order.append(key)

    return Response(content=audio_bytes, media_type="audio/mpeg")


WEB_DIR = Path(__file__).parent / "web"


def build_id() -> str:
    h = hashlib.sha1()
    for name in ("index.html", "extract.js", "sw.js"):
        h.update((WEB_DIR / name).read_bytes())
    return h.hexdigest()[:10]


@app.get("/api/version")
def version():
    return JSONResponse({"build": build_id()}, headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__BUILD__", build_id())
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/extract.js")
def extract_js():
    return FileResponse(WEB_DIR / "extract.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


app.mount("/vendor", StaticFiles(directory=WEB_DIR / "vendor"), name="vendor")


@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/icon-192.png")
def icon192():
    return FileResponse(WEB_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(WEB_DIR / "icon-512.png", media_type="image/png")
