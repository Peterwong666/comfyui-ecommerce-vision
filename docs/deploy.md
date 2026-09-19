# 部署文档（P11-02）

> 目标：让有 Linux + Docker 的环境能在 **30 分钟**内把中间件与本项目后端跑起来。
> 真实出图仍需要 autoDL / GPU 服务器上的 ComfyUI，见 [`docs/sop/quickstart.md`](./sop/quickstart.md)。

---

## 1. 最低硬件要求

| 组件 | 最低配置 | 说明 |
|---|---|---|
| 本仓库后端 | 2 核 / 4G 内存 / 10G 磁盘 | 纯 CPU 服务，不加载模型 |
| PostgreSQL | 与后端同机即可 | 生产建议独立 |
| Redis | 与后端同机即可 | 生产建议独立 |
| MinIO | 20G 磁盘起步 | 产物会占空间 |
| ComfyUI | RTX 4090 24G 或同级 | 见 quickstart.md；**必须与本项目后端同机或内网可达** |

---

## 2. 中间件一键启动

```bash
# 1. 启动 PG / Redis / MinIO
docker compose up -d

# 2. 确认健康
sleep 10
docker compose ps

# 3. 配置环境变量
cp .env.example .env
# 默认值已指向 127.0.0.1:5432 / :6379 / :9000，通常无需修改
```

> ⚠️ `.env` 文件**不能提交**（已在 `.gitignore` 中）。

---

## 3. 后端启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 建表并灌入工作流注册表
export DATABASE_URL="postgresql+psycopg://platform:platform@127.0.0.1:5432/platform"
python -m app.cli.init_db
python -m app.cli.seed_workflows

# 启动 API
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：

```bash
curl http://127.0.0.1:8000/api/v1/workflows
```

应返回注册表中 `status: enabled` 的工作流列表。

---

## 4. 前端启动（可选）

```bash
cd web
corepack pnpm install
corepack pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。

---

## 5. 常见坑

| 现象 | 根因 | 处理 |
|---|---|---|
| `init_db` 报 `database does not exist` | compose 还没跑完健康检查 | `docker compose ps` 等 `healthy` |
| MinIO 桶不存在 | `seed_workflows` 不建桶 | 手动登录 `http://127.0.0.1:9001` 建 `uploads` / `outputs`，或见 `backend/app/services/storage.py` |
| 后端连不上 ComfyUI | ComfyUI 未启动或不在同一网络 | 确认 `COMFYUI_BASE_URL` 可达；ComfyUI 必须监听 `0.0.0.0` 或同机 `127.0.0.1` |
| `.env` 改了不生效 | pydantic-settings 缓存 | 重启 uvicorn 进程 |
| 端口冲突 | 本机已有 PG/Redis/MinIO | 改 `docker-compose.yml` 端口映射，并同步改 `.env` |

---

## 6. 生产与 GPU 环境

生产部署需要：

1. **ComfyUI 有卡实例**：autoDL 选型见 `docs/sop/quickstart.md` §1。
2. **模型权重**：按 `deploy/versions.lock` 与 `docs/sop/model_guide.md` 准备，注意商用许可。
3. **对象存储**：生产用 S3/MinIO 集群，禁用默认 `minioadmin`。
4. **数据库**：PG 主从 + 定时备份。
5. **Worker 启动**：必须带 `-Q maintenance`，否则清理任务永不执行（风险 R14）。

---

## 7. 与本项目相关的关键文件

- `docker-compose.yml`：本文件对应的编排
- `.env.example`：全部可配置项与默认值
- `backend/app/core/config.py`：`Settings` 字段定义
- `backend/app/cli/init_db.py`：建表
- `backend/app/cli/seed_workflows.py`：registry.yaml → workflows 表
- `deploy/versions.lock`：ComfyUI / 节点 / pip 版本锚点
