@echo off
cd /d "%~dp0"

start "" cmd /c "timeout /t 2 /nobreak > nul & start http://127.0.0.1:8000"

echo Starting Tabelog Bot admin panel...
echo.
echo Keep this window open while using the admin panel.
echo Close this window to stop the admin panel.
echo.

python -m uvicorn admin_app:app --host 127.0.0.1 --port 8000

echo.
echo Admin panel stopped.
pause
