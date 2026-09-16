#!/usr/bin/env bash
# ============================================================
# 09_recon_upgrade.sh —— 升级前侦察（只读，不改任何东西）
#
# 目的：在动 ComfyUI 之前搞清楚破坏面与回退手段。
#   R2 风险（环境不可复现）是本项目登记的高风险项，
#   升级核心引擎必须先证明「能原样退回去」。
# ============================================================
set -uo pipefail
cd /root/ComfyUI

echo "############ 1. 当前版本与回退点 ############"
echo "HEAD: $(git rev-parse HEAD)"
git describe --tags 2>&1
echo "v0.3.75 tag 是否存在（回退锚点）:"
git rev-parse v0.3.75 2>&1

echo
echo "############ 2. 本地改动明细（决定 checkout 是否会冲突）############"
echo "-- 已暂存/未暂存改动 --"
git status --porcelain | awk '{print $1}' | sort | uniq -c
echo "-- 是否有非删除类改动 --"
git status --porcelain | grep -vE '^ ?D ' | head -20 || echo "  无（全部是删除，属于 models 迁移的预期结果）"

echo
echo "############ 3. custom_nodes 是否为独立 git 仓库 ############"
total=0; gitrepo=0
for d in custom_nodes/*/; do
  [ -d "$d" ] || continue
  total=$((total+1))
  if [ -d "$d/.git" ]; then gitrepo=$((gitrepo+1)); fi
done
echo "  custom_nodes 目录数: $total"
echo "  其中独立 git 仓库: $gitrepo"
echo "-- 前 40 个节点名 --"
ls custom_nodes | head -40

echo
echo "############ 4. 依赖差异（当前 requirements vs 目标版本）############"
echo "-- 当前 requirements.txt 行数 --"
wc -l < requirements.txt
echo "-- 当前 pip 版本快照存到数据盘 --"
/root/miniconda3/bin/pip freeze > /root/autodl-tmp/pip_freeze_before_v036.txt 2>&1
wc -l < /root/autodl-tmp/pip_freeze_before_v036.txt
echo "-- 关键包版本 --"
/root/miniconda3/bin/pip list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|numpy|transformers|safetensors|comfyui-frontend|aiohttp|pydantic|sqlalchemy) ' 

echo
echo "############ 5. 资源余量 ############"
df -h / /root/autodl-tmp | tail -3
echo "-- 内存 --"
free -g | head -2
echo "-- CPU --"
nproc

echo
echo "############ 6. ComfyUI 仓库体积（fetch 代价预估）############"
du -sh .git 2>/dev/null
du -sh --exclude=.git . 2>/dev/null
