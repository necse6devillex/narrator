"""Narrator on this PC: the same reading app, with the natural Kokoro voices made by the graphics card.

Run with start.bat. Open http://127.0.0.1:8011 . Nothing here goes over the internet.
"""
import hashlib
import os
import threading
from pathlib import Path

import lameenc
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from kokoro_onnx import Kokoro
from pydantic import BaseModel

HERE = Path(__file__).parent
WEB_DIR = HERE.parent / "server" / "web"
MODEL_PATH = os.environ.get("KOKORO_MODEL", str(HERE / "models" / "kokoro-v1.0.onnx"))
VOICES_PATH = os.environ.get("KOKORO_VOICES", str(HERE / "models" / "voices-v1.0.bin"))
PARALLEL = int(os.environ.get("KOKORO_PARALLEL", "2"))

VOICES = {
    "af_heart": ("Heart — female, warm (US)", "en-us"),
    "af_bella": ("Bella — female, expressive (US)", "en-us"),
    "af_nicole": ("Nicole — female, soft (US)", "en-us"),
    "af_aoede": ("Aoede — female, bright (US)", "en-us"),
    "af_kore": ("Kore — female, clear (US)", "en-us"),
    "af_sarah": ("Sarah — female, calm (US)", "en-us"),
    "bf_emma": ("Emma — female (UK)", "en-gb"),
    "am_michael": ("Michael — male, clear (US)", "en-us"),
    "am_fenrir": ("Fenrir — male, deep (US)", "en-us"),
    "am_puck": ("Puck — male, lively (US)", "en-us"),
    "bm_george": ("George — male (UK)", "en-gb"),
    "bm_fable": ("Fable — male, storyteller (UK)", "en-gb"),
}

CACHE_MAX = 600
_cache = {}
_order = []
_gate = threading.Semaphore(PARALLEL)


def make_session():
    providers = ["CPUExecutionProvider"]
    try:
        ort.preload_dlls()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except Exception as e:
        print("Graphics libraries not found, using the processor instead (slower):", e)
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    opts.intra_op_num_threads = max(2, (os.cpu_count() or 4))
    sess = ort.InferenceSession(MODEL_PATH, sess_options=opts, providers=providers)
    return sess


session = make_session()
using_gpu = "CUDAExecutionProvider" in session.get_providers()
kokoro = Kokoro.from_session(session, VOICES_PATH)
print("Voice engine ready. Using the graphics card:", using_gpu, flush=True)

app = FastAPI()


class SpeakRequest(BaseModel):
    text: str
    voice: str


def to_mp3(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    enc = lameenc.Encoder()
    enc.set_bit_rate(48)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(1)
    enc.set_quality(2)
    return bytes(enc.encode(pcm.tobytes()) + enc.flush())


def build_id() -> str:
    h = hashlib.sha1()
    for name in ("index.html", "extract.js", "sw.js"):
        h.update((WEB_DIR / name).read_bytes())
    return h.hexdigest()[:10]


@app.get("/api/health")
def health():
    return {"ready": True, "neural": "ok", "reason": "", "retry_after": 0, "route": "local-gpu" if using_gpu else "local-cpu"}


@app.get("/api/voices")
def voices():
    return [{"id": k, "label": v[0]} for k, v in VOICES.items()]


@app.get("/api/version")
def version():
    return JSONResponse({"build": build_id()}, headers={"Cache-Control": "no-store"})


@app.post("/api/speak")
def speak(req: SpeakRequest):
    if req.voice not in VOICES:
        raise HTTPException(400, "Unknown voice")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Empty text")
    key = (req.voice, text)
    hit = _cache.get(key)
    if hit is not None:
        return Response(content=hit, media_type="audio/mpeg")
    with _gate:
        samples, sr = kokoro.create(text, voice=req.voice, speed=1.0, lang=VOICES[req.voice][1])
    audio = to_mp3(samples, sr)
    if key not in _cache and len(_order) >= CACHE_MAX:
        _cache.pop(_order.pop(0), None)
    _cache[key] = audio
    _order.append(key)
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/", response_class=HTMLResponse)
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__BUILD__", build_id()).replace("__LOCAL_KOKORO__", "1")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/sw.js")
def sw():
    return Response(
        "self.addEventListener('install',()=>self.skipWaiting());"
        "self.addEventListener('activate',e=>e.waitUntil(self.registration.unregister()));",
        media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/extract.js")
def extract_js():
    return FileResponse(WEB_DIR / "extract.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/icon-192.png")
def icon192():
    return FileResponse(WEB_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(WEB_DIR / "icon-512.png", media_type="image/png")


app.mount("/vendor", StaticFiles(directory=WEB_DIR / "vendor"), name="vendor")
