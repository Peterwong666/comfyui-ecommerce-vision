#!/usr/bin/env bash
# 本地 pre-push 钩子（可选）。
# 启用方式：
#   chmod +x scripts/pre-push.sh
#   ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push
#
# 本钩子只跑零 GPU 依赖的离线门禁；不替代 CI，也不跑 golden set 真实出图。

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check（仓库根口径）"
backend/.venv/bin/ruff check .

echo "==> 后端 pytest"
cd backend && .venv/bin/python -m pytest -q && cd ..

echo "==> engine pytest"
backend/.venv/bin/python -m pytest engine/tests -q

echo "==> 缺陷知识库机械校验"
backend/.venv/bin/python -m engine.tools.check_defect_kb

echo "==> golden set 可执行性校验"
backend/.venv/bin/python -m engine.tools.check_golden_set

echo "==> 离线门禁全绿，允许 push"
