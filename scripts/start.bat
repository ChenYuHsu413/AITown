@echo off
rem Start the town server as an independent process (survives closing this window).
rem Usage: double-click, or run "scripts\start.bat" from the repo.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
python scripts\server_ctl.py start
if "%~1"=="" pause
