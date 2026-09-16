#!/usr/bin/env bash
# ============================================================
# 11_fetch_target.sh —— 拉取目标版本（无卡模式专用，带内存护栏）
#
# 背景：实例已切无卡模式，cgroup 硬限 memory.max=2GB / cpu.max=0.5 核。
#       git fetch 的打包阶段是 CPU + 内存密集型，默认参数有 OOM 风险，
#       故显式压低下述参数（代价是变慢，但无卡模式本来就在意成本不在意速度）。
#
# 用法：nohup bash 11_fetch_target.sh > /root/autodl-tmp/fetch.log 2>&1 &
# ============================================================
set -uo pipefail
cd /root/ComfyUI
TARGET="${TARGET:-v0.36.0}"

# --- 内存护栏 ---
git config pack.threads 1
git config pack.windowMemory 32m
git config core.packedGitLimit 128m
git config core.packedGitWindowSize 16m
git config pack.deltaCacheSize 16m
echo "内存护栏已设置: $(git config --get pack.threads) 线程 / windowMemory $(git config --get pack.windowMemory)"

echo "==================== fetch 开始 $(date '+%H:%M:%S') ===================="

# 先只拉目标 tag 的对象，不拉全量分支历史，减少传输量
git fetch --tags origin "$TARGET" 2>&1
rc=$?
echo "==================== fetch 结束 $(date '+%H:%M:%S') rc=$rc ===================="

if [ "$rc" -ne 0 ]; then
  echo "!! 定向 fetch 失败，退回全量 fetch"
  git fetch origin --tags --prune 2>&1
  rc=$?
  echo "全量 fetch rc=$rc"
fi

echo
echo "############ 目标版本信息 ############"
if git rev-parse "$TARGET" >/dev/null 2>&1; then
  git log -1 --format='TARGET=%s%ncommit=%H%ndate=%ad' --date=iso "$TARGET"
else
  echo "!! $TARGET 仍不存在。最新 10 个 tag："
  git tag --sort=-v:refname | head -10
fi

echo
echo "############ requirements 差异 ############"
diff <(git show v0.3.75:requirements.txt) <(git show "$TARGET":requirements.txt) && echo "  （无差异）"
echo
echo "############ python 要求 ############"
git show "$TARGET":pyproject.toml 2>/dev/null | grep -iE 'requires-python|^version' | head -5
echo
echo "############ 提交跨度 ############"
echo "落后提交数: $(git rev-list --count v0.3.75..$TARGET 2>&1)"
echo "FETCH_ALL_DONE"
