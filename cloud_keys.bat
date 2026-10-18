@echo off
chcp 65001 >nul
cd /d "%~dp0"
python cloud_keys.py
start notepad cloud_keys.txt
pause
