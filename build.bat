@echo off
rem Сборка HH-Auto-Response.exe двойным кликом. Результат: dist\HH-Auto-Response\ и архив .zip
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python packaging\build.py %*
echo.
pause
