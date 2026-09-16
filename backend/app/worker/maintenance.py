"""素材与产物的**物理**清理（P6-10 的「生命周期清理」半边）。

**这个模块为什么必须存在**：素材只有软删除（`Asset.is_deleted`），
也就是说"删除"只是把一行标记了一下 —— 对象存储里的字节永远留着。
没有物理清理，存储就是**只增不减**的：一条 1000 张的批次就是几个 GB，
跑上几个月就会把数据盘吃满（EX-4 disk_full，而磁盘满了连出图都做不了）。
仓库里 `delete` 接口、`storage.delete`、`config.py` 的多处注释都写着
"物理清理由生命周期任务统一做（P6-10）" —— 指的就是本模块，
在它存在之前，`asset_trash_retention_days` / `asset_output_retention_days`
这两个配置项是**死配置**（写了也不会有人读）。

## 分层：纯函数 + 薄 Celery 任务

业务逻辑全在 `purge_expired_assets()`（纯函数，一切外部依赖从参数注入），
Celery 任务 `cleanup_assets()` 只做「建 session → 调它 → commit」。

理由与 `TaskExecutor` 的取舍完全一致（见 `worker/tasks.py` 开头）：
把逻辑写进被 `@celery_app.task` 装饰的函数里，单测就必须先有 Celery **和 Redis**
才能调到它 —— 而本项目本地是无卡/无 Redis 环境，那样等于把这条逻辑变成"测不了"。
存储同样是一个外部依赖（MinIO 连不上），所以它也只能从参数注入。

## 两条规则，不要混成一条

- **规则 A · 回收站超期**：`is_deleted` 且进回收站已超过 `trash_days` 天。
  **已采纳 / 已收藏不豁免这条** —— 用户是**主动**删的它，保留期只是给误删一个
  挽回窗口，不是"重要资产豁免"。豁免会让"删掉的图永远占着存储"，
  而这恰恰是本模块要解决的问题。
- **规则 B · 产物超期**：仅当 `output_days > 0` 时才生效（0 = 永久保留，V1 默认）。
  `kind == output` 且未删除且 `is_adopted` / `is_favorite` 都为假，且创建已超过
  `output_days` 天。
  **`is_adopted` / `is_favorite` 的豁免是硬要求**：那是用户明确标记"我要的"资产，
  按时间把它删掉会直接摧毁信任（用户看到的是"我采纳的图自己没了"），
  而 `is_adopted` 还是良品率指标的分子 —— 删了它，历史指标就再也算不出来了。

两条规则**各写各的 `WHERE`**，用 `OR` 拼起来（见 `_candidate_query`）。
合并成一条会把两者的豁免条件混在一起，最典型的错误就是让"已采纳"顺带豁免了回收站
（`test_trashed_asset_is_purged_even_if_adopted_or_favorited` 专门钉这一点）。

## 删除顺序：先对象，后记录 —— 这是本模块最容易写错的地方

1. 先 `storage.delete(asset.object_key)`；
2. 对象删成功了，才删 DB 记录；
3. 对象删**失败**：记 `log.warning`、**跳过该条记录**（保留它）、
   计入 `storage_failed`、继续处理下一条。

理由：反过来（先删记录）时，一旦对象删除失败，我们就留下了一个**永远查不到的孤儿
对象** —— 平台没有任何"列出存储里所有对象"的能力（`AssetStorage` 只有
put/get/delete），所以谁也发现不了它，空间永久泄漏，且**不会有任何报错**。
保留记录则完全不同：下一轮扫描会重新挑到它，存储恢复后自愈。
而且 `delete` 是幂等的（见 `AssetStorage.delete` 的说明），
所以"对象其实已经删掉了、只是记录没删成"这种半完成状态重试也不会卡住。

**这是"宁可慢，不可漏"的取舍**：清理晚几天完成没人会有感觉，
但悄悄丢掉/泄漏用户的字节是不可接受的。

## 为什么每轮有上限

`settings.cleanup_batch_limit`（默认 500）。清理跑在 maintenance 队列上，
一轮把百万行全扫出来 + 逐条删会把 DB 连接占满，直接影响出图主链路
（NFR-1 的提交响应预算、以及 GPU 空闲时的排队）。分批做只是慢一点，
而"慢"对清理是无害的 —— 下一轮（见 `celery_app.py` 的 beat 配置）会接着做。

## 时区

`Asset.created_at` / `updated_at` 存的是 UTC。`now` 必须是 timezone-aware 的 UTC
（`datetime.now(timezone.utc)`），**所有时间比较都在 SQL 里做** ——
读回来的 `updated_at` 在 SQLite 上是 naive 的，拿它跟 aware 的 `now` 在 Python 里
比会直接 `TypeError`。

`now` 做成参数是为了可测试：不注入就只能真的等 30 天才能验证规则 A 生效。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.asset import Asset
from app.models.enums import AssetKind
from app.services.storage import AssetStorage, MinioAssetStorage
from app.worker.celery_app import celery_app

log = get_logger(__name__)

#: 返回值形状。用 plain dict（而不是 dataclass）是为了让它直接进 Celery 的 JSON
#: 结果后端，且测试里可以整个 dict 一次断言完。
PurgeResult = dict[str, int]


def purge_expired_assets(
    db: Session,
    *,
    storage_factory: Callable[[], AssetStorage] = MinioAssetStorage,
    now: datetime | None = None,
    limit: int | None = None,
    trash_days: int | None = None,
    output_days: int | None = None,
) -> PurgeResult:
    """清理一轮过期的素材/产物，返回 `{"candidates", "deleted", "storage_failed"}`。

    **不做 commit**：事务边界留给调用方（Celery 任务只在一轮全部处理完之后提交一次）。
    这样"处理到一半进程被杀"的结果是「对象删了、记录还在」——
    下一轮重挑时 `delete` 幂等，能自愈；反过来则会出现永远查不到的孤儿对象。

    - `storage_factory`：返回 `AssetStorage` 的可调用对象（注入点，见模块 docstring）。
    - `now`：比较基准，默认"此刻的 UTC"。注入它才能构造"超期"而不必真的等 30 天。
    - `limit` / `trash_days` / `output_days`：默认取 `settings` 里的同名配置。
    """
    moment = now or datetime.now(timezone.utc)
    trash_retention = settings.asset_trash_retention_days if trash_days is None else trash_days
    output_retention = settings.asset_output_retention_days if output_days is None else output_days
    cap = settings.cleanup_batch_limit if limit is None else limit

    candidates = list(db.scalars(_candidate_query(moment, trash_retention, output_retention, cap)))

    deleted = 0
    storage_failed = 0
    # 整个批次复用**同一个**存储实例：`MinioAssetStorage` 会在实例上持有 Minio 客户端
    # （含连接池）。逐条新建的话，500 条就是 500 个连接池，光是建连就能把清理拖成
    # 比出图还重的负载。
    #
    # 没有候选时**不去碰存储**：配置错了（例如 MinIO 地址写错）不该让一次"无事可做"
    # 的清理报错。
    if candidates:
        with storage_factory() as storage:
            for asset in candidates:
                try:
                    storage.delete(asset.object_key)
                except Exception as exc:
                    # 捕获所有异常（`Exception`）是刻意的：对象存储这一层的故障面很宽
                    # （S3Error / urllib3 / OSError / 超时…），我们不关心是哪一种 ——
                    # 处理方式完全相同：**留着记录，下一轮再来**。
                    # 绝不能让一条坏数据把整轮清理打断，那会让它后面的所有过期素材
                    # 永远轮不到（且不报错，只是"清理好像没生效"）。
                    storage_failed += 1
                    log.warning(
                        "cleanup.storage_delete_failed asset_id=%s key=%s err=%s",
                        asset.id,
                        asset.object_key,
                        str(exc)[:200],
                    )
                    continue
                # 只有对象确实删掉了才删记录。顺序反了会产生查不到的孤儿对象（见模块 docstring）。
                db.delete(asset)
                deleted += 1
        # `SessionLocal` 是 `autoflush=False`，不能指望后面的查询顺手替我们 flush；
        # 显式 flush 让"已删除"这件事在处理完后就确定下来（也便于调用方 flush/commit 之外
        # 的错误在提交前就暴露）。
        db.flush()

    if deleted or storage_failed:
        log.info(
            "cleanup.purged candidates=%s deleted=%s storage_failed=%s",
            len(candidates),
            deleted,
            storage_failed,
        )

    return {
        "candidates": len(candidates),
        "deleted": deleted,
        "storage_failed": storage_failed,
    }


def _candidate_query(
    moment: datetime, trash_days: int, output_days: int, cap: int
) -> Select[tuple[Asset]]:
    """本轮候选集。两条规则各自一个条件，`OR` 起来后统一套用同一个上限。

    为什么是**一个** `LIMIT` 而不是"每条规则各查一批"：两批各自限额会让实际处理量
    变成 2×`cleanup_batch_limit`，上限就不再是一个可推算的常数了 ——
    而这个上限存在的意义正是"能算出一轮最多多少负载"。

    排序按 `id` 升序（= 最早入库的优先）：清理的推进方向与数据的产生顺序一致，
    积压会单调地往前啃，不会在某种排序抖动下反复挑中同一批（也就不会饿死后面的）。
    """
    conditions = [
        # 规则 A：回收站超期。`updated_at` 就是**进回收站的时刻** ——
        # API 的软删除只做 `asset.is_deleted = True`，而该列带
        # `onupdate=func.now()`，所以这次 UPDATE 会把 updated_at 刷成删除时间。
        # 因此这里不能用 created_at（那是"素材被创建的时间"，与保留期无关）。
        and_(
            Asset.is_deleted.is_(True),
            Asset.updated_at < moment - timedelta(days=trash_days),
        )
    ]
    if output_days > 0:
        # 规则 B：产物超期。`output_days == 0` 表示永久保留 —— 这条 gate 必须在
        # SQL 之外判（0 天在 SQL 里会退化成"删掉所有产物"）。
        conditions.append(
            and_(
                Asset.kind == AssetKind.OUTPUT.value,
                Asset.is_deleted.is_(False),
                Asset.is_adopted.is_(False),  # 用户明确要的，永不按时间删
                Asset.is_favorite.is_(False),  # 同上
                Asset.created_at < moment - timedelta(days=output_days),
            )
        )

    return select(Asset).where(or_(*conditions)).order_by(Asset.id.asc()).limit(cap)


# ---------------------------------------------------------------- Celery 入口


@celery_app.task(name="app.worker.maintenance.cleanup_assets")
def cleanup_assets(
    limit: int | None = None,
    storage_factory: Callable[[], AssetStorage] | None = None,
) -> PurgeResult:
    """清理一轮过期素材/产物（beat 定时触发，跑在 `maintenance` 队列上）。

    只做三件事：建 session、调纯函数、提交。**逻辑一行都不放这里** ——
    放进来单测就必须依赖 Celery + Redis（见模块 docstring 的分层说明）。

    `storage_factory` 默认在**调用时**解析成 `MinioAssetStorage`（而不是写成参数默认值）：
    写成默认值会在 import 期就把实现绑死，单测没法替换掉它，于是"任务到底有没有
    commit"这件事就**无法验证** —— 而漏掉 `commit()` 的后果是
    `Session.close()` 静默回滚全部删除（日志照打、记录一条没少），
    属于最难发现的一类故障。`SessionLocal` 同理，测试直接替换模块属性
    （与 `test_worker.py` 测 `requeue_orphans` 的做法一致）。
    生产路径不传它，行为与写死 MinIO 完全相同。

    不加重试装饰器：清理是**幂等且周期性**的（下一轮 beat 会再来），
    单独为它做重试只会让失败被掩盖两遍。真出错了让异常抛出去、在 worker 日志里
    以 error 露面，比"悄悄重试成功"更容易发现。
    """
    db = SessionLocal()
    try:
        result = purge_expired_assets(
            db,
            limit=limit,
            storage_factory=storage_factory or MinioAssetStorage,
        )
        db.commit()
    finally:
        db.close()

    if result["storage_failed"]:
        # 这条**值得告警**：说明对象存储有故障，清理已经在退化（记录删不掉、
        # 空间不释放）。它不会自己变好，需要人去看 MinIO。
        log.warning(
            "cleanup.storage_degraded failed=%s candidates=%s",
            result["storage_failed"],
            result["candidates"],
        )
    return result
