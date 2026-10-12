@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    echo Usage: release.bat v1.2.3 [--dry-run]
    exit /b 2
)
python tools\release.py %* --publish
exit /b %errorlevel%
