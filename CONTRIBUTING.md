# 贡献指南

本项目当前为个人开发（V1 阶段），此文档用于规范自身的提交与分支纪律，也为后续协作预留。

## 分支策略

| 分支 | 用途 | 规则 |
|---|---|---|
| `main` | 稳定主线，每个可发布节点一个 tag | 只接受来自 `dev` 的 PR |
| `dev` | 开发主线 | 只接受来自 `feat/*` 的 PR |
| `feat/<阶段>-<简述>` | 功能分支 | 如 `feat/p3-t2i-workflow`、`feat/p6-task-queue` |
| `fix/<简述>` | 缺陷修复 | 如 `fix/oom-retry` |
| `docs/<简述>` | 纯文档 | 如 `docs/mrd-v1` |

分支命名使用小写英文 + 连字符，阶段前缀与 `todolist.md` 的 `P{阶段}` 对齐。

## 提交信息规范（Conventional Commits）

```
<type>(<scope>): <subject>

<body>
<footer>
```

**type**

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `perf` | 性能优化 |
| `refactor` | 重构（无功能变化） |
| `docs` | 文档 |
| `test` | 测试 |
| `chore` | 构建/依赖/工具 |
| `workflow` | 工作流 JSON 变更（本项目特有） |
| `model` | 模型 registry 变更（本项目特有） |

**scope**：`engine` / `server` / `web` / `workflows` / `docs` / `deploy` / `tests`

**示例**

```
feat(engine): 实现 ComfyUI 任务提交与 WebSocket 进度监听

- 支持 /prompt 提交与 client_id 关联
- WebSocket 断线自动重连
- 任务超时默认 600s，可配置

Closes P6-01
```

**规则**
- subject 用祈使句，不超过 72 字符，结尾不加句号
- 中文或英文均可，但同一提交内保持一致
- 关联任务时引用 `todolist.md` 的任务编号（如 `Closes P6-01`）

## 提交前自检

先跑这几条（与 `.github/workflows/ci.yml` 里的命令逐条对应，即**命令等价** ——
本地跑通这些命令，可预期 CI 不会因命令写错而失败。但 **CI 本身尚未运行过**
（仓库无远端），且它跑在干净环境里：pip/pnpm 全新安装、不同解释器、无 `.venv`，
还受 action 版本与缓存命中影响 —— 所以**本地绿 ≠ CI 绿**）：

```bash
# 除特别标注外，都在**仓库根**执行
backend/.venv/bin/ruff check .          # 必须在仓库根跑：根目录与 backend/ 各有一份
                                        # pyproject.toml，换了 CWD 结论就会变
backend/.venv/bin/python -m pytest engine/tests -q
(cd backend && .venv/bin/python -m pytest -q)      # 后端全量（含防漂移 + 密钥/卫生门禁）
(cd web && corepack pnpm typecheck && corepack pnpm lint \
        && corepack pnpm test --run && corepack pnpm build)
```

其中密钥与卫生那一道是纯 pytest（`backend/tests/test_repo_hygiene.py`），它会拦住：
被追踪的 `.env` / 权重 / 私钥文件、文本里形似真密钥的字符串、`.gitignore` 关键规则被删
或被去掉根锚定、>5 MiB 的误提交文件。注意它问的是**追踪状态**：

- `.gitignore` **不是防线** —— `git add -f` 能绕过，且已被追踪的文件不再受它约束；
- 万一密钥真的进过库，正确处置是**吊销并轮换**，不是把文件删掉（历史里还在）。

- [ ] 上述命令全绿
- [ ] 未提交模型权重（`.safetensors` / `.ckpt` 等，由卫生门禁 R2 拦截）
- [ ] 未提交密钥（`.env`、token、内网地址，由卫生门禁 R1/R3/R4 拦截）
- [ ] 新增模型已登记到 `model_registry` 并核对商用许可
- [ ] 相关文档已同步更新
- [ ] `todolist.md` 中对应任务已打勾

## 工作流与模型变更的额外要求

- **工作流变更**：必须更新版本号与变更日志，并跑一遍 golden set 回归（见 P8-08）
- **模型变更**：必须登记 hash、来源、许可证、评测分；禁止引入「许可未知」的模型

## 代码风格

- Python：`ruff` + `black`，类型标注尽量完整
- 前端：ESLint + Prettier
- 文档：中文为主，术语保留英文原文
