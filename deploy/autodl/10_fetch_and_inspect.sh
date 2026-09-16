#!/usr/bin/env bash
# ============================================================
# 10_fetch_and_inspect.sh —— 拉取目标版本并检查依赖差异（不改工作树）
#
# 只做两件事：
#   ① git fetch origin --tags —— 把 v0.36.0 的提交与对象拉到本地
#   ② 对比 v0.3.75 与 v0.36.0 的 requirements.txt，评估依赖变动代价
# 不碰工作树，因此随时可中断。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
TARGET="${TARGET:-v0.36.0}"

echo "############ 1. fetch ############"
time git fetch origin --tags --prune 2>&1 | tail -15
echo "fetch exit=$?"

echo
echo "############ 2. 目标版本信息 ############"
if git rev-parse "$TARGET" >/dev/null 2>&1; then
  echo "TARGET=$TARGET 存在"
  git log -1 --format='commit=%H%ndate=%ad%nsubject=%s' --date=iso "$TARGET"
else
  echo "!! $TARGET 不存在，可选最新 tag："
  git tag --sort=-v:refname | head -10
  exit 1
fi

echo
echo "############ 3. 提交跨度 ############"
echo "落后提交数: $(git rev-list --count v0.3.75..$TARGET)"
echo "涉及文件数: $(git diff --name-only v0.3.75..$TARGET | wc -l)"
echo "-- 核心目录改动统计 --"
git diff --stat v0.3.75..$TARGET -- comfy comfy_extras | tail -3

echo
echo "############ 4. requirements.txt 依赖差异 ############"
echo "=== v0.3.75 ==="
git show v0.3.75:requirements.txt
echo
echo "=== $TARGET ==="
git show "$TARGET":requirements.txt
echo
echo "=== 差异（< 是旧，> 是新）==="
diff <(git show v0.3.75:requirements.txt) <(git show "$TARGET":requirements.txt) || true

echo
echo "############ 5. pyproject / python 要求 ############"
git show "$TARGET":pyproject.toml 2>/dev/null | grep -iE 'requires-python|version|name' | head -10

echo
echo "############ 6. 版本文件 ############"
git show "$TARGET":comfyui_version.py 2>/dev/null

echo
echo "############ 7. 自定义节点中有无已声明的白名单文件 ############"
ls -la /root/ComfyUI/custom_nodes/\$LOCK 2>&1 | head -5
