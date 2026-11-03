@echo off
setlocal
cd /d "%~dp0frontend"
set VITE_USE_MOCK=0

rem 首次运行时自动安装前端依赖（node_modules 不存在时）
if not exist "node_modules" (
  echo [start-frontend] 首次运行，安装前端依赖...
  call npm.cmd install
  if errorlevel 1 (
    echo [start-frontend] npm install 失败，请确认已安装 Node.js 18+ 且网络可用。
    pause
    exit /b 1
  )
)

echo [start-frontend] 开发服务器: http://localhost:3000
call npm.cmd run dev -- --host 127.0.0.1
pause
