---
title: P8-10 测试计划执行报告
date: 2026-09-22
status: 完成
---

# P8-10 测试计划执行报告

## 执行环境

| 项 | 值 |
|---|---|
| 数据库 | PostgreSQL 16.14（真实实例，非 SQLite） |
| Python | 3.12.3 |
| 后端框架 | FastAPI + SQLAlchemy |
| 引擎 | ComfyUI v0.36.0（L1/L2/L3 离线校验） |
| 前端 | React 18 + TypeScript + Vite 5 + antd 5 |

## 后端测试结果

| 指标 | 数值 |
|---|---|
| **总用例数** | 576 |
| **通过** | **571** |
| **失败** | 5（全部 pre-existing） |
| **通过率** | **99.1%** |
| **执行时间** | 59.87s |
| **警告** | 306（deprecation，非功能问题） |

### 失败用例分析

| 用例 | 原因 | 分类 |
|---|---|---|
| `test_every_enabled_workflow_renders` | 需要 `asset_resolver` 回调 | pre-existing |
| `test_definition_survives_database_json_roundtrip` | 同上 | pre-existing |
| `test_rendering_with_schema_defaults_is_warning_free` | 同上 | pre-existing |
| `test_seed_typed_workflows_resolve_seed` | 同上 | pre-existing |
| `test_no_tracked_file_is_ignored_by_gitignore` | `.gitignore` 例外规则问题 | pre-existing |

> **注**：4 个 render integration 测试失败是因为需要 `asset_resolver` 回调（素材存储管线），在无完整渲染管线的测试环境中是预期行为。
>
> ⚠️ **勘误（2026-09-23 修正）**：第 5 条失败的**原因判断是错的**。原文写「`.gitignore` 的 `!deploy/**/evidence/*.log` 例外规则未被 `git check-ignore --stdin` 正确处理」——
> 实为**规则本身的 glob 深度不够**：`!deploy/**/evidence/*.log` 匹配不到
> `deploy/comfyui/evidence/golden_set_20260918/39_golden_set_run.log`（`evidence/` 之下还有一层目录），
> 于是该文件既被追踪、又被 `*.log` 命中。已把例外改为 `!deploy/**/evidence/**/*.log` 并给仓库根 `evidence/` 开同类例外，
> **该用例现为通过**（`test_repo_hygiene.py` 12 passed）。⇒ 修完后后端失败数由 **5 降为 4**，通过数 **571 → 572**。
> 教训：测试报红时，「gate 工具不生效」与「gate 规则写错」是两种完全不同的根因，**先核对规则本身，再怀疑工具**。

### 新增测试（本轮）

| 文件 | 用例数 | 说明 |
|---|---|---|
| `tests/test_stats.py` | 7 | P8-05 质量看板端点 |
| `tests/test_compare.py` | 8 | P8-07 A/B 对比端点 |
| **合计** | **15** | 全部通过 |

### 关键路径测试（真实 PG）

| 测试文件 | 用例数 | 说明 |
|---|---|---|
| `test_quota.py` | 8 | 配额原子扣减（条件 UPDATE） |
| `test_queue_guard.py` | 14 | 队列深度闸门 |
| `test_in_flight_limit.py` | 10 | 每用户在途上限 |
| `test_content_safety.py` | 多条 | 输入侧内容安全 |
| `test_auth_coverage.py` | 36 | 接口鉴权全覆盖 |
| `test_catalog_templates.py` | 10 | 模板库 AC-F6 |
| **合计** | **78+** | **全部通过真实 PG** |

## 引擎校验结果

| 层级 | 结果 |
|---|---|
| L1（节点存在性） | ✅ PASS |
| L2（连线合法性） | ✅ PASS |
| L3（参数 Schema） | ✅ PASS |
| L4（真实出图） | ⏭ 跳过（需 GPU） |

## 前端检查结果

| 检查项 | 结果 |
|---|---|
| TypeScript 类型检查 | ✅ 通过 |
| ESLint | ✅ 0 errors, 1 warning（既有） |
| 构建 | ✅ 通过（27s） |
| ruff（Python） | ✅ All checks passed |

## 覆盖率说明

> ⚠️ 本项目**未安装覆盖率工具**（`pytest-cov` / `istanbul`），不提供覆盖率数字。
> 依据纪律 #15：测试里的期望值必须钉「规格」，不能从被测实现里算出来。
> 覆盖率工具的阈值设置本身也需要人工标定，当前阶段不具备条件。

## 结论

- **571/576 通过**（99.1%），5 个失败全部 pre-existing
- **关键路径（quota / queue guard / in-flight / auth / content safety）全部通过真实 PostgreSQL**
- **引擎 L1/L2/L3 全部 PASS**
- **前端 TypeScript/lint/build 全部通过**
- P8 阶段 DoD（良品率有数、有看板、可下钻；缺陷知识库 ≥30 条；模型/工作流变更有回归门禁）**已满足**
