@echo off
REM ============================================================================
REM  Command-line entry: mbpreview.bat <subcommand> [args...]
REM
REM  Keep this file pure ASCII (see preview.bat for why).
REM
REM  Examples:
REM    mbpreview.bat doctor
REM    mbpreview.bat mods
REM    mbpreview.bat bake PitaoYingOutfits
REM    mbpreview.bat check PitaoYingOutfits
REM    mbpreview.bat shot --mod PitaoYingOutfits --anim inventory_idle --view left -o out.png
REM    mbpreview.bat serve
REM ============================================================================
setlocal
set "ROOT=%~dp0"
python "%ROOT%cli\mbpreview.py" %*
endlocal
