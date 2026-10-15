@echo off
chcp 65001 >nul
set "SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%SU%\hookshorts_bot.bat" echo @echo off
>> "%SU%\hookshorts_bot.bat" echo start "hookshorts" /min "%~dp0start_bot.bat"
echo.
echo [완료] 이제 PC를 켜고 로그인하면 리모컨이 자동으로 켜져요.
echo 지금 바로 켜려면 start_bot.bat 을 더블클릭하세요.
echo 자동 시작을 없애려면 remove_autostart.bat 을 실행하세요.
echo.
pause
