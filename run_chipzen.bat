@echo off
cd /d C:\Users\billy\poker-arena
title Chipzen bot - closes itself after the match
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set TS=%%i
echo Chipzen bot running... log: chipzen_run_%TS%.log
py chipzen_agent.py %1 > chipzen_run_%TS%.log 2>&1
