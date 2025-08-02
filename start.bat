@echo off
setlocal

:: Setting up virtual environments and script paths
set VENV_PATH=%cd%\venv
set ACTIVATE_PATH=%VENV_PATH%\Scripts\activate.bat
set SERVER_SCRIPT=%cd%\debug_server.py
set CLIENT_SCRIPT=%cd%\example_client.py
set LOG_FILE=%cd%\codesys_api_server.log
set PY_PATH=%VENV_PATH%\Scripts\python.exe

if exist "%LOG_FILE%" (
    echo. > "%LOG_FILE%"
    echo Log file cleared: %LOG_FILE%
)

::  Start Administrator cmd to run debug_server.py
echo Starting debug_server.py in admin cmd...

powershell -Command "Start-Process cmd -Verb RunAs -ArgumentList '/k cd /d \"%cd%\" && call \"%ACTIVATE_PATH%\" && python \"%SERVER_SCRIPT%\" '"

timeout /t 2 /nobreak >nul

@REM ::  Start a non-administrator cmd and run example_client.py
echo Starting example_client.py in normal cmd...

start cmd /k "call "%ACTIVATE_PATH%" && python "%CLIENT_SCRIPT%" "

echo All tasks launched. This script will now exit.
exit /b 0
