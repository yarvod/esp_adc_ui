@echo off
setlocal
cd /d "%~dp0"
python tools\build_windows.py %*
exit /b %errorlevel%
