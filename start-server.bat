@echo off
chcp 65001 > nul
cd /d "%~dp0"
python serve.py
if errorlevel 1 py serve.py
pause
