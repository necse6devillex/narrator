import asyncio
import io
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse
from pydantic import BaseModel

VOICES = {
    "AriaNeural": {"id": "en-US-AriaNeural", "label": "Aria — expressive (US)"},
    "JennyNeural": {"id": "en-US-JennyNeural", "label": "Jenny — warm, friendly (US)"},
    "GuyNeural": {"id": "en-US-GuyNeural", "label": "Guy — clear (US)"},
    "SoniaNeural": {"id": "en-GB-SoniaNeural", "label": "Sonia — (British)"},
    "RyanNeural": {"id": "en-GB-RyanNeural", "label": "Ryan — (British)"},
    "NatashaNeural": {"id": "en-AU-NatashaNeural", "label": "Natasha — (Australian)"},
}

CACHE_MAX = 400
_cache: dict[tuple[str, str], bytes] = {}
_cache_order: list[tuple[str, str]] = []

app = FastAPI()


class SpeakRequest(BaseModel):
    text: str
    voice: str


@app.get("/api/health")
def health():
    return {"ready": True}


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

    audio_bytes = b""
    last_error = None
    for attempt in range(5):
        try:
            communicate = edge_tts.Communicate(text, VOICES[req.voice]["id"])
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            audio_bytes = buf.getvalue()
            if audio_bytes:
                break
        except Exception as e:
            last_error = e
        if attempt < 4:
            await asyncio.sleep(0.5 * (attempt + 1))
    if not audio_bytes:
        raise HTTPException(502, f"Voice service unavailable: {last_error}")

    if key not in _cache and len(_cache_order) >= CACHE_MAX:
        oldest = _cache_order.pop(0)
        _cache.pop(oldest, None)
    _cache[key] = audio_bytes
    _cache_order.append(key)

    return Response(content=audio_bytes, media_type="audio/mpeg")


WEB_DIR = Path(__file__).parent / "web"


@app.get("/", response_class=HTMLResponse)
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/icon-192.png")
def icon192():
    return FileResponse(WEB_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(WEB_DIR / "icon-512.png", media_type="image/png")
