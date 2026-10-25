@echo off
rem ---------------------------------------------------------------------
rem  Build a single-file exe: dist\pc-monitor.exe
rem  The target machine needs no Python, but does need the PawnIO driver
rem  for CPU temperature / power readings.
rem
rem  NOTE: keep this file pure ASCII. cmd.exe reads .bat files with the
rem  console ANSI codepage; UTF-8 Chinese comments desync the parser and
rem  silently corrupt later lines. Chinese docs live in README.md.
rem ---------------------------------------------------------------------
setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

if not exist "vendor\pawnio\PawnIOLib.dll" (
    echo [ERROR] Missing vendor\pawnio\PawnIOLib.dll - see vendor\pawnio\README.md
    exit /b 1
)

echo [1/2] Cleaning old artifacts
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/2] Running PyInstaller
%PY% -m PyInstaller --noconfirm pc-monitor.spec
if errorlevel 1 (
    echo [ERROR] Build failed.
    exit /b 1
)

echo.
echo Build finished: dist\pc-monitor.exe
dir dist\pc-monitor.exe
endlocal
