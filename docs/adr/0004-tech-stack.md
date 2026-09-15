# ADR-004 技术栈选型

- 日期：2026-09-15
- 状态：已定（前端框架在 P2-10 前仍可调整）

## 背景

ComfyUI 生态以 Python 为主，适配层必须用 Python；上层平台需要独立于 ComfyUI，
避免其 GPL-3.0 与依赖环境污染主服务（见 ADR-002）。

## 决策

| 层 | 选型 | 版本 |
|---|---|---|
| 前端 | React + TypeScript + Vite + Ant Design | React 18 / Vite 5 / antd 5 |
| 后端 | FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic | Python 3.10+ |
| 任务队列 | Celery + Redis | Celery 5 |
| 数据库 | PostgreSQL | 15+ |
| 对象存储 | MinIO（S3 兼容） | latest |
| 监控 | Prometheus + Grafana + Loki | — |
| 产品埋点 | 先落 PostgreSQL 表，量大再迁 ClickHouse | — |
| 引擎 | ComfyUI（独立进程，HTTP + WebSocket），**不 fork** | — |
| 容器 | Docker + docker-compose | — |
| 包管理 | uv（Python）、pnpm（前端） | — |

## 备选方案与取舍

| 项 | 选定 | 备选 | 理由 |
|---|---|---|---|
| 后端框架 | FastAPI | Django / Flask | 原生异步、自动生成 OpenAPI（对外 API 是 V2 目标）、与 AI 生态一致 |
| 任务队列 | Celery | RQ / Dramatiq / 自研 | 生态成熟、支持优先级队列与重试策略；自研不划算 |
| 数据库 | PostgreSQL | MySQL / SQLite | JSONB 存参数与元数据、窗口函数便于做良品率统计 |
| 对象存储 | MinIO | 云 OSS / 本地磁盘 | 自建零成本、S3 协议可平滑迁移到云；本地磁盘不利于水平扩展 |
| 前端框架 | React + antd | Vue3 + Element Plus | 二者皆可；antd 在中文中后台场景组件最全，省前端时间（关键：我是单人开发） |
| 前端状态 | React Query + Zustand | Redux | 本项目的核心状态是「服务端数据」，React Query 更贴合 |
| 监控 | Prometheus 全家桶 | 云监控 / 自研 | 开源标准、可离线部署（V2 私有化需求） |

## 关键设计约束

1. **ComfyUI 与主服务进程隔离** —— 二者独立部署、独立依赖，只通过网络通信。
   好处：ComfyUI 崩溃不拖垮平台；ComfyUI 的 GPL 依赖不污染平台代码；可水平扩展多实例。
2. **工作流以 API 格式 JSON 存库**，参数通过占位符注入，前端表单由参数 Schema 自动生成
   （P7-02）—— 新增工作流不改前端代码。
3. **所有配置外置**（`.env` + 环境变量），不写死路径 —— 为 V2 私有化部署预留。
4. **数据模型预留 `tenant_id` 与 `role`** —— 为 V2 多租户预留，V1 只有单租户默认值。

## 代价与风险

- React + antd 对单人开发者仍需较多前端工作量（风险 R7），靠动态表单缓解。
- Celery 引入运维复杂度，但相比自研队列仍是最优解。
- Prometheus 全家桶在单机演示环境偏重；V1 只启用必要指标，不做完整告警体系。

## 复核时机

- P2-10（前端骨架）前：最终确认 React vs Vue3，一旦开始写页面不再更换。
- P6-02（任务队列）前：确认 Celery，若复杂度过高降级为 RQ。
- ADR-005（底模选型）与 ADR-006（放大策略）在 P5 阶段另行决策。
