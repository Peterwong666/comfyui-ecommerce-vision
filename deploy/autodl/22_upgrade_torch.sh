#!/usr/bin/env bash
# ============================================================
# 22_upgrade_torch.sh —— torch 2.5.1+cu124 → 2.13.0+cu130
#
# 依据（ComfyUI v0.36.0 README:195）：
#   - 最低 torch 2.7
#   - 20 系及以上必须 cu130+
#   - 推荐最新大版本但回避 <2 周发布的
#   → 选 2.13.0（2026-07-08，70 天），回避 2.14.0（距今仅 14 天）
#
# 磁盘策略：先卸载旧 torch + nvidia_cu12 释放约 4.4G，再安装。
#           （若"先装后卸"，峰值占用 ≈ 旧4.4G + 新6G = 10.4G，
#             系统盘仅剩 14G，风险偏高）
#
# 回退命令（install 失败时使用）：
#   /root/miniconda3/bin/pip install \
#     torch==2.5.1+cu124 torchvision==0.20.1+cu124 torchaudio==2.5.1+cu124 \
#     --index-url https://download.pytorch.org/whl/cu124
#
# 用法：setsid nohup bash 22_upgrade_torch.sh > /root/autodl-tmp/torch_upgrade.log 2>&1 &
# ============================================================
set -uo pipefail
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip
IDX="https://download.pytorch.org/whl/cu130"
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

step() { echo; echo "==================== $* ===================="; }

step "0. 前置：磁盘与当前版本"
df -h / | tail -1
$PIP list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio) '

FREE_KB=$(df --output=avail / | tail -1)
FREE_GB=$((FREE_KB / 1048576))
if [ "$FREE_GB" -lt 10 ]; then
  echo "!! 系统盘只剩 ${FREE_GB}G（<10G），为安全起见中止。请先清理或把环境迁到数据盘。"
  exit 1
fi
echo "系统盘可用: ${FREE_GB}G —— 通过前置检查"

step "1. 卸载旧 torch 系（释放磁盘）"
$PIP uninstall -y torch torchvision torchaudio 2>&1 | tail -6
echo "-- 卸载 nvidia cu12 子包 --"
$PIP list 2>/dev/null | grep -iE '^nvidia' | awk '{print $1}' > /tmp/nvidia_pkgs.txt
wc -l < /tmp/nvidia_pkgs.txt
if [ -s /tmp/nvidia_pkgs.txt ]; then
  xargs -a /tmp/nvidia_pkgs.txt $PIP uninstall -y 2>&1 | tail -5
fi
echo "-- 卸载后磁盘 --"
df -h / | tail -1

step "2. 安装 torch 2.13.0+cu130（下载约 3.2GB，耗时较长）"
echo "开始时间: $(date '+%H:%M:%S')"
$PIP install --index-url "$IDX" \
  "torch==2.13.0" torchvision torchaudio 2>&1 | tail -25
rc=${PIPESTATUS[0]}
echo "pip exit=$rc  结束时间: $(date '+%H:%M:%S')"

if [ "$rc" -ne 0 ]; then
  echo
  echo "!! 安装失败。回退命令："
  echo "   $PIP install torch==2.5.1+cu124 torchvision==0.20.1+cu124 torchaudio==2.5.1+cu124 --index-url https://download.pytorch.org/whl/cu124"
  exit 1
fi

step "3. 校验：版本、CUDA 归属、关键算子导入"
$PY - <<'PYEOF'
import sys
sys.path.insert(0, '/root/ComfyUI')
try:
    import torch
    print(f"torch        = {torch.__version__}")
    print(f"torch.version.cuda = {torch.version.cuda}")
    print(f"cuda available     = {torch.cuda.is_available()}  (无卡模式应为 False，属正常)")
    import torchvision, torchaudio
    print(f"torchvision  = {torchvision.__version__}")
    print(f"torchaudio   = {torchaudio.__version__}")
except Exception as e:
    print("torch 基础导入失败:", type(e).__name__, e)
    raise SystemExit(1)

print()
print("--- 关键路径导入（这次要看到全部 OK）---")
mods = ['comfy.sd', 'comfy.model_management', 'comfy.quant_ops',
        'comfy_extras.nodes_flux', 'comfy.text_encoders.flux', 'comfy.text_encoders.z_image']
ok = True
for m in mods:
    try:
        __import__(m)
        print(f"  [OK]   {m}")
    except Exception as e:
        ok = False
        print(f"  [FAIL] {m}: {type(e).__name__}: {str(e)[:200]}")
print("IMPORT_CHECK=" + ("PASS" if ok else "FAIL"))
PYEOF

step "4. 结果"
df -h / | tail -1
$PIP freeze > /root/autodl-tmp/pip_freeze_after_torch.txt 2>&1
echo "-- torch 相关最终版本 --"
$PIP list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|triton|nvidia-cudnn|nvidia-nccl) '
echo
echo "TORCH_UPGRADE_DONE"
