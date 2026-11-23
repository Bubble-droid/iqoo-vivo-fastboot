@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash-full.ps1" %*
exit /b %ERRORLEVEL%
