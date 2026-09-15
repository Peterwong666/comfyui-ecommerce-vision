#!/usr/bin/env bash
# ============================================================
# watch_flux2_download.sh —— 轮询远端 FLUX.2 klein 下载状态
#
# 用途：配合后台 Monitor 使用，下载全部完成时输出一行信号，
#       用于「需要 GPU 时通知用户」的自动化提醒。
#
# 判断依据（重要，别再用 ls -la 的大小判断）：
#   aria2c 用了 -k 1M 预分配，文件大小一开始就是最终大小，
#   唯一可靠信号是 .aria2 控制文件消失 + 日志出现 Download complete。
#
# 用法：SSHPASS='xxx' bash watch_flux2_download.sh
# ============================================================
set -uo pipefail

: "${SSHPASS:?需要设置 SSHPASS 环境变量}"
HOST="${HOST:-connect.westc.seetacloud.com}"
PORT="${PORT:-22910}"
RUSER="${RUSER:-root}"
MODELS="${MODELS:-/root/autodl-tmp/comfyui-data/models}"
MAX="${MAX:-180}"   # 最多轮询次数（每次 60s → 默认 3 小时）

for i in $(seq 1 "$MAX"); do
  n=$(sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
        -p "$PORT" "$RUSER@$HOST" \
        "ls $MODELS/unet/*.aria2 $MODELS/text_encoders/*.aria2 2>/dev/null | wc -l" \
        2>/dev/null || echo "ERR")

  if [ "$n" = "0" ]; then
    echo "FLUX2_DOWNLOADS_COMPLETE —— unet + text_encoder 下载完成，现在需要 GPU 跑冒烟测试"
    exit 0
  fi

  if [ "$n" = "ERR" ]; then
    echo "WATCH_WARN 第 ${i} 次连接失败，继续重试"
  fi
  sleep 60
done

echo "WATCH_TIMEOUT 超过 ${MAX} 分钟仍未完成，请手工检查"
