#!/usr/bin/env bash
# ============================================================
# 01_lock_versions.sh —— 采集 ComfyUI 环境版本基线（P2-06）
# 用法：在 autoDL 实例上以 root 执行
#
# 说明（2026-09-16 增补）：
#   仅锁「包版本」不足以复现环境 —— v0.3.75 → v0.36.0 升级时，
#   新版把 async weight offloading 与 dynamic VRAM 设为 Nvidia 上默认开启，
#   包版本全部锁死也照样复现不出旧性能（SDXL 回归 1.58x，见 项目进展.md #17）。
#   因此本脚本额外采集：
#     ① [launch]  实际启动参数 + 从日志解析出的「生效中」运行时开关
#     ② [models]  模型文件 sha256（M2 验收：销毁重建仍能出同一张图）
# ============================================================
set -Eeuo pipefail

COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
DATA_DIR="${DATA_DIR:-/root/autodl-tmp/comfyui-data}"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
LOG="${COMFY_LOG:-/root/autodl-tmp/comfyui.log}"
PORT="${COMFY_PORT:-8188}"
HASH_MODELS="${HASH_MODELS:-1}"   # 置 0 可跳过大文件哈希（哈希期间会占用磁盘 I/O）
LOCK="$DATA_DIR/versions.lock"

mkdir -p "$DATA_DIR"

# 清理历史上因转义错误生成的字面量文件 '$LOCK'
find /root -maxdepth 2 -name '$LOCK' -delete 2>/dev/null || true

{
  echo "# ============================================================"
  echo "# ComfyUI 商用封装平台 · 环境版本基线"
  echo "# 生成时间: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "# 用途: P2-06 版本锁定。重建环境时以此为唯一事实来源。"
  echo "# ============================================================"
  echo
  echo "[host]"
  echo "platform=autoDL"
  echo "os=$(. /etc/os-release && printf '%s' "$PRETTY_NAME")"
  echo "kernel=$(uname -r)"
  echo "cpu_cores=$(nproc)"
  echo "ram_gb=$(free -g | awk '/^Mem:/{print $2}')"
  echo
  echo "[gpu]"
  nvidia-smi --query-gpu=name,driver_version,memory.total \
    --format=csv,noheader | awk -F', ' '{print "gpu="$1"\ndriver="$2"\nvram="$3}'
  echo
  echo "[python]"
  "$PYTHON_BIN" - <<'PY'
import sys, torch
print("version=" + sys.version.split()[0])
print("torch=" + torch.__version__)
print("cuda=" + str(torch.version.cuda))
print("cuda_available=" + str(torch.cuda.is_available()))
PY
  echo
  echo "[comfyui]"
  echo "path=$COMFY_DIR"
  echo "version=$(git -C "$COMFY_DIR" describe --tags 2>/dev/null || echo unknown)"
  echo "commit=$(git -C "$COMFY_DIR" rev-parse HEAD)"
  echo
  echo "[launch]"
  # argv 以运行中的实例为准（权威）；未运行则回退到启动脚本中的定义
  argv=$(curl -sf --max-time 5 "http://127.0.0.1:${PORT}/system_stats" \
         2>/dev/null | "$PYTHON_BIN" -c \
         'import json,sys; print(" ".join(json.load(sys.stdin)["system"]["argv"]))' \
         2>/dev/null || echo "NOT_RUNNING")
  echo "argv=$argv"
  # 从日志里挖出「实际生效」的运行时开关 —— 这才是性能的真正决定因素
  if [ -f "$LOG" ]; then
    awk '/Using (async weight offloading|pytorch attention|sage attention|flash attention|split attention)/ ||
         /DynamicVRAM/ || /VRAM support|Total VRAM|Set vram state/ {
           gsub(/^[[:space:]]+/, ""); print "effective_" NR "=" $0
         }' "$LOG" | tail -12
  else
    echo "log=NOT_FOUND($LOG)"
  fi
  echo
  echo "[models]"
  echo "# sha256 是 M2「销毁重建仍可复现同图」的锚点；size 便于快速比对"
  for d in checkpoints unet vae text_encoders; do
    for f in "$DATA_DIR/models/$d"/*.safetensors; do
      [ -e "$f" ] || continue
      sz=$(stat -c '%s' "$f")
      if [ "$HASH_MODELS" = "1" ]; then
        h=$(sha256sum "$f" | cut -d' ' -f1)
      else
        h="SKIPPED"
      fi
      echo "$d/$(basename "$f") size=$sz sha256=$h"
    done
  done
  echo
  echo "[custom_nodes]"
  cd "$COMFY_DIR/custom_nodes"
  for d in */; do
    n="${d%/}"
    [ "$n" = "__pycache__" ] && continue
    if [ -d "$d/.git" ]; then
      echo "$n=$(git -C "$d" rev-parse HEAD)"
    else
      echo "$n=NO_GIT"
    fi
  done
} > "$LOCK"

echo >> "$LOCK"
echo "[pip]" >> "$LOCK"
"$PYTHON_BIN" -m pip freeze >> "$LOCK" 2>/dev/null || true

echo "OK -> $LOCK ($(wc -l < "$LOCK") 行)"
