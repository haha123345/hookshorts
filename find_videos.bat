@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [준비] 프로그램 확인 중...
python -m pip install -q --disable-pip-version-check --no-warn-script-location pillow edge-tts gtts google-genai requests google-api-python-client google-auth-oauthlib
python -m pip install -q -U --disable-pip-version-check --no-warn-script-location yt-dlp edge-tts
python finder.py
pause
