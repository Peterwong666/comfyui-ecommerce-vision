#!/usr/bin/env bash
# ============================================================
# watch_flux2_full_te.sh —— 轮询「完整版」FLUX.2 文本编码器下载
#
# 背景：fp4 版 qwen_3_4b_fp4_flux2.safetensors 与该 ComfyUI(v0.3.75)
#       不兼容（hidden 1280 vs 期望 2560），需改用全精度
#       qwen_3_4b.safetensors（hidden 2560，已校验）。
#
# 完成判定：.aria2 控制文件消失（aria2c 仅在成功完成时删除它）。
#   —— 注意：aria2c -k 1M 预分配会让 stat 看到的文件大小恒为最终值，
#      因此绝不能用「文件大小」判断进度/完成，只能用 .aria2 是否存在。
# 健壮性：若 aria2c 进程不在但 .aria2 还在（如切换无卡模式被杀），
#         自动用 -c 续传重启。
# 用法：SSHPASS='xxx' bash watch_flux2_full_te.sh
# ============================================================
set -uo pipefail
: "${SSHPASS:?需要设置 SSHPASS 环境变量}"
HOST="${HOST:-connect.westc.seetacloud.com}"
PORT="${PORT:-22910}"
RUSER="${RUSER:-root}"
TE_DIR="/root/autodl-tmp/comfyui-data/models/text_encoders"
FNAME="qwen_3_4b.safetensors"
URL="https://hf-mirror.com/Comfy-Org/flux2-klein-4B/resolve/main/split_files/text_encoders/${FNAME}"
MAX="${MAX:-90}"        # 最多轮询次数（每次 ~60s → 默认 90 分钟）

poll() {
  sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "$PORT" "$RUSER@$HOST" \
    "F='$TE_DIR/$FNAME'; A=\$(ls \"\$F\".aria2 >/dev/null 2>&1 && echo YES || echo NO); \
     P=\$(pgrep -f 'aria2c.*$FNAME' >/dev/null 2>&1 && echo RUN || echo STOP); \
     echo \"\$A \$P\"" 2>/dev/null || echo "SSH_FAIL"
}

restart() {
  echo "  [$(date +%H:%M:%S)] 重启 aria2c -c 续传 ..."
  sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "$PORT" "$RUSER@$HOST" \
    "setsid nohup aria2c -c -x 16 -s 16 -k 1M --console-log-level=notice --summary-interval=60 \
       --dir='$TE_DIR' -o '$FNAME' '$URL' > /root/autodl-tmp/dl_klein_tenc_full.log 2>&1 &" \
    2>/dev/null || echo "  [restart] SSH 失败（实例可能关机中），下一轮重试"
}

for i in $(seq 1 "$MAX"); do
  OUT=$(poll)
  if [ "$OUT" = "SSH_FAIL" ]; then
    echo "poll $i: SSH 不可达（实例关机中？），重试"
    sleep 60
    continue
  fi
  read -r A P <<< "$OUT"
  echo "poll $i: aria2=$A aria2c=$P"

  # 完成：aria2c 成功下载后才会删除 .aria2 控制文件
  if [ "$A" = "NO" ]; then
    echo "FLUX2_FULL_TE_DONE —— 完整文本编码器已就绪，可开 GPU 跑冒烟测试"
    exit 0
  fi

  # 续传重启：.aria2 还在但 aria2c 进程没了（多半是切换无卡模式时被杀）
  if [ "$A" = "YES" ] && [ "$P" = "STOP" ]; then
    restart
    sleep 20
  fi

  sleep 60
done
echo "FLUX2_WATCH_TIMEOUT —— 超过 ${MAX} 分钟仍未完成，请手工检查"
