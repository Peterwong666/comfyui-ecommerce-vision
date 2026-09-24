#!/usr/bin/env bash
# deploy/autodl/46_startup_all.sh
# GPU 服务器一键启动全部服务（ComfyUI + Redis + Mock S3 + Backend + Celery Worker）
# 用法：sshpass -p 'xxx' ssh -p 22910 root@host 'bash /root/comfyui-platform/startup_all.sh'
set -euo pipefail

export PATH="/root/miniconda3/bin:$PATH"
PLATFORM_DIR="/root/comfyui-platform"
BACKEND_DIR="$PLATFORM_DIR/backend"
LOG_DIR="/tmp"

echo "=== 1. Redis ==="
if redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "Redis already running"
else
  redis-server --daemonize yes --port 6379
  echo "Redis started"
fi

echo "=== 2. Mock S3 (MinIO replacement) ==="
if curl -s --max-time 2 http://127.0.0.1:9000/ >/dev/null 2>&1; then
  echo "Mock S3 already running"
else
  nohup python $PLATFORM_DIR/mock_s3.py 9000 > $LOG_DIR/mock_s3.log 2>&1 &
  sleep 2
  echo "Mock S3 started"
fi

echo "=== 3. ComfyUI ==="
if curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
  echo "ComfyUI already running"
else
  cd /root/ComfyUI
  nohup python main.py --listen 0.0.0.0 --port 8188 --highvram > $LOG_DIR/comfyui.log 2>&1 &
  echo "ComfyUI starting (waiting 15s for model load)..."
  sleep 15
  if curl -s --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
    echo "ComfyUI ready"
  else
    echo "WARNING: ComfyUI may still be loading, check $LOG_DIR/comfyui.log"
  fi
fi

echo "=== 4. Backend (FastAPI) ==="
if curl -s --max-time 3 http://127.0.0.1:8010/health >/dev/null 2>&1; then
  echo "Backend already running"
else
  pkill -f "uvicorn.*8010" 2>/dev/null || true
  sleep 1
  cd $BACKEND_DIR
  source .venv/bin/activate
  export PYTHONPATH="$BACKEND_DIR:$PLATFORM_DIR"
  export DATABASE_URL='sqlite+pysqlite:///./dev.db'
  export COMFYUI_BASE_URL='http://127.0.0.1:8188'
  export COMFYUI_WS_URL='ws://127.0.0.1:8188/ws'
  export SECRET_KEY='dev-secret-key-for-testing'
  export REDIS_URL='redis://127.0.0.1:6379/0'
  export CELERY_BROKER_URL='redis://127.0.0.1:6379/1'
  export CELERY_RESULT_BACKEND='redis://127.0.0.1:6379/2'
  nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 > $LOG_DIR/backend.log 2>&1 &
  sleep 3
  if curl -s --max-time 3 http://127.0.0.1:8010/health >/dev/null 2>&1; then
    echo "Backend started on :8010"
  else
    echo "ERROR: Backend failed to start, check $LOG_DIR/backend.log"
  fi
fi

echo "=== 5. Celery Worker ==="
if pgrep -f "celery.*worker" >/dev/null 2>&1; then
  echo "Celery worker already running"
else
  cd $BACKEND_DIR
  source .venv/bin/activate
  export PYTHONPATH="$BACKEND_DIR:$PLATFORM_DIR"
  export DATABASE_URL='sqlite+pysqlite:///./dev.db'
  export COMFYUI_BASE_URL='http://127.0.0.1:8188'
  export COMFYUI_WS_URL='ws://127.0.0.1:8188/ws'
  export SECRET_KEY='dev-secret-key-for-testing'
  export REDIS_URL='redis://127.0.0.1:6379/0'
  export CELERY_BROKER_URL='redis://127.0.0.1:6379/1'
  export CELERY_RESULT_BACKEND='redis://127.0.0.1:6379/2'
  nohup celery -A app.worker.celery_app worker --loglevel=info --concurrency=1 \
    -Q gen_high,gen_default,gen_low,maintenance > $LOG_DIR/celery.log 2>&1 &
  sleep 3
  echo "Celery worker started"
fi

echo ""
echo "=== All services status ==="
echo "Redis:      $(redis-cli ping 2>/dev/null || echo 'DOWN')"
echo "Mock S3:    $(curl -s --max-time 2 http://127.0.0.1:9000/ >/dev/null 2>&1 && echo 'OK' || echo 'DOWN')"
echo "ComfyUI:    $(curl -s --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1 && echo 'OK' || echo 'DOWN')"
echo "Backend:    $(curl -s --max-time 3 http://127.0.0.1:8010/health | head -c 20 || echo 'DOWN')"
echo "Celery:     $(pgrep -f 'celery.*worker' >/dev/null 2>&1 && echo 'OK' || echo 'DOWN')"
echo ""
echo "=== Done. Backend API at http://0.0.0.0:8010 ==="
