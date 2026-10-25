@echo off
rem Use the Xbox controller layout for Skyward Sword (your old controls are kept as a backup).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0controls.ps1" -Apply
pause
