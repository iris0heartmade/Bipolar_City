@echo off
rem ==========================================================
rem  BIPOLE V2.1 Industry Economy Lab launcher (ASCII-only)
rem  - All Chinese text is printed by Python itself
rem  - This file avoids any non-ASCII characters so it
rem    works regardless of the active Windows code page
rem  - Place this file in the project root and double-click
rem ==========================================================
setlocal

cd /d "%~dp0"

rem Prefer the project's virtualenv; fall back to system Python
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem Make project modules importable and keep stdio in UTF-8
set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"

rem Probe Python before doing anything else
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python

"%PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto no_tk

"%PY%" -c "import tools.economy_lab" >nul 2>&1
if errorlevel 1 goto no_module

"%PY%" -m tools.economy_lab
if errorlevel 1 goto failed
goto done

:no_python
echo [x] Python not found.
echo     Either the .venv directory is missing, or Python 3.11+
echo     is not installed and on PATH.
echo     Reinstall Python with "Add Python to PATH" enabled.
echo.
pause
goto done

:no_tk
echo [x] The current Python is missing tkinter.
echo     Reinstall Python and enable the Tcl/Tk component.
echo.
pause
goto done

:no_module
echo [x] tools.economy_lab not importable.
echo     This file must live in the project root next to tools/.
echo.
pause
goto done

:failed
echo.
echo [x] The launcher exited abnormally. Scroll up for the error.
echo.
pause

:done
endlocal
