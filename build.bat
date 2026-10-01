@echo off
rem Сборка hh-auto.exe двойным кликом. Результат: dist\hh-auto\hh-auto.exe и dist\hh-auto-windows.zip
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python packaging\build.py %*
echo.
pause
