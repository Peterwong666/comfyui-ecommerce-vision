#!/usr/bin/env bash
# ============================================================
# 16_diag_torch_incompat.sh —— 定位 list[int] 注册失败的来源
#
# 现象：import comfy.sd 抛
#   ValueError: infer_schema(func): Parameter stride has unsupported type list[int]
# 需要回答：
#   ① 是 comfy 核心还是 comfy-kitchen / comfy-aimdo 注册的算子？
#   ② 当前 torch 的 infer_schema 到底支持哪些类型？
#   ③ 哪个 torch 版本开始支持 list[int]（PEP 585）？
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python

echo "############ 1. 完整 traceback ############"
$PY -c "
import sys, traceback
sys.path.insert(0, '/root/ComfyUI')
try:
    import comfy.sd
    print('IMPORT OK')
except Exception:
    traceback.print_exc()
" 2>&1 | tail -30

echo
echo "############ 2. 谁的签名里有 stride: list[int] ############"
echo "-- comfy 核心 --"
grep -rn 'stride: list\[int\]' /root/ComfyUI/comfy/ 2>/dev/null | grep -v '.pyc' | head -10
echo "-- comfy_kitchen --"
grep -rn 'stride: list\[int\]' /root/miniconda3/lib/python3.12/site-packages/comfy_kitchen/ 2>/dev/null | grep -v '.pyc' | head -10
echo "-- comfy_aimdo --"
grep -rn 'stride: list\[int\]' /root/miniconda3/lib/python3.12/site-packages/comfy_aimdo/ 2>/dev/null | grep -v '.pyc' | head -10

echo
echo "############ 3. 谁调用了 infer_schema / custom_op ############"
echo "-- comfy 核心里 register/custom_op 相关 --"
grep -rn 'infer_schema\|custom_op\|def register_op' /root/ComfyUI/comfy/ 2>/dev/null | grep -v '.pyc' | head -15

echo
echo "############ 4. 这两个新包是什么 ############"
for p in comfy_kitchen comfy_aimdo comfy_angle; do
  d=/root/miniconda3/lib/python3.12/site-packages/$p
  echo "--- $p ---"
  ls "$d" 2>/dev/null | head -10
  cat "$d"/METADATA 2>/dev/null | grep -iE '^(Name|Version|Requires-Dist|Summary):' | head -12
  echo
done

echo
echo "############ 5. 当前 torch 的 infer_schema 源码位置 ############"
$PY -c "
import torch, inspect
print('torch', torch.__version__)
try:
    from torch._library import infer_schema
    src = inspect.getsource(infer_schema)
    print('源码文件:', inspect.getsourcefile(infer_schema))
    # 找出它认识 list 的判定逻辑
    for i, line in enumerate(src.splitlines()):
        if 'List' in line or 'list' in line:
            print(f'  {i}: {line.strip()}')
except Exception as e:
    print('取源码失败:', e)
" 2>&1 | head -30

echo
echo "DIAG_TORCH_DONE"
