# Web 前端（P2-10 / P7-02）

React 18 + TypeScript + Vite 5 + Ant Design 5 · 状态：**P2-10 骨架 + P7-02 动态表单引擎**

> 技术选型依据：`docs/adr/0004-tech-stack.md`（React vs Vue3 的逃生舱已在 P2-10 关闭）。
> 跨流接口契约：`docs/sop/contracts.md`（**与代码冲突时以该文档为准**）。

---

## 1. 快速开始

前置：Node ≥ 20（本机 v20.20.2）、`corepack`（随 Node 提供）。

```bash
cd web
corepack pnpm install          # 首次约 1–2 分钟
corepack pnpm dev              # http://localhost:5173
```

后端要单独起，见 `docs/sop/runbook.md`。**前端默认连 `http://127.0.0.1:8000`**。

### 端口说明（本机特有）

本机 **8000 已被另一个项目占用**（ToyVerse Cloud），所以本地开发时：

```bash
# 后端换端口
cd backend && DATABASE_URL='sqlite+pysqlite:///./dev.db' \
  .venv/bin/python -m uvicorn app.main:app --port 8010

# 前端指向它（.env.local 已被 .gitignore 排除，不会误提交）
echo 'VITE_API_BASE_URL=http://127.0.0.1:8010' > web/.env.local
```

⚠️ Vite 配了 `strictPort: true`：5173 被占时**直接起不来**，而不是自动换端口。
原因是 `backend/app/main.py` 的 CORS 白名单只放行 5173/5174 ——
自动换端口会变成"能启动但请求全被 CORS 拦"，那种错很难定位。

---

## 2. 本地无 PG 怎么跑（SQLite）

`backend/` 默认连 PostgreSQL，但本机没有 PG。用 SQLite 跑本地 UI 联调：

```bash
cd backend
export DATABASE_URL='sqlite+pysqlite:///./dev.db'
.venv/bin/python -m app.cli.init_db          # 建表（仅 SQLite，非 SQLite 会被拒绝）
.venv/bin/python -m app.cli.seed_workflows   # 把 registry.yaml 灌进 workflows 表
.venv/bin/python -m uvicorn app.main:app --port 8010
```

`seed_workflows` 支持 `--dry-run`、`--json`、`--include-disabled`
（最后一个**只允许在 SQLite 上用**）。

> ⚠️ **SQLite 不能替代 PG 验证**：JSONB 路径/包含查询、窗口函数、真并发、
> 部分唯一索引的语义都不会被行使。这一条在 `init_db` 的输出里也会打印。

---

## 3. 命令

| 命令 | 作用 |
|---|---|
| `corepack pnpm dev` | 开发服务器（5173，strictPort） |
| `corepack pnpm build` | `tsc -b && vite build` → `dist/` |
| `corepack pnpm typecheck` | 仅类型检查 |
| `corepack pnpm test` | Vitest（`--run` 进 CI 模式） |
| `corepack pnpm lint` | ESLint |
| `corepack pnpm format` | Prettier 写入 |

---

## 4. 目录

```
src/
├── api/                 # 契约层：与后端逐字对齐，不做驼峰转换
│   ├── enums.ts         #   冻结枚举镜像（后端 enums.py/event.py 的镜像）
│   ├── types.ts         #   响应/请求类型（从 Pydantic Schema 抄来）
│   ├── errors.ts        #   三种错误信封 → ApiError
│   ├── http.ts          #   fetch 底座（401 清会话、402 按状态码判断）
│   ├── client.ts        #   唯一允许拼路径的地方
│   ├── hooks.ts         #   React Query 查询（含任务轮询）
│   └── keys.ts          #   query key 工厂
├── features/workflow-form/   # ★ P7-02 动态表单引擎
│   ├── types.ts              #   ParamSchema 类型（契约 §3）
│   ├── controlRegistry.tsx   #   类型 → 控件 注册表（**新增工作流不改代码的落脚点**）
│   ├── defaults.ts           #   默认值与分组
│   ├── validate.ts           #   客户端校验
│   ├── promptGuards.ts       #   klein 严禁权重语法（按工作流名）
│   ├── WorkflowForm.tsx      #   主组件（从不 switch(field.type)）
│   └── controls/             #   9 种控件 + FallbackControl
├── layout/AppShell.tsx  # 顶栏 56 + 侧栏 200 + 主区 1280（线框图 §0.1）
├── pages/               # 登录 / 工作台（已实现）+ 5 个占位页
├── components/          # StatusBadge 等
├── stores/              # zustand（仅客户端状态）
└── test/                # setup + fixtures
```

### ⚠️ `web/` 内不得出现 Python 配置

根 `pyproject.toml` 与 `backend/pyproject.toml` 各有一份 ruff 配置，
ruff 按"最近祖先"解析 —— 再添第三份会给"哪个配置生效"增加歧义
（`项目进度.md` 纪律 #7）。Node 侧配置只放 `package.json` / `tsconfig*.json` / `eslint.config.js`。

---

## 5. 动态表单引擎（P7-02）的要点

**目标**：新增一条工作流，**前端零改动**。

做法：`WorkflowForm` 从不 `switch (field.type)`，一律查 `controlRegistry`。
后端 `param_schema`（契约 §3）驱动一切。

字段级别的坑（都有测试，见 `__tests__/`）：

| 坑 | 处理 |
|---|---|
| `seed` 没有 min/max，`-1` 表示随机 | 原样保留 `-1`，**由服务端**解析并回写；前端绝不自己随机化 |
| `image` 的 `default: 0` 是"必填占位符" | 初值给 `null`，且**当必填**校验 |
| `mask_expand` 的 `min` 是 `-32` | 不假设 `min >= 0` |
| `int` 不保证有 `step` | 客户端兜底 |
| `advanced` 缺省 = 常显 | 只有显式 `true` 才折叠 |
| `visible_when` 是惰性字段 | **不实现**（真实数据 0 处、服务端无语义） |
| 未知 `type` | 落 `FallbackControl`（只读 + 告警），**不崩** |
| 默认值全量提交会覆盖模板预设 | **只提交改动过的字段** |

### 两层防漂移门禁

1. `backend/tests/test_web_enum_parity.py` —— `enums.ts` 与后端枚举**双向**比对，
   含 `terminal`/`cancellable`/`retryable` 派生集合。
2. `backend/tests/test_web_form_fixtures.py` —— `src/test/fixtures/*.schema.json`
   与 `workflows/registry.yaml` **逐字节**相等。

重新导出夹具（切勿手改）：

```bash
backend/.venv/bin/python - <<'PY'
import json, pathlib
from engine import Registry
out = pathlib.Path("web/src/test/fixtures")
for wid in ("t2i_v1", "flux2_klein_t2i_v1", "inpaint_v1"):
    s = Registry.load().require(wid).raw["param_schema"]
    (out / f"{wid}.schema.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
```

---

## 6. 当前未验证的部分（诚实清单）

本地环境的能力边界，**不要**把这些当成"已跑通"：

| # | 项 | 状态 |
|---|---|---|
| 1 | 任何任务走到 `succeeded` / 重试 / 取消往返 | **待 Redis + ComfyUI + GPU**：没有 Redis 时任务停在 `queued` |
| 2 | 图片上传与 `params.reference_image` | **待 MinIO**：`POST /assets` 依赖对象存储 |
| 3 | `str` / `bool` / `image_list` 控件 | **待真实数据**：注册表里出现 0 次，仅合成夹具覆盖 |
| 4 | 浏览器里的人工走查 | **待人工验证**：已验的是构建、单测、CORS 预检与接口响应 |
| 5 | 生产 PG 上的行为 | **待 PG 验证**：本机无 PG 二进制也无 docker daemon |

后端还有一个**已登记但未修**的缺陷：批量路径不设 `params.reference_image`
（`backend/app/api/v1/tasks.py`），批量跑带参考图的工作流会静默沿用占位值 `0`。
见 `项目进展.md` 的待解决清单。
