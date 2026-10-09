@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === SCU Notice Radar ===
python radar.py check
echo.
start "" "digest\latest.html"
pause