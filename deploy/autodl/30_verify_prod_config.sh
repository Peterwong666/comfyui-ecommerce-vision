#!/usr/bin/env bash
# ============================================================
# 30_verify_prod_config.sh —— 收口 SDXL 回归 + 选定生产启动配置
#
# 前置结论（29_ab_offload_vram.sh 实测）：
#   v036_default            7.10s  ← 默认即回归
#   --disable-async-offload 7.18s  ← 单关异步卸载无效
#   --disable-async-offload --disable-dynamic-vram  4.92s  ← 回到基线
#   --highvram              4.72s  ← 回到基线
#   => 元凶是 Dynamic VRAM，async offload 与本次回归无关
#
# 本脚本要回答 3 个问题：
#   ① --disable-dynamic-vram 单独使用能否同样修复 SDXL？
#   ② 上述修复是否**牺牲了 FLUX.2 klein 的性能**（klein 才是升级的目的）
#   ③ 哪种配置更适合做 V1 生产默认（显存占用 + 模型切换能力）
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
RD=/root/autodl-tmp
PORT=8188
SDXL_WF=$RD/t2i_v1.json
KLEIN_WF=$RD/flux2_klein_t2i_v1.json
OUT=$RD/prod_out
N=${N:-12}

mkdir -p "$OUT"

stop_comfy() {
  pkill -f "main.py --listen" 2>/dev/null
  for _ in $(seq 1 30); do
    curl -sf --max-time 2 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1 || return 0
    sleep 1
  done
  pkill -9 -f "main.py --listen" 2>/dev/null
  sleep 2
}

start_comfy() {
  cd /root/ComfyUI
  setsid nohup "$PY" main.py --listen 127.0.0.1 --port "$PORT" "$@" \
    > "$RD/comfyui_prod.log" 2>&1 &
  for _ in $(seq 1 60); do
    if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
      echo "  [启动] 就绪"
      grep -aiE "attention|offload|DynamicVRAM" "$RD/comfyui_prod.log" \
        | head -4 | sed 's/^/  [生效] /'
      return 0
    fi
    sleep 3
  done
  echo "  [启动] 超时"; tail -15 "$RD/comfyui_prod.log"; return 1
}

bench() {  # $1=标签 $2=工作流 $3=步数
  echo "  -- $1 基准 N=$N warmup=1 steps=$3 --"
  "$PY" "$RD/07_bench_workflow.py" "$2" -n "$N" --warmup 1 --steps "$3" \
      --outdir "$OUT/$1" 2>&1 | tail -12
}

vram_snapshot() {
  echo "  -- 显存 --"
  nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/  /'
}

run_config() {  # $1=标签, 其余=启动参数
  local label="$1"; shift
  echo
  echo "################################################################"
  echo "###  配置: $label   参数: ${*:-（默认）}"
  echo "################################################################"
  stop_comfy
  start_comfy "$@" || return 1
  bench "${label}_sdxl"  "$SDXL_WF"  30
  bench "${label}_klein" "$KLEIN_WF" 4
  vram_snapshot
}

run_config dynvram_off --disable-dynamic-vram
run_config highvram    --highvram

# ---------- 收尾：以 --disable-dynamic-vram 启动，供用户继续网页测试 ----------
echo
echo "==================== 收尾启动（--disable-dynamic-vram） ===================="
stop_comfy
start_comfy --disable-dynamic-vram
echo
echo "PROD_DONE"
