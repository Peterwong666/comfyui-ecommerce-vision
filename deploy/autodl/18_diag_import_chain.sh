#!/usr/bin/env bash
# ============================================================
# 18_diag_import_chain.sh —— 抓完整导入链，定位 list[int] 从哪来
#
# 前面的诊断被 tail 截断了 traceback，没看到真正的调用链。
# 本脚本只做一件事：把 import comfy.sd 的完整调用链打出来，
# 并查清 comfy_kitchen 后端是按需导入还是无条件导入。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python

echo "############ 1. 完整 traceback（不截断）############"
$PY -c "
import sys, traceback
sys.path.insert(0, '/root/ComfyUI')
try:
    import comfy.sd
    print('=== IMPORT OK ===')
except Exception:
    traceback.print_exc()
" 2>&1 | head -45

echo
echo "############ 2. comfy_kitchen 版本与后端导入逻辑 ############"
$PY -c "import comfy_kitchen; print('version:', getattr(comfy_kitchen,'__version__','?')); print('file:', comfy_kitchen.__file__)" 2>&1 | head -5
echo "--- backends/__init__.py ---"
cat /root/miniconda3/lib/python3.12/site-packages/comfy_kitchen/backends/__init__.py 2>/dev/null | head -60

echo
echo "############ 3. 新装包的实际版本（dist-info）############"
for p in comfy_kitchen comfy_aimdo; do
  echo "--- $p ---"
  ls -d /root/miniconda3/lib/python3.12/site-packages/${p}*.dist-info 2>/dev/null
  grep -iE '^(Name|Version|Requires-Dist):' /root/miniconda3/lib/python3.12/site-packages/${p}-*.dist-info/METADATA 2>/dev/null | head -12
done

echo
echo "############ 4. hip 后端里 list[int] 的上下文 ############"
sed -n '1575,1605p' /root/miniconda3/lib/python3.12/site-packages/comfy_kitchen/backends/hip/__init__.py 2>/dev/null

echo
echo "############ 5. torch 2.5.1 的 infer_schema 支持类型清单 ############"
$PY -c "
import torch
from torch._library.infer_schema import SUPPORTED_PARAM_TYPES
print('torch', torch.__version__)
ks = sorted(str(k) for k in SUPPORTED_PARAM_TYPES.keys())
for k in ks:
    print('   ', k)
" 2>&1 | head -40

echo
echo "DIAG2_DONE"
