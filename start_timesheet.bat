@echo off
setlocal EnableExtensions
title Note2TimeSheet
cd /d "%~dp0"

rem ======================================================================
rem  Note2TimeSheet v2 - Windows launcher
rem
rem  1. locate Python 3.10+ ("py -3" first, then "python")
rem  2. create the .venv virtual environment if it is missing
rem  3. install dependencies only when requirements.txt is newer than the
rem     stamp file .venv\.deps-installed (written after a successful install)
rem  4. run app.py
rem  On any error the message is printed and the window stays open (pause).
rem ======================================================================

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "STAMP=%VENV_DIR%\.deps-installed"
set "REQS=requirements.txt"
set "PY_CMD="

if not exist "app.py" (
    call :fail "app.py non trovato in %CD%. Avvia lo script dalla cartella del progetto."
    exit /b 1
)
if not exist "%REQS%" (
    call :fail "%REQS% non trovato in %CD%."
    exit /b 1
)

if exist "%VENV_PY%" goto :deps

rem ---- 1. locate a suitable Python -------------------------------------
call :probe py -3
if not defined PY_CMD call :probe python
if not defined PY_CMD (
    call :fail "Python 3.10 o superiore non trovato. Installalo da https://www.python.org/downloads/ spuntando 'Add python.exe to PATH', poi riavvia questo script."
    exit /b 1
)
echo [Note2TimeSheet] Python trovato: %PY_CMD%

rem ---- 2. create the virtual environment -------------------------------
echo [Note2TimeSheet] Creazione ambiente virtuale in "%VENV_DIR%" ...
%PY_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
    call :fail "Creazione dell'ambiente virtuale fallita. Controlla i messaggi sopra."
    exit /b 1
)
if not exist "%VENV_PY%" (
    call :fail "Ambiente virtuale creato ma %VENV_PY% non esiste."
    exit /b 1
)

:deps
rem ---- 3. install dependencies only when requirements.txt changed ------
if not exist "%STAMP%" goto :install
"%VENV_PY%" -c "import os, sys; sys.exit(0 if os.path.getmtime(r'%STAMP%') >= os.path.getmtime(r'%REQS%') else 1)" >nul 2>&1
if errorlevel 1 goto :install
goto :run

:install
echo [Note2TimeSheet] Installazione dipendenze da %REQS% (puo' richiedere qualche minuto) ...
"%VENV_PY%" -m pip install --quiet --disable-pip-version-check -r "%REQS%"
if errorlevel 1 (
    call :fail "Installazione delle dipendenze fallita. Controlla la connessione di rete; se il problema persiste elimina la cartella %VENV_DIR% e riprova."
    exit /b 1
)
rem The stamp's modification time marks the last successful install.
> "%STAMP%" echo %DATE% %TIME%
echo [Note2TimeSheet] Dipendenze installate.

:run
rem ---- 4. run the application ------------------------------------------
echo [Note2TimeSheet] Avvio dell'applicazione ...
"%VENV_PY%" app.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    call :fail "L'applicazione e' terminata con codice di errore %EXIT_CODE%."
    exit /b %EXIT_CODE%
)
endlocal
exit /b 0

rem ---- helpers ---------------------------------------------------------
:probe
rem Sets PY_CMD to "%*" when that command is a Python 3.10 or newer.
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=%*"
exit /b 0

:fail
echo.
echo [ERRORE] %~1
echo.
pause
exit /b 1
