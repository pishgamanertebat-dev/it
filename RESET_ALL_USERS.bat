@echo off
setlocal
set "HERMES_ROOT=C:\Users\win-10\AppData\Local\hermes\hermes-agent"
set "HERMES_HOME=C:\Users\win-10\AppData\Local\hermes"
set "PY=%~dp0.venv\Scripts\python.exe"
set "HERMES=%HERMES_ROOT%\venv\Scripts\hermes.exe"
set "SCRIPT=%~dp0tools\reset_user_sessions.py"
set "PYTHONDONTWRITEBYTECODE=1"
set "PLATFORM=bale"
if not "%~1"=="" set "PLATFORM=%~1"
echo.
echo KOMATSO AI - Fresh sessions for %PLATFORM%
"%PY%" "%SCRIPT%" --platform "%PLATFORM%"
if errorlevel 1 goto failed
echo.
echo Conversation context will start fresh on the next message.
echo Stored history and user registration will be retained.
echo Gateway will stop briefly. Run this between active conversations.
choice /C YN /M "Apply this reset"
if errorlevel 2 exit /b 0
"%HERMES%" gateway stop
if errorlevel 1 goto failed
"%PY%" "%SCRIPT%" --platform "%PLATFORM%" --apply
if errorlevel 1 goto reset_failed
"%HERMES%" gateway start
if errorlevel 1 goto start_failed
echo Reset completed; gateway start command succeeded.
pause
exit /b 0
:reset_failed
echo RESET FAILED. Gateway is left stopped for inspection.
echo Inspect the error and %HERMES_HOME%\backups\session-reset before retrying.
echo To start manually: "%HERMES%" gateway start
pause
exit /b 1
:start_failed
echo Sessions were reset, but gateway startup failed. Do not repeat the reset.
echo To retry startup: "%HERMES%" gateway start
pause
exit /b 1
:failed
echo Operation failed. No reset was applied by this launcher.
pause
exit /b 1
