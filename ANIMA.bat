@echo off
title ANIMA - Intelligent Life System
echo.
echo   ========================================
echo     ANIMA  -  Intelligent Life System
echo   ========================================
echo.

:: Try conda environment first
if exist "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" (
    echo   [OK] Using conda anima environment
    echo   Starting ANIMA...
    echo.
    start "" "D:\program\codesupport\anaconda\envs\anima\pythonw.exe" -m anima %*
    goto :done
)

:: Try conda via activate
where conda >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   [OK] Activating conda anima environment
    echo   Starting ANIMA...
    echo.
    call conda activate anima
    start "" pythonw -m anima %*
    goto :done
)

:: Try local venv
if exist ".venv\Scripts\pythonw.exe" (
    echo   [OK] Using local .venv
    echo   Starting ANIMA...
    echo.
    start "" ".venv\Scripts\pythonw.exe" -m anima %*
    goto :done
)

:: Fallback to system python (visible console)
echo   [!!] No conda/venv found, using system python
echo   Starting ANIMA (console mode)...
echo.
python -m anima %*
goto :done

:done
echo   ANIMA launched.
timeout /t 2 >nul
