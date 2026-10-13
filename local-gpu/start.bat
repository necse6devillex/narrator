@echo off
title Narrator voice maker
cd /d "%~dp0"
set KOKORO_MODEL=D:\Claude\kokoro-space\models\kokoro-v1.0.onnx
set KOKORO_VOICES=D:\Claude\kokoro-space\models\voices-v1.0.bin
echo Starting the voice maker... keep this window open while you use it.
start "" /min cmd /c "timeout /t 8 /nobreak >nul & start http://127.0.0.1:8011"
"D:\Claude\kokoro-gpu\venv\Scripts\python.exe" -m uvicorn local_app:app --host 127.0.0.1 --port 8011
pause
