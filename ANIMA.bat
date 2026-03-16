@echo off
cd /d "%~dp0"

:: Find Python: conda env > local venv > system python
:: Check conda env by name (portable across machines)
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    start "" "%%i" -m anima %*
    exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m anima %*
    exit /b
)
if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" -m anima %*
    exit /b
)

:: Fallback: python with minimized window
start "" /min python -m anima %*
