@echo off
chcp 65001 >nul
title 国际旅游规划助手 - 后端服务
cd /d %~dp0server

::: ============================================================
::: MySQL 密码【绝对不要写在本文件里】（本文件会提交到 GitHub，会泄露）
::: 二选一：
:::   1) 在系统环境变量里设置 MYSQL_PASSWORD
:::   2) 新建 server\.env.local（已在 .gitignore 中，不会被提交），内容一行：
:::        MYSQL_PASSWORD=你的真实密码
::: ============================================================
if exist ".env.local" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env.local") do (
        if not "%%a"=="" set "%%a=%%b"
    )
)
if "%MYSQL_PASSWORD%"=="" (
    echo [提示] 未设置 MYSQL_PASSWORD，将以空密码连接 MySQL。
    echo        若连不上数据库，请设置环境变量或创建 server\.env.local（详见本脚本注释）
)

echo.
echo  ============================================
echo    国际旅游规划助手 - 后端服务启动中...
echo    访问地址: http://127.0.0.1:5000
echo    按 Ctrl+C 停止服务
echo  ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请安装 Python 3.10+ 并添加到 PATH
    pause
    exit /b 1
)

:: 检查 Flask
echo [检查依赖] 正在验证 Flask...
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [安装依赖] 正在安装 Flask...
    pip install -r ../requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络或手动运行 pip install -r requirements.txt
        pause
        exit /b 1
    )
)

:: 启动本地大模型服务 (Ollama, AI 规划功能依赖；已运行则跳过)
echo [检查依赖] 正在检查本地大模型服务 (Ollama)...
curl -s -o nul -m 2 http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0ollama\ollama.exe" (
        echo [启动服务] 正在启动 Ollama 服务...
        start "Ollama Serve" /min "%~dp0ollama\ollama.exe" serve
        timeout /t 3 /nobreak >nul
    ) else (
        echo [提示] 未找到本地 Ollama，AI 规划功能将不可用（其他功能不受影响）
    )
) else (
    echo [启动服务] Ollama 已在运行，无需重复启动
)

:: 启动服务
python app.py
if errorlevel 1 (
    echo.
    echo [错误] 服务启动失败，请查看上方报错信息
    pause
)
pause
