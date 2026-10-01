@echo off
REM ============================================================================
REM  Bannerlord Outfit Animation Previewer -- one-click launcher
REM
REM  IMPORTANT: keep this file pure ASCII.
REM  The console code page here is GBK; if this .bat contains UTF-8 Chinese,
REM  cmd mis-decodes the bytes, the shift can produce stray & | > characters,
REM  and the shell then tries to run the garbage as commands
REM  ("'xxx' is not recognized as an internal or external command").
REM  All Chinese output is printed by Python, never by this script.
REM
REM  Uses %~dp0 to locate the project root, so the whole folder can be moved.
REM ============================================================================
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo         Install Python 3.10+ and make sure "python" works in a terminal.
  pause
  exit /b 1
)

python "cli\mbpreview.py" doctor
if errorlevel 1 (
  echo.
  echo [ERROR] Environment check failed. Fix the items marked with X above.
  pause
  exit /b 1
)

if not "%~1"=="" (
  echo.
  echo [1/2] Baking mod: %~1
  python "cli\mbpreview.py" bake "%~1"
)

echo.
echo [2/2] Starting preview server (browser will open)...
python "cli\mbpreview.py" serve
endlocal
