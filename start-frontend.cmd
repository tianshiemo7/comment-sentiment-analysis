@echo off
cd /d "%~dp0frontend"
set VITE_USE_MOCK=0
call npm.cmd run dev -- --host 127.0.0.1
pause
