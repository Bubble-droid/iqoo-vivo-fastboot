@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0resign.ps1" %*
exit /b %ERRORLEVEL%
