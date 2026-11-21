@echo off
rem Serves the web version at http://127.0.0.1:8765 (the page needs http://, not a file).
cd /d "%~dp0web"
echo Fake Drive Checker is running at http://127.0.0.1:8765
echo KEEP THIS WINDOW OPEN while you test. Close it to stop the server.
echo.
rem open the browser 2 s from now, once the server is listening
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 2; Start-Process http://127.0.0.1:8765"
py -3.12 -m http.server 8765 --bind 127.0.0.1
pause
