#!/usr/bin/env bash
# ============================================================
# 24_fix_flash_attn.sh —— 修复 flash-attn ABI 失配导致的连锁导入失败
#
# 现象（见 23 脚本审计）：
#   flash_attn_2_cuda.cpython-312-x86_64-linux-gnu.so:
#   undefined symbol: _ZN3c104cuda29c10_cuda_check_implementationEiPKcS2_ib
#   → 拖垮 comfy_extras/{nodes_latent,nodes_post_processing,nodes_morphology}.py
#     以及 custom_nodes/ComfyUI_essentials
#
# 分析：
#   该 .so 是针对 torch 2.5.1 编译的 C++ 扩展；升级到 torch 2.13 后
#   libtorch 的 C++ ABI 变化，符号对不上。这是 torch 大版本升级的典型连带损伤。
#
# 决策：卸载 flash-attn，而非重编译。
#   理由：① flash-attn 对 ComfyUI 是**可选加速**，缺失时自动回退
#              PyTorch 原生注意力（SDPA），功能不受影响
#         ② 重编译需要 1-3 小时且极易失败，而它带来的收益在 4090 单卡
#            出图场景下有限
#         ③ 保留核心 comfy_extras 模块可用，比保住 flash-attn 重要得多
#
# 用法：bash 24_fix_flash_attn.sh
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

echo "############ 1. 现状：谁装了 flash-attn ############"
$PIP list 2>/dev/null | grep -iE 'flash|attn|xformers' || echo "  (pip list 里未直接列出)"
echo "-- site-packages 中的 .so --"
ls -la /root/miniconda3/lib/python3.12/site-packages/flash_attn* 2>/dev/null | head -10
ls -la /root/miniconda3/lib/python3.12/site-packages/*.dist-info 2>/dev/null | grep -i flash

echo
echo "############ 2. 确认 .so 的符号缺失确实是 ABI 问题 ############"
SO=$(ls /root/miniconda3/lib/python3.12/site-packages/flash_attn_2_cuda*.so 2>/dev/null | head -1)
if [ -n "$SO" ]; then
  echo "  文件: $SO"
  echo "  -- 尝试解析该符号 --"
  nm -D "$SO" 2>/dev/null | grep -c 'c10_cuda_check_implementation' || echo "  未找到该符号"
  echo "  -- 当前 torch 里是否有这个符号 --"
  find /root/miniconda3/lib/python3.12/site-packages/torch/lib -name 'libc10_cuda.so' 2>/dev/null | head -2
else
  echo "  未找到 flash_attn_2_cuda*.so"
fi

echo
echo "############ 3. 卸载 flash-attn ############"
$PIP uninstall -y flash-attn flash_attn 2>&1 | tail -6
# 兜底：直接清掉残留 .so（pip 有时留孤儿文件）
rm -f /root/miniconda3/lib/python3.12/site-packages/flash_attn_2_cuda*.so 2>/dev/null
rm -rf /root/miniconda3/lib/python3.12/site-packages/flash_attn 2>/dev/null
echo "-- 清理后 --"
ls /root/miniconda3/lib/python3.12/site-packages/ 2>/dev/null | grep -i flash || echo "  已无 flash_attn 残留"

echo
echo "############ 4. 验证：受影响的模块能否导入了 ############"
$PY - <<'PY'
import sys
sys.path.insert(0, '/root/ComfyUI')
mods = ['comfy.quant_ops',
        'comfy_extras.nodes_latent',
        'comfy_extras.nodes_post_processing',
        'comfy_extras.nodes_morphology']
ok = True
for m in mods:
    try:
        __import__(m)
        print(f"  [OK]   {m}")
    except Exception as e:
        ok = False
        print(f"  [FAIL] {m}: {type(e).__name__}: {str(e)[:160]}")
print("FLASH_FIX_CHECK=" + ("PASS" if ok else "FAIL"))
PY

echo
echo "############ 5. 顺带：清理 \$LOCK 残留（09-15 shell 转义事故产物）############"
if [ -e 'custom_nodes/$LOCK' ]; then
  echo "  类型: $(stat -c '%F' 'custom_nodes/$LOCK')"
  ls -la 'custom_nodes/$LOCK' 2>/dev/null | head -5
  rm -rf 'custom_nodes/$LOCK'
  echo "  已删除"
else
  echo "  未找到 custom_nodes/\$LOCK（注意：目录列表里出现过，可能是文件而非目录）"
  find custom_nodes -maxdepth 1 -name '*LOCK*' -print 2>/dev/null
fi

echo
echo "############ 6. custom_nodes 里的其它垃圾文件 ############"
for f in install_errors.log requirements_content.log install_requireements.sh skip_download_model example_node.py.example websocket_image_save.py __pycache__; do
  if [ -e "custom_nodes/$f" ]; then echo "  存在: custom_nodes/$f"; fi
done

echo
echo "FLASH_ATTN_FIX_DONE"
