@echo off
rem Standard shutdown: pause, wait for in-flight work and the drained snapshot,
rem confirm the snapshot on disk, then stop the server.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
python scripts\server_ctl.py stop
if "%~1"=="" pause
