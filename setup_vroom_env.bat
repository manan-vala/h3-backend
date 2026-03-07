@echo off
REM setup_vroom_env.bat  –  Create Python 3.10 venv for VROOM
REM Run this ONCE from h3-backend\:   .\setup_vroom_env.bat

echo =================================================
echo  VROOM Environment Setup
echo =================================================

REM Check if Python 3.10 is available
py -3.10 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python 3.10 not found via the Python Launcher.
    echo.
    echo Please install Python 3.10 from:
    echo   https://www.python.org/downloads/release/python-31011/
    echo   ^(tick "Add to PATH" during install^)
    echo.
    echo Then re-run this script.
    pause
    exit /b 1
)

echo [OK] Python 3.10 found.
py -3.10 --version

echo.
echo Creating vroom_env\...
py -3.10 -m venv vroom_env

echo Installing pyvroom, pandas, openpyxl...
vroom_env\Scripts\pip install --upgrade pip
vroom_env\Scripts\pip install pyvroom pandas openpyxl

echo.
echo Testing vroom import...
vroom_env\Scripts\python -c "import vroom; p=vroom.Input(); print('[OK] vroom works:', dir(vroom)[:5])"
if %errorlevel% neq 0 (
    echo [ERROR] vroom import failed. Check output above.
    pause
    exit /b 1
)

echo.
echo =================================================
echo  Setup complete! VROOM is ready.
echo  The solver will automatically use vroom_env\Scripts\python.exe
echo =================================================
pause
