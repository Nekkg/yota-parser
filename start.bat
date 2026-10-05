@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0bin\YotaParser.exe" goto source
"%~dp0bin\YotaParser.exe" %*
set "YOTA_EXIT_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %YOTA_EXIT_CODE%
:source
if not exist "%~dp0scripts\start-source.bat" goto missing
call "%~dp0scripts\start-source.bat" %*
exit /b %errorlevel%
:missing
echo Program files are missing. Copy the complete built folder before starting.
if "%~1"=="" pause
exit /b 1
