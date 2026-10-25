@echo off
rem ---------------------------------------------------------------------
rem  Set up the dev environment: create a venv, install deps, run tests.
rem
rem  NOTE: keep this file pure ASCII. cmd.exe reads .bat files with the
rem  console ANSI codepage; UTF-8 Chinese comments desync the parser and
rem  silently corrupt later lines. Chinese docs live in README.md.
rem ---------------------------------------------------------------------
setlocal

cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
    exit /b 1
)

if not exist ".venv" (
    echo [1/3] Creating virtual environment .venv
    python -m venv .venv
    if errorlevel 1 exit /b 1
) else (
    echo [1/3] Reusing existing virtual environment .venv
)

call .venv\Scripts\activate.bat

echo [2/3] Installing dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo [3/3] Running tests
python -m pytest tests -q
if errorlevel 1 (
    echo [WARN] Some tests failed.
    exit /b 1
)

echo.
echo Done. Run:  .venv\Scripts\python -m pcmonitor
endlocal
