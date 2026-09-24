#!/usr/bin/env bash
# deploy/autodl/45_deploy_backend_gpu.sh
# 在 GPU 服务器上部署后端，连接本地 ComfyUI（端口 8188）
# 用法：本地执行 bash deploy/autodl/45_deploy_backend_gpu.sh
set -euo pipefail

REMOTE_HOST="root@connect.westc.seetacloud.com"
REMOTE_PORT="22910"
REMOTE_DIR="/root/comfyui-platform"
LOCAL_BACKEND="$(dirname "$0")/../../backend"

echo "=== 1. 同步后端代码到 GPU 服务器 ==="
sshpass -p 'Q5HRxhp++Qfx' ssh -p "$REMOTE_PORT" -o StrictHostKeyChecking=no "$REMOTE_HOST" \
  "mkdir -p $REMOTE_DIR/backend/app $REMOTE_DIR/backend/tests $REMOTE_DIR/workflows"

# 同步 backend 目录
sshpass -p 'Q5HRxhp++Qfx' rsync -az --delete \
  -e "ssh -p $REMOTE_PORT -o StrictHostKeyChecking=no" \
  "$LOCAL_BACKEND/" "$REMOTE_HOST:$REMOTE_DIR/backend/"

# 同步 workflows/registry.yaml
sshpass -p 'Q5HRxhp++Qfx' rsync -az \
  -e "ssh -p $REMOTE_PORT -o StrictHostKeyChecking=no" \
  "$(dirname "$0")/../../workflows/registry.yaml" "$REMOTE_HOST:$REMOTE_DIR/workflows/"

echo "=== 2. 安装 Python 依赖 ==="
sshpass -p 'Q5HRxhp++Qfx' ssh -p "$REMOTE_PORT" -o StrictHostKeyChecking=no "$REMOTE_HOST" bash <<'REMOTE_SCRIPT'
set -euo pipefail
cd /root/comfyui-platform/backend

# 使用 miniconda 的 python
PYTHON=/root/miniconda3/bin/python

# 创建 venv（如果不存在）
if [ ! -d .venv ]; then
  $PYTHON -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt 2>/dev/null || pip install -q \
  fastapi uvicorn[standard] sqlalchemy[asyncio] aiosqlite alembic \
  pydantic pydantic-settings python-jose[cryptography] passlib[bcrypt] \
  httpx python-multipart redis minio Pillow scikit-image 2>/dev/null

echo "Dependencies installed."
REMOTE_SCRIPT

echo "=== 3. 初始化数据库 ==="
sshpass -p 'Q5HRxhp++Qfx' ssh -p "$REMOTE_PORT" -o StrictHostKeyChecking=no "$REMOTE_HOST" bash <<'REMOTE_SCRIPT'
set -euo pipefail
cd /root/comfyui-platform/backend
source .venv/bin/activate

export DATABASE_URL='sqlite+pysqlite:///./dev.db'
python -m app.cli.init_db 2>/dev/null || echo "DB already exists"
python -m app.cli.seed_workflows 2>/dev/null || echo "Workflows already seeded"
echo "DB ready."
REMOTE_SCRIPT

echo "=== 4. 启动后端（端口 8010）==="
sshpass -p 'Q5HRxhp++Qfx' ssh -p "$REMOTE_PORT" -o StrictHostKeyChecking=no "$REMOTE_HOST" bash <<'REMOTE_SCRIPT'
set -euo pipefail

# 先杀掉旧的后端进程
pkill -f "uvicorn.*8010" 2>/dev/null || true
sleep 1

cd /root/comfyui-platform/backend
source .venv/bin/activate

export DATABASE_URL='sqlite+pysqlite:///./dev.db'
export COMFYUI_BASE_URL='http://127.0.0.1:8188'
export COMFYUI_WS_URL='ws://127.0.0.1:8188/ws'
export SECRET_KEY='dev-secret-key-for-testing'
export CORS_ORIGINS='http://localhost:5173,http://localhost:8010'

nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 > /tmp/backend.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8010/health 2>/dev/null && echo " Backend started on :8010" || echo "Backend start failed"
REMOTE_SCRIPT

echo "=== 5. 建 SSH 隧道（本地 8011 → 远端 8010）==="
# 杀掉旧隧道
pkill -f "ssh.*8011.*22910" 2>/dev/null || true
sleep 1

sshpass -p 'Q5HRxhp++Qfx' ssh -p "$REMOTE_PORT" -o StrictHostKeyChecking=no \
  -L 8011:127.0.0.1:8010 -N -f "$REMOTE_HOST"
sleep 2

echo "=== 验证隧道 ==="
curl -s http://127.0.0.1:8011/health 2>/dev/null && echo "Tunnel OK: http://127.0.0.1:8011" || echo "Tunnel failed"

echo ""
echo "=== 完成 ==="
echo "后端地址：http://127.0.0.1:8011（通过隧道）"
echo "ComfyUI 地址：GPU 服务器本地 127.0.0.1:8188"
echo "前端请访问：http://localhost:5173"
