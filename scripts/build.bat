@echo off
chcp 65001 >nul
setlocal

rem 打包成单文件 exe:dist\pc-monitor.exe
rem 目标机器不需要装 Python,但需要装并运行 PawnIO 驱动才能读到 CPU 温度/功耗。

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

if not exist "vendor\pawnio\PawnIOLib.dll" (
    echo [错误] 缺少 vendor\pawnio\PawnIOLib.dll,先按 README 补齐第三方文件
    exit /b 1
)

echo [1/2] 清理旧产物
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/2] 调用 PyInstaller
%PY% -m PyInstaller --noconfirm pc-monitor.spec
if errorlevel 1 (
    echo [错误] 打包失败
    exit /b 1
)

echo.
echo 打包完成: dist\pc-monitor.exe
dir dist\pc-monitor.exe
endlocal
