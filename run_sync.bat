@echo off
title Ambika Portal to Tally Sync
cd /d "%~dp0"
echo ========================================================
echo   AMBIKA MULTI SERVICES - LIVE PORTAL DATA SYNC
echo ========================================================
python portal_sync.py --once
echo.
echo ========================================================
echo   Sync Complete!
echo ========================================================
pause
