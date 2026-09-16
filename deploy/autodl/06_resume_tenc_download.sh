#!/usr/bin/env bash
# ============================================================
# 06_resume_tenc_download.sh —— 续传 FLUX.2 全精度文本编码器
#
# 背景（见 项目进展.md #13）：
#   fp4 版 qwen_3_4b_fp4_flux2.safetensors 隐藏维 1280，
#   本机 ComfyUI v0.3.75 的 flux2 路径按 Qwen3_4B（hidden 2560）构建，
#   加载即 shape mismatch。故改用全精度 qwen_3_4b.safetensors。
#
# 为何要单独一个脚本：
#   实例关机时 aria2c 会被杀，.aria2 控制文件保留。
#   本脚本做三件事：① 幂等检查（已完成则直接退出）
#                  ② 用 -c 断点续传
#                  ③ 打印真实进度（只认 .aria2 与日志，不认文件大小）
# ============================================================
set -uo pipefail

TE_DIR="${TE_DIR:-/root/autodl-tmp/comfyui-data/models/text_encoders}"
FNAME="qwen_3_4b.safetensors"
URL="https://hf-mirror.com/Comfy-Org/flux2-klein-4B/resolve/main/split_files/text_encoders/${FNAME}"
LOG="/root/autodl-tmp/dl_klein_tenc_full.log"

# --- 幂等检查：.aria2 不存在 = 已下载完成 ---
if [ -f "$TE_DIR/$FNAME" ] && [ ! -f "$TE_DIR/$FNAME.aria2" ]; then
  echo "已下载完成，无需续传：$TE_DIR/$FNAME"
  exit 0
fi

# --- 已有 aria2c 在跑则不动 ---
if pgrep -f "aria2c.*$FNAME" >/dev/null 2>&1; then
  echo "aria2c 已在运行，跳过启动"
else
  echo "启动 aria2c -c 续传 ..."
  setsid nohup aria2c -c -x 16 -s 16 -k 1M \
    --console-log-level=notice --summary-interval=60 \
    --max-tries=0 --retry-wait=5 \
    --dir="$TE_DIR" -o "$FNAME" "$URL" > "$LOG" 2>&1 &
  sleep 10
fi

echo "--- 进程状态 ---"
pgrep -af "aria2c.*$FNAME" || echo "  警告：aria2c 未在运行"

echo "--- 真实进度（来自 aria2c 日志，非文件大小）---"
tr '\r' '\n' < "$LOG" 2>/dev/null | grep -aE 'CN:[0-9]+' | tail -1 || echo "  (日志暂无进度行)"

echo "--- 完成判定 ---"
if [ -f "$TE_DIR/$FNAME.aria2" ]; then
  echo "  未完成（.aria2 控制文件存在）"
else
  echo "  已完成（.aria2 已消失）"
fi
