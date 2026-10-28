@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo PC 에 있는 예전 사연 쇼츠로 롱폼을 만들어 유튜브에 올려요...
python longform.py
pause
