#!/usr/bin/env bash
# ============================================================
# 13_install_deps.sh —— 安装目标版本的 Python 依赖（无卡模式，带内存/磁盘护栏）
#
# 无卡模式约束：cgroup memory.max=2GB、cpu.max=0.5 核、系统盘仅剩约 15G。
# 故：
#   - PIP_NO_CACHE_DIR=1  不落 pip 缓存（缓存动辄数百 MB，系统盘吃不消）
#   - 先装 requirements，再单独核对关键包（torch 等大件若需升级会单独提示）
#   - 全程日志落数据盘
#
# 用法：bash 13_install_deps.sh [--dry-run]
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip
LOGDIR=/root/autodl-tmp
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

export PIP_NO_CACHE_DIR=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore

echo "############ 0. 环境快照 ############"
echo "python: $($PY -V 2>&1)"
echo "HEAD:   $(git log -1 --format='%h %ad %s' --date=short)"
echo "内存上限: $(cat /sys/fs/cgroup/memory.max 2>/dev/null)  当前: $(awk '{printf "%.0fMB", $1/1048576}' /sys/fs/cgroup/memory.current 2>/dev/null)"
df -h / | tail -1

echo
echo "############ 1. 本次要装的 requirements.txt ############"
cat requirements.txt

echo
echo "############ 2. 开始安装 $DRY ############"
# --extra-index-url https://pypi.org/simple：
#   v0.36.0 发布于「昨天」，其 pin 的 comfyui-workflow-templates==0.11.62
#   尚未同步到阿里云镜像（镜像最高 0.11.60）。用官方源兜底，
#   主源仍是镜像（快），只有镜像缺失的那一个包走官方源（代价极小）。
PIP_EXTRA="${PIP_EXTRA:---extra-index-url https://pypi.org/simple}"
echo "额外索引: $PIP_EXTRA"
$PIP install $DRY $PIP_EXTRA -r requirements.txt 2>&1 | tail -40
rc=${PIPESTATUS[0]}
echo "pip exit=$rc"

if [ "$rc" -ne 0 ] && [ -z "$DRY" ]; then
  echo
  echo "!! 安装失败。可能原因与对策："
  echo "   - 内存不足(OOM)：无卡模式仅 2GB，可考虑切回有卡装依赖"
  echo "   - 编译型包需构建工具：检查 gcc / python-dev"
  echo "   - 网络问题：pip 默认源是否可达（可换清华源 -i https://pypi.tuna.tsinghua.edu.cn/simple）"
  exit 1
fi

echo
echo "############ 3. 依赖变更对比（相对升级前）############"
$PIP freeze > "$LOGDIR/pip_freeze_after_v036.txt" 2>&1
echo "-- 新增/升级的包 --"
diff <(sort "$LOGDIR/pip_freeze_before_v036.txt") <(sort "$LOGDIR/pip_freeze_after_v036.txt") | grep '^>' | head -40
echo "-- 移除/降级的包 --"
diff <(sort "$LOGDIR/pip_freeze_before_v036.txt") <(sort "$LOGDIR/pip_freeze_after_v036.txt") | grep '^<' | head -40

echo
echo "############ 4. 关键包版本 ############"
$PIP list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|numpy|transformers|safetensors|comfyui-frontend-package|comfyui-embedded-docs|aiohttp|pydantic|sqlalchemy|alembic|av) '

echo
echo "############ 5. 导入自检（不启动服务）############"
$PY -c "
import sys
sys.path.insert(0, '/root/ComfyUI')
mods = ['torch','torchvision','comfy.sd','comfy.model_management','comfy_extras.nodes_flux','comfy.text_encoders.flux','comfy.text_encoders.z_image']
ok = True
for m in mods:
    try:
        __import__(m)
        print(f'  [OK]   {m}')
    except Exception as e:
        ok = False
        print(f'  [FAIL] {m}: {type(e).__name__}: {e}')
print('IMPORT_CHECK=' + ('PASS' if ok else 'FAIL'))
" 2>&1 | tail -20

echo
echo "INSTALL_DEPS_DONE"
