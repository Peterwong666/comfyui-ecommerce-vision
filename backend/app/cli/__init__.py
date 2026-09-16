"""后端 CLI 入口（P2-10）。

为什么是显式 CLI，而不是启动钩子或 alembic 数据迁移
--------------------------------------------------
- **不用启动钩子**：在生产共享库上每次开机跑 DDL/DML 不确定，且多 worker 会竞争。
  ADR-004 要求部署可复现 = 步骤显式、可重复、可审计。
- **不用 alembic 数据迁移**：`backend/tests/test_migration.py` **断言只存在一个迁移文件**；
  且 registry 是与 schema 无关的可变数据，每次改 registry 都要新 revision 不可接受。
"""
