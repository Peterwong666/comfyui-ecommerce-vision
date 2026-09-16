#!/usr/bin/env bash
# ============================================================
# 28_try_sage_attention.sh —— 尝试用 SageAttention 追回 SDXL 性能
#
# 背景（见 项目进展.md #17）：
#   升级到 v0.36.0 + torch 2.13 后，flash-attn 因 ABI 失配被卸载，
#   ComfyUI 启动日志显示 "Using pytorch attention"，
#   SDXL 1024²/30 步的 median 从 4.60s 退到 7.26s（约 1.6x 回归）。
#
# 假设：这是注意力后端切换的代价。
# 验证方式：改用 SageAttention（环境里已装 spas_sage_attn 0.1.0），
#           重跑同一基准，与 pytorch attention 的 7.26s 对比。
#
# 失败即回退：脚本结束时会恢复默认注意力设置，不会留下坏状态。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
RD=/root/autodl-tmp
PORT=8188

stop_comfy() {
  pkill -f "main.py.*--port $PORT" 2>/dev/null
  sleep 4
}

report() { echo; echo "==================== $* ===================="; }

report "0. 前置：SageAttention 是否可导入"
$PY -c "
try:
    import spas_sage_attn
    print('  spas_sage_attn 可导入:', getattr(spas_sage_attn,'__version__','?'))
except Exception as e:
    print('  [FAIL] 导入失败:', type(e).__name__, str(e)[:200])
" 2>&1 | tail -5

report "1. 以 SageAttention 重启 ComfyUI"
stop_comfy
setsid nohup "$PY" main.py --listen 127.0.0.1 --port $PORT --use-sage-attention \
  > $RD/comfyui_sage.log 2>&1 &
for i in $(seq 1 40); do
  curl -sf --max-time 3 "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1 && break
  sleep 5
done
if ! curl -sf --max-time 3 "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1; then
  echo "!! SageAttention 模式启动失败，日志尾部："
  tail -25 $RD/comfyui_sage.log
  echo "---- 回退到默认注意力 ----"
  stop_comfy
  setsid nohup "$PY" main.py --listen 127.0.0.1 --port $PORT > $RD/comfyui.log 2>&1 &
  exit 1
fi
echo "启动成功。注意力后端："
grep -aiE 'attention' $RD/comfyui_sage.log | head -5

report "2. SageAttention 下的 SDXL 基准（对比 pytorch attention 的 median 7.26s / P95 7.72s）"
$PY $RD/07_bench_workflow.py $RD/t2i_v1.json -n 12 --warmup 1 --steps 30 \
    --outdir $RD/verify_out/sdxl_sage 2>&1 | tail -16

report "3. 结论"
echo "见上方统计。若 median 明显低于 7.26s，则采用 SageAttention 作为默认后端；"
echo "否则说明回归主因不在注意力后端，需另找原因（记入待解决清单）。"

echo
echo "SAGE_ATTENTION_TEST_DONE"
