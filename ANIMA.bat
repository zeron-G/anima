@echo off
cd /d "%~dp0"

:: Find Python environment (prefer pythonw for no console)
if exist "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" (
    start "" "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" -m anima %*
    exit /b
)
if exist "D:\program\codesupport\anaconda\envs\anima\python.exe" (
    start "" /min "D:\program\codesupport\anaconda\envs\anima\python.exe" -m anima %*
    exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m anima %*
    exit /b
)

:: Fallback
start "" /min python -m anima %*
