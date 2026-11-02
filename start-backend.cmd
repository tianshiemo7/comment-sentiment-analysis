@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set ACTIVE_MODEL=bilstm_attention
set INFERENCE_DEVICE=cpu
".venv\Scripts\python.exe" -m uvicorn src.inference.app:app --host 127.0.0.1 --port 8000
pause
