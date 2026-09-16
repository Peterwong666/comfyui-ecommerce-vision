#!/usr/bin/env bash
# ============================================================
# 03_start_comfyui.sh —— 启动 ComfyUI 服务（P2-04）
# 安全说明：默认只监听 127.0.0.1。ComfyUI 无鉴权，暴露公网等于裸奔，
#           需要外部访问请用 SSH 端口转发，不要用 --listen 0.0.0.0。
#
# 为什么默认加 --highvram（2026-09-16 实测决定，见 项目进展.md #17）：
#   v0.36.0 起 dynamic VRAM 与 async weight offload 在 Nvidia 上**默认开启**，
#   它们对 SDXL 这种「显存装得下的小模型」是纯开销，使单张出图从 4.6s 劣化到 7.1s（1.58x）。
#   四变体单变量 A/B 证明元凶就是 dynamic VRAM；--highvram 关掉它，并让模型常驻。
#   实测稳态地板：--highvram 4.59s ≈ 升级前基线 4.54s（差 1%，回归已修复）；
#                而 --disable-dynamic-vram 单独使用为 4.85s，明显更差。
#   双底模（SDXL + FLUX.2 klein）常驻仅占 13.1GB/24GB，符合 NFR-2「并发=1、模型常驻显存」的设计。
#   ⚠️ 若未来同时常驻的模型总量逼近 24G，需重新评估该参数（highvram 不卸载模型）。
# ============================================================
set -Eeuo pipefail

COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
LISTEN="${COMFY_LISTEN:-127.0.0.1}"
PORT="${COMFY_PORT:-8188}"
LOG="${COMFY_LOG:-/root/autodl-tmp/comfyui.log}"
EXTRA_ARGS="${COMFY_EXTRA_ARGS:---highvram}"

# 已在运行则跳过
if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
  echo "ComfyUI 已在运行 (port ${PORT})"
  curl -s "http://127.0.0.1:${PORT}/system_stats" | head -c 400
  echo
  exit 0
fi

echo "启动 ComfyUI ... (额外参数: ${EXTRA_ARGS:-无})"
cd "$COMFY_DIR"
# shellcheck disable=SC2086  # EXTRA_ARGS 需按空格拆成多个参数
setsid nohup "$PYTHON_BIN" main.py \
  --listen "$LISTEN" \
  --port "$PORT" \
  $EXTRA_ARGS \
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
