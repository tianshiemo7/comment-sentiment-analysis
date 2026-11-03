@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set ACTIVE_MODEL=bilstm_attention
set INFERENCE_DEVICE=cpu

rem 优先使用项目内虚拟环境；未创建 .venv 时回退系统 python。
rem （旧版本硬编码 .venv\Scripts\python.exe，仓库里没有 .venv 时双击会立刻退出。）
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import uvicorn, fastapi" >nul 2>&1
if errorlevel 1 (
  echo [start-backend] 当前解释器缺少 uvicorn / fastapi：
  echo                 %PY%
  echo [start-backend] 请先安装依赖：
  echo                 python -m pip install -r requirements.txt
  echo [start-backend] 或创建虚拟环境：
  echo                 python -m venv .venv
  echo                 .venv\Scripts\activate
  echo                 pip install -r requirements.txt
  pause
  exit /b 1
)

echo [start-backend] 使用解释器: %PY%
echo [start-backend] 服务地址: http://127.0.0.1:8000/docs
"%PY%" -m uvicorn src.inference.app:app --host 127.0.0.1 --port 8000
pause
