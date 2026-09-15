#!/usr/bin/env bash
# ============================================================
# 01_lock_versions.sh —— 采集 ComfyUI 环境版本基线（P2-06）
# 用法：在 autoDL 实例上以 root 执行
# ============================================================
set -Eeuo pipefail

COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
DATA_DIR="${DATA_DIR:-/root/autodl-tmp/comfyui-data}"
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
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
