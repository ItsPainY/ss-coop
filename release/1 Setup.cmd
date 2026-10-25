@echo off
rem Double-click: one-time setup. Asks for your disc dump; uses host-save\ if it is there.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
