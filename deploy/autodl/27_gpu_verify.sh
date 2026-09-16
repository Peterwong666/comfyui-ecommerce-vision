#!/usr/bin/env bash
# ============================================================
# 27_gpu_verify.sh —— 升级后的 GPU 侧一次过验证（切回有卡后执行）
#
# 前置（均已在无卡模式完成，不占 GPU）：
#   - ComfyUI 已切到 v0.36.0，依赖与 torch 2.13.0+cu130 已装
#   - 两个工作流已通过 26_validate_workflow.py 静态校验
#   - 节点生态已审计（2530 节点；仅 nunchaku/TeaCache/smZNodes 不可用）
#
# 本脚本要回答 5 个问题：
#   ① torch 能否真正用上 GPU（升级是否成功）
#   ② ComfyUI 能否在 GPU 模式启动
#   ③ SDXL 是否回归（对比升级前 P95 5.07s / median 4.60s）
#   ④ FLUX.2 klein 能否出图（整个升级的最终目的）
#   ⑤ 两者 P95 各是多少（回填 PRD NFR-1）
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
PYT=/root/autodl-tmp
RD=/root/autodl-tmp
PORT=8188

report() { echo; echo "==================== $* ===================="; }

report "0. GPU 与驱动"
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv 2>&1 | head -3
$PY -c "
import torch
print(f'torch={torch.__version__}  torch.version.cuda={torch.version.cuda}')
print(f'cuda.is_available()={torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'device={torch.cuda.get_device_name(0)}  sm={torch.cuda.get_device_capability(0)}')
    x = torch.randn(2048, 2048, device='cuda'); y = (x @ x).sum().item()
    print(f'GPU 矩阵乘自测 OK  (sum={y:.1f})')
" 2>&1 | tail -8

if ! $PY -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"; then
  echo "!! torch 用不上 GPU —— 升级校验未通过，中止后续步骤"
  exit 1
fi

report "1. 启动 ComfyUI（GPU 模式，端口 $PORT）"
bash /root/03_start_comfyui.sh 2>&1 | tail -6

report "2. SDXL 防回归冒烟（升级前基线: P95 5.07s / median 4.60s）"
$PY $PYT/04_api_smoke_test.py $PYT/t2i_v1.json $RD/verify_out/sdxl 2>&1 | tail -8
echo
echo "-- SDXL P95 基准（N=12, warmup=1, 30 步）--"
$PY $PYT/07_bench_workflow.py $PYT/t2i_v1.json -n 12 --warmup 1 --steps 30 \
    --outdir $RD/verify_out/sdxl_bench 2>&1 | tail -16

report "3. FLUX.2 klein 蒸馏版冒烟（本次升级的最终目的）"
$PY $PYT/04_api_smoke_test.py $PYT/flux2_klein_t2i_v1.json $RD/verify_out/flux2 2>&1 | tail -10
echo
echo "-- FLUX.2 klein P95 基准（N=12, warmup=1, 4 步）--"
$PY $PYT/07_bench_workflow.py $PYT/flux2_klein_t2i_v1.json -n 12 --warmup 1 --steps 4 \
    --outdir $RD/verify_out/flux2_bench 2>&1 | tail -16

report "4. 显存占用快照"
nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv 2>&1 | head -3

report "5. 产物清单"
find $RD/verify_out -type f -name '*.png' 2>/dev/null | head -20
echo
echo "GPU_VERIFY_DONE"
