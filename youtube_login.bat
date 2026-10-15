@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -q --disable-pip-version-check --no-warn-script-location google-api-python-client google-auth-oauthlib
python uploader.py --login
pause
