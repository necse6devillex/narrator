import asyncio
import io
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
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


@app.get("/api/diag")
async def diag():
    import datetime, platform, time as _t
    import aiohttp
    from edge_tts.drm import DRM
    out = {"python": platform.python_version(), "edge_tts": getattr(edge_tts, "__version__", "?"),
           "aiohttp": aiohttp.__version__, "server_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org?format=json", timeout=aiohttp.ClientTimeout(total=8)) as r:
                out["egress_ip"] = await r.text()
            async with s.get("https://speech.platform.bing.com/consumer/speech/synthesize/readaloud/voices/list?trustedclienttoken=6A5AA1D4EAFF4E9FB37E23D68491D6F4",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                out["voices_list_status"] = r.status
                out["ms_date_header"] = r.headers.get("Date")
    except Exception as e:
        out["net_error"] = str(e)[:200]
    results = []
    for i in range(8):
        t0 = _t.time()
        try:
            n = 0
            async for ch in edge_tts.Communicate(f"Diagnostic sentence number {i}, hello there.", "en-US-JennyNeural").stream():
                if ch["type"] == "audio":
                    n += len(ch["data"])
            results.append({"ok": n > 0, "bytes": n, "secs": round(_t.time() - t0, 2)})
        except Exception as e:
            results.append({"ok": False, "err": str(e)[:90], "secs": round(_t.time() - t0, 2)})
    out["attempts"] = results
    out["skew_seconds"] = DRM.clock_skew_seconds
    return out


WEB_DIR = Path(__file__).parent / "web"


@app.get("/", response_class=HTMLResponse)
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
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
