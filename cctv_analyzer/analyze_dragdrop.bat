@echo off
chcp 65001 >nul
rem Drag and drop video files or folders onto this .bat to analyze them
cd /d "%~dp0"
python analyze.py %* --out "%~dp0cctv_results"
if exist "%~dp0cctv_results" start "" "%~dp0cctv_results"
pause
