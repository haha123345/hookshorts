@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -q --disable-pip-version-check --no-warn-script-location pillow edge-tts gtts
python compose.py %*
pause
