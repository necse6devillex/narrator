import io
import os
import threading
import time
from pathlib import Path

import lameenc
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from kokoro_onnx import Kokoro
from pydantic import BaseModel

BASE = Path(__file__).parent
MODEL_PATH = os.environ.get("KOKORO_MODEL", str(BASE / "models" / "kokoro-v1.0.int8.onnx"))
VOICES_PATH = os.environ.get("KOKORO_VOICES", str(BASE / "models" / "voices-v1.0.bin"))
THREADS = int(os.environ.get("KOKORO_THREADS", "2"))
MAX_PARALLEL = int(os.environ.get("KOKORO_PARALLEL", "1"))

VOICES = {
    "af_heart": ("Heart — female, warm (US)", "en-us"),
    "af_bella": ("Bella — female, expressive (US)", "en-us"),
    "af_nicole": ("Nicole — female, soft (US)", "en-us"),
    "af_sarah": ("Sarah — female, calm (US)", "en-us"),
    "am_michael": ("Michael — male, clear (US)", "en-us"),
    "am_fenrir": ("Fenrir — male, deep (US)", "en-us"),
    "bf_emma": ("Emma — female (UK)", "en-gb"),
    "bm_george": ("George — male (UK)", "en-gb"),
    "bm_fable": ("Fable — male, storyteller (UK)", "en-gb"),
}

CACHE_MAX = 400
_cache = {}
_cache_order = []
_gate = threading.Semaphore(MAX_PARALLEL)

opts = ort.SessionOptions()
opts.intra_op_num_threads = THREADS
session = ort.InferenceSession(MODEL_PATH, sess_options=opts, providers=["CPUExecutionProvider"])
kokoro = Kokoro.from_session(session, VOICES_PATH)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], max_age=86400)


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


@app.get("/api/health")
def health():
    return {"ready": True, "neural": "ok", "engine": "kokoro"}


@app.get("/api/voices")
def voices():
    return [{"id": k, "label": v[0]} for k, v in VOICES.items()]


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
    if key not in _cache and len(_cache_order) >= CACHE_MAX:
        _cache.pop(_cache_order.pop(0), None)
    _cache[key] = audio
    _cache_order.append(key)
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/")
def root():
    return JSONResponse({"name": "Narrator voice server", "engine": "kokoro", "voices": len(VOICES)})
