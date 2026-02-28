#!/bin/bash
# Excel转TXT Web工具 - 一键启动脚本
# 在 Git Bash 中执行: bash start.sh

PYTHON="/c/software/Anaconda3/python.exe"

echo "========================================"
echo "   Excel → TXT 转换工具"
echo "========================================"

# 检查Python
if [ ! -f "$PYTHON" ]; then
    echo "[错误] 未找到 Python: $PYTHON"
    echo "请修改 start.sh 中的 PYTHON 路径"
    exit 1
fi

echo "[1/2] 安装依赖..."
"$PYTHON" -m pip install flask openpyxl werkzeug -q

echo "[2/2] 启动服务..."
echo ""
echo "  >>> 请在浏览器访问: http://127.0.0.1:5000 <<<"
echo ""
"$PYTHON" app.py
