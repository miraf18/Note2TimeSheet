@echo off
setlocal
rem ======================================================================
rem  Note2TimeSheet v2 - minimized launcher
rem
rem  Runs start_timesheet.bat (venv + dependencies + app) in a new console
rem  window started minimized, then returns immediately. If the launcher
rem  hits an error its window stays open in the taskbar with the message.
rem ======================================================================
cd /d "%~dp0"

if not exist "start_timesheet.bat" (
    echo [ERRORE] start_timesheet.bat non trovato in %CD%.
    pause
    exit /b 1
)

start "Note2TimeSheet" /min cmd /c .\start_timesheet.bat
endlocal
exit /b 0
