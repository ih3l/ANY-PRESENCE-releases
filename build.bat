@echo off
REM Builds ANY-PRESENCE.exe (web interface). Run on Windows, in this folder.

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

python -m PyInstaller --noconfirm --onefile --noconsole ^
  --name ANY-PRESENCE ^
  --hidden-import pystray._win32 ^
  --icon icon.ico ^
  --add-data "index.html;." ^
  --add-data "tray.png;." ^
  --add-data "icon.ico;." ^
  any_presence_web.py
if errorlevel 1 goto :fail

echo.
echo Done! Your app is at dist\ANY-PRESENCE.exe
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1