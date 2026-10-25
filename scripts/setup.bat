@echo off
chcp 65001 >nul
setlocal

rem 初始化开发环境:建虚拟环境、装依赖、跑一遍测试

cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python,请先安装 Python 3.10+ 并加入 PATH
    exit /b 1
)

if not exist ".venv" (
    echo [1/3] 创建虚拟环境 .venv
    python -m venv .venv
    if errorlevel 1 exit /b 1
) else (
    echo [1/3] 复用已有虚拟环境 .venv
)

call .venv\Scripts\activate.bat

echo [2/3] 安装依赖
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo [3/3] 跑测试
python -m pytest tests -q
if errorlevel 1 (
    echo [警告] 有测试没通过
    exit /b 1
)

echo.
echo 完成。运行:  .venv\Scripts\python -m pcmonitor
endlocal
