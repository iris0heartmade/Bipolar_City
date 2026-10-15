@echo off
rem ==========================================================
rem  BIPOLE industry management web launcher (ASCII-only)
rem  - All Chinese text is rendered by the browser / server
rem  - Avoids non-ASCII characters so it works under any
rem    Windows code page
rem  - Place this file in the project root and double-click
rem ==========================================================
setlocal

cd /d "%~dp0"

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"

"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python

"%PY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 goto no_deps

"%PY%" -c "import backend.app" >nul 2>&1
if errorlevel 1 goto no_module

echo Starting BIPOLE industry web server...
echo Open http://127.0.0.1:8765/ in your browser.
echo Close this window to stop the server.

"%PY%" -m backend.app
if errorlevel 1 goto failed
goto done

:no_python
echo [x] Python not found.
echo     Either the .venv directory is missing, or Python 3.11+
echo     is not installed and on PATH.
echo.
pause
goto done

:no_deps
echo [x] Required Python packages are missing.
echo     Run: "%PY%" -m pip install -r requirements.txt
echo.
pause
goto done

:no_module
echo [x] backend.app not importable.
echo     This file must live in the project root.
echo.
pause
goto done

:failed
echo.
echo [x] Server exited abnormally. Scroll up for the error.
echo.
pause

:done
endlocal
