#!/usr/bin/env bash
# 环境复现脚本（P2-07）—— CPU 侧一键重建。
#
# 用法：
#   bash scripts/reproduce_env.sh
#
# 本脚本复现：backend venv、前端 pnpm install、SQLite 开发库初始化。
# 不包含：ComfyUI / 模型权重 / GPU 驱动（见 docs/sop/quickstart.md）。
#
# 前置：
#   - Python 3.12
#   - Node.js 20 + corepack
#   - pnpm 已通过 corepack 启用（或会用 `corepack pnpm`）

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "==> 仓库根：$REPO_ROOT"

# ---------- backend ----------
echo "==> 安装后端依赖"
cd "$REPO_ROOT/backend"
if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
pip install -e "$REPO_ROOT[yaml]"

echo "==> 后端 lint / 测试基线"
cd "$REPO_ROOT"
backend/.venv/bin/ruff check .
cd backend
.venv/bin/python -m pytest -q

echo "==> 初始化 SQLite 开发库"
export DATABASE_URL='sqlite+pysqlite:///./dev.db'
.venv/bin/python -m app.cli.init_db
.venv/bin/python -m app.cli.seed_workflows

# ---------- engine ----------
echo "==> engine 自校验"
cd "$REPO_ROOT"
backend/.venv/bin/python -m engine
backend/.venv/bin/python -m engine.tools.check_defect_kb
backend/.venv/bin/python -m engine.tools.check_golden_set

# ---------- frontend ----------
echo "==> 安装前端依赖"
cd "$REPO_ROOT/web"
corepack pnpm install --frozen-lockfile

echo "==> 前端静态检查"
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm exec vitest run
corepack pnpm build

echo "==> CPU 侧环境复现完成"
echo "下一步（可选）："
echo "  1. 起中间件：docker compose up -d"
echo "  2. 改 .env 指向 PG/Redis/MinIO"
echo "  3. 起后端：cd backend && .venv/bin/python -m uvicorn app.main:app --port 8010"
echo "  4. 起前端：cd web && corepack pnpm dev"
echo "  5. GPU 出图：见 docs/sop/quickstart.md"
