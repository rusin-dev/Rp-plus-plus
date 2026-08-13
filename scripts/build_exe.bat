@echo off
rem 一键编译：把项目打包为单文件 rp.exe（产物在 dist\ 下）
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

python scripts\build_exe.py %*
if errorlevel 1 (
    echo [ERROR] 编译失败，请检查上方输出
    pause
    exit /b 1
)

pause
