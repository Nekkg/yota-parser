@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto setup_failed
"%~dp0..\.runtime\python\Scripts\python.exe" "%~dp0..\run.py" %*
set "YOTA_EXIT_CODE=%errorlevel%"
goto finish
:setup_failed
set "YOTA_EXIT_CODE=1"
:finish
if "%~1"=="" pause
exit /b %YOTA_EXIT_CODE%
