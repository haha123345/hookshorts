@echo off
chcp 65001 >nul
title hookshorts 리모컨 (이 창을 닫으면 꺼져요)
cd /d "%~dp0"
echo [준비] 프로그램 확인 중...
python -m pip install -q --disable-pip-version-check --no-warn-script-location pillow edge-tts gtts google-genai requests google-api-python-client google-auth-oauthlib
python -m pip install -q -U --disable-pip-version-check --no-warn-script-location yt-dlp edge-tts
:loop
python finder.py --bot
if errorlevel 3 goto end
echo 리모컨이 멈췄어요. 1분 뒤 다시 켭니다... (완전히 끄려면 이 창을 닫으세요)
timeout /t 60 >nul
goto loop
:end
