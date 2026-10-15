@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 优先使用项目自带的虚拟环境，没有则退回系统 Python
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem 让 python 找得到项目模块，并保证中文提示不乱码
set "PYTHONPATH=%CD%"
set "PYTHONIOENCODING=utf-8"

echo.
echo   ============================================
echo     BIPOLE V2.1 行业经济实验室
echo   ============================================
echo   左侧中文调参，右侧查看七行业经营、基准对照与趋势。
echo   关掉这个窗口即退出工具。
echo.

rem --- 检查 Python 解释器 ---
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python

rem --- 标准库 Tk 同时负责控件与曲线，不再依赖 matplotlib ---
"%PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto no_tk

rem --- 检查工具模块本身 ---
"%PY%" -c "import tools.economy_lab" >nul 2>&1
if errorlevel 1 goto no_module

"%PY%" -m tools.economy_lab
if errorlevel 1 goto failed
goto done

:no_python
echo   [x] 找不到可用的 Python。
echo.
echo       如果项目里原本有 .venv 目录，它可能被移动或删除了。
echo       否则请先安装 Python 3.11 或更新版本，
echo       安装时务必勾选 "Add Python to PATH"。
echo.
pause
goto done

:no_tk
echo   [x] 当前 Python 缺少 tkinter 图形界面组件。
echo.
echo       请在 Python 安装程序中启用 Tcl/Tk 组件后重试。
echo.
pause
goto done

:no_module
echo   [x] 找不到 tools.economy_lab 模块。
echo.
echo       通常是因为本文件被单独拷走了，或不在项目根目录。
echo       请把它放回项目根目录再双击。
echo.
pause
goto done

:failed
echo.
echo   [x] 工具异常退出，上方是具体错误信息。
echo.
pause

:done
endlocal
