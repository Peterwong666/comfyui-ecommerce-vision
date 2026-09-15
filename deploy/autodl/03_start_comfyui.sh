#!/usr/bin/env bash
# ============================================================
# 03_start_comfyui.sh —— 启动 ComfyUI 服务（P2-04）
# 安全说明：默认只监听 127.0.0.1。ComfyUI 无鉴权，暴露公网等于裸奔，
#           需要外部访问请用 SSH 端口转发，不要用 --listen 0.0.0.0。
# ============================================================
set -Eeuo pipefail

COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
LISTEN="${COMFY_LISTEN:-127.0.0.1}"
PORT="${COMFY_PORT:-8188}"
LOG="${COMFY_LOG:-/root/autodl-tmp/comfyui.log}"

# 已在运行则跳过
if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
  echo "ComfyUI 已在运行 (port ${PORT})"
  curl -s "http://127.0.0.1:${PORT}/system_stats" | head -c 400
  echo
  exit 0
fi

echo "启动 ComfyUI ..."
cd "$COMFY_DIR"
setsid nohup "$PYTHON_BIN" main.py \
  --listen "$LISTEN" \
  --port "$PORT" \
  > "$LOG" 2>&1 &

echo "PID=$! 日志=$LOG (监听 ${LISTEN}:${PORT})"

# 等待就绪（最多 180s）
for i in $(seq 1 60); do
  if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
    echo "就绪，耗时约 ${i} 次轮询"
    curl -s "http://127.0.0.1:${PORT}/system_stats"
    echo
    exit 0
  fi
  sleep 3
done

echo "启动超时，请检查日志尾部："
tail -30 "$LOG"
exit 1
