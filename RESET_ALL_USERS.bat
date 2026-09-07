@echo off
setlocal

set "HERMES_ROOT=C:\Users\win-10\AppData\Local\hermes\hermes-agent"
set "HERMES_HOME=C:\Users\win-10\AppData\Local\hermes"

set "PY=%HERMES_ROOT%\venv\Scripts\python.exe"
set "HERMES=%HERMES_ROOT%\venv\Scripts\hermes.exe"

echo.
echo ==========================================
echo       KOMATSO AI - RESET ALL USERS
echo ==========================================
echo.
echo This will start a fresh Telegram session
echo for every currently routed user.
echo.
choice /C YN /M "Continue"

if errorlevel 2 (
    echo.
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo [1/4] Stopping Hermes Gateway...
"%HERMES%" gateway stop

timeout /t 2 /nobreak >nul

echo.
echo [2/4] Creating backup...

if exist "%HERMES_HOME%\state.db" (
    copy /Y "%HERMES_HOME%\state.db" "%HERMES_HOME%\state.before_bulk_reset.db" >nul
)

if exist "%HERMES_HOME%\sessions\sessions.json" (
    copy /Y "%HERMES_HOME%\sessions\sessions.json" "%HERMES_HOME%\sessions\sessions.before_bulk_reset.json" >nul
)

echo.
echo [3/4] Resetting Telegram sessions...

cd /d "%HERMES_ROOT%"

"%PY%" -c "from pathlib import Path; from hermes_constants import get_hermes_home; from gateway.session import SessionStore; from gateway.config import GatewayConfig; h=Path(get_hermes_home()); s=SessionStore(h/'sessions',GatewayConfig()); es=s.list_sessions(); t=[e for e in es if str(getattr(getattr(e,'platform',None),'value',getattr(e,'platform',None))).lower()=='telegram']; print('Telegram routes found:',len(t)); [(lambda r,e=e: print('RESET:',e.session_key,'->',r.session_id if r else 'FAILED'))(s.reset_session(e.session_key,getattr(e,'display_name',None))) for e in t]; print('Done.')"

set "RESET_RESULT=%ERRORLEVEL%"

echo.
echo [4/4] Starting Hermes Gateway...
"%HERMES%" gateway start

echo.

if not "%RESET_RESULT%"=="0" (
    echo ==========================================
    echo RESET FAILED
    echo Gateway was started again.
    echo Backups were preserved.
    echo ==========================================
    pause
    exit /b 1
)

echo ==========================================
echo SUCCESS
echo Telegram sessions were reset.
echo ==========================================
echo.
echo Users without an existing route will also
echo receive a fresh session on their next message.
echo.

pause