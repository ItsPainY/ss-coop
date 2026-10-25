@echo off
rem Double-click: start the game and join the host. Leave this window open while playing.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0play.ps1" join
pause
