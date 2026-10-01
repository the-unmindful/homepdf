@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_start_menu_shortcut.ps1" -ExePath "%~dp0PDFUltimate.exe"
if errorlevel 1 (
  echo.
  echo HomePDF could not be added to Start. The error is shown above.
  pause
  exit /b 1
)
echo.
echo HomePDF has been added to your Start menu.
pause
