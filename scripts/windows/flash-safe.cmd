@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash-safe.ps1" %*
exit /b %ERRORLEVEL%
