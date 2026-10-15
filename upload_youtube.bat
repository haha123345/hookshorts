@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (set /p EP=올릴 영상 번호를 입력하세요 [예: ep001]: ) else (set EP=%~1)
python uploader.py %EP%
pause
