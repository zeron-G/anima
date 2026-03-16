@echo off
cd /d "%~dp0"

:: Conda environment (primary)
if exist "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" (
    start "" "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" -m anima %*
    exit /b
)

:: Local venv
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m anima %*
    exit /b
)

:: Fallback
start "" /min python -m anima %*
