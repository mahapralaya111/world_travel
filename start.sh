#!/bin/bash
# 国际旅游规划助手 - 后端服务启动脚本 (macOS/Linux)
cd "$(dirname "$0")/server"

# MySQL 密码不要写进仓库：优先读 server/.env.local（已 gitignore），否则用环境变量 MYSQL_PASSWORD
if [ -f .env.local ]; then set -a; . ./.env.local; set +a; fi
[ -z "$MYSQL_PASSWORD" ] && echo "[提示] 未设置 MYSQL_PASSWORD（可在 server/.env.local 或环境变量中配置，详见 start.bat 注释）"

echo ""
echo "  ============================================"
echo "   国际旅游规划助手 - 后端服务启动中..."
echo "   访问地址: http://127.0.0.1:5000"
echo "   按 Ctrl+C 停止服务"
echo "  ============================================"
echo ""
python3 app.py
