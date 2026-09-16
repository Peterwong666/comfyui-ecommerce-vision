#!/usr/bin/env bash
# ============================================================
# 34_bench_recheck.sh —— 稳态基准重测（两条已放行工作流）
#
# 为什么现在重测：
#   L4（渲染路径）刚在真实 GPU 上通过、注册表已放行 status=enabled。
#   放行后需要一份**与当前放行状态对应**的稳态基线，作为 P9 压测与
#   成本模型的对照锚点。
#
# 口径（严格沿用，便于与历史值对比 —— 见 项目进展.md #15/#18）：
#   N=12 · warmup=1（不计入统计）· 每次改 seed 避免引擎缓存整条执行结果
#   上报值含 min（地板）/ median / P95，判读以**地板**为准（共享宿主机
#   每约 3 个样本一次干扰尖峰，升级前的基线里同样存在）
#
# ⚠️ 本脚本必须整体上传后执行，不要内联进 ssh（台账 #3：
#    多层 shell 转义曾静默产生错误的字面量文件）
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
RD=/root/autodl-tmp
OUT=$RD/bench_recheck
PORT=8188

mkdir -p "$OUT"

echo "==================== 前置检查 ===================="
if ! curl -sf --max-time 5 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
  echo "❌ ComfyUI 未运行 —— 请先 bash $RD/31_restart_prod.sh"
  exit 1
fi
echo "ComfyUI 可达"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader

echo
echo "==================== 1. SDXL · 1024² / 30 步 · N=12 warmup=1 ===================="
"$PY" "$RD/07_bench_workflow.py" "$RD/t2i_v1.json" \
  -n 12 --warmup 1 --steps 30 --outdir "$OUT/sdxl" 2>&1 | tail -22

echo
echo "==================== 2. FLUX.2 klein · 1024² / 4 步 · N=12 warmup=1 ===================="
"$PY" "$RD/07_bench_workflow.py" "$RD/flux2_klein_t2i_v1.json" \
  -n 12 --warmup 1 --steps 4 --outdir "$OUT/klein" 2>&1 | tail -22

echo
echo "==================== 3. 显存快照 ===================="
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

echo
echo BENCH_DONE
