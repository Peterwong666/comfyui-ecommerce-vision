#!/usr/bin/env bash
# ============================================================
# 05_download_flux2_klein.sh —— 下载 FLUX.2 [klein] 4B（Apache-2.0，可商用）
# 来源：Comfy-Org/flux2-klein-4B（ComfyUI 官方打包版，未 gated）
# 组件：unet + vae + text encoder（先用 fp4 版，显存友好）
# ============================================================
set -Eeuo pipefail

MIRROR="https://hf-mirror.com"
REPO="Comfy-Org/flux2-klein-4B"
MODELS="${COMFY_MODELS:-/root/autodl-tmp/comfyui-data/models}"
LOGDIR="/root/autodl-tmp"

mkdir -p "$MODELS/unet" "$MODELS/vae" "$MODELS/text_encoders"

dl() {
  local url="$1" dest_dir="$2" out="$3" log="$4"
  setsid nohup aria2c -c -x 16 -s 16 -k 1M \
    --console-log-level=notice --summary-interval=60 \
    --dir="$dest_dir" -o "$out" "$url" > "$log" 2>&1 &
  echo "  启动: $out"
}

echo "下载 FLUX.2 [klein] 4B 组件 -> $MODELS"
dl "$MIRROR/$REPO/resolve/main/split_files/diffusion_models/flux-2-klein-4b.safetensors" \
   "$MODELS/unet" "flux-2-klein-4b.safetensors" "$LOGDIR/dl_klein_unet.log"

dl "$MIRROR/$REPO/resolve/main/split_files/vae/flux2-vae.safetensors" \
   "$MODELS/vae" "flux2-vae.safetensors" "$LOGDIR/dl_klein_vae.log"

dl "$MIRROR/$REPO/resolve/main/split_files/text_encoders/qwen_3_4b_fp4_flux2.safetensors" \
   "$MODELS/text_encoders" "qwen_3_4b_fp4_flux2.safetensors" "$LOGDIR/dl_klein_tenc.log"

sleep 8
echo "进度快照："
for L in dl_klein_unet dl_klein_vae dl_klein_tenc; do
  tr "\r" "\n" < "$LOGDIR/$L.log" 2>/dev/null | grep -a "CN:" | tail -1
done
