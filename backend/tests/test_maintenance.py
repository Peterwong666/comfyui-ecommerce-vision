"""素材/产物的物理清理（P6-10）单元测试。

**为什么全部用假存储**：本机没有 MinIO（实例处于无卡模式，也连不上对象存储）。
`purge_expired_assets` 的存储依赖是注入的（见 `worker/maintenance.py` 的分层说明），
所以这里用 `InMemoryAssetStorage` 就能把整条清理链路跑通，
并直接断言"存储里的对象真的没了"——而不是只断言"记录被删了"。

**关于"构造超期"**：默认注入 `now` 参数（把基准时间推到将来），
**不去改 `created_at` 的 `server_default`**。改 `server_default` 会让所有行的
语义一起变（包括别的用例造的、以及代码里 `func.now()` 的预期），
解决不了问题还制造新问题。

只有两种情况例外，都是直接给**实例属性**赋值（与 `test_worker.py` 构造
`requeue_orphans` 的做法一致，同样不碰 `server_default`）：
① 要区分 `created_at` 与 `updated_at`（保留期到底从哪个时刻算）时，
必须让两者不相等；② 走真实调度路径的薄壳用例没有 `now` 可注入。

覆盖：回收站规则（超期/未超期/采纳不豁免）、产物规则（0=永久、超期、已采纳/已收藏豁免）、
删除顺序（对象删不掉必须保留记录且继续处理下一条）、幂等、批量上限、跨用户、
空库（无候选时不碰存储）、薄壳任务的 commit。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.enums import AssetKind, UserRole
from app.models.user import User
from app.services.storage import InMemoryAssetStorage
from app.worker.maintenance import cleanup_assets, purge_expired_assets

# ---------------------------------------------------------------- 工具


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def later(days: int) -> datetime:
    """相对"此刻"的将来时刻 —— 注入它就能构造"已超期"，不必真的等 30 天。"""
    return utcnow() + timedelta(days=days)


@pytest.fixture
def storage() -> InMemoryAssetStorage:
    return InMemoryAssetStorage()


class FlakyDeleteStorage(InMemoryAssetStorage):
    """`delete` 对指定 key 抛异常的替身，其余 key 一切正常。

    用它才能表达"**一条**坏数据"：如果整个存储都坏，就没法同时验证
    "后面的记录照常被清理"这条纪律了。
    """

    def __init__(self) -> None:
        super().__init__()
        self.fail_keys: set[str] = set()

    def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise OSError("MinIO 连接被重置")
        super().delete(key)


def seed(
    db: Session,
    storage: InMemoryAssetStorage,
    user_id: int,
    key: str,
    *,
    kind: str = AssetKind.UPLOAD.value,
    is_deleted: bool = False,
    is_adopted: bool = False,
    is_favorite: bool = False,
) -> Asset:
    """把对象真的写进存储 + 登记一条 Asset 行。

    对象必须真的存在，"对象被删掉了"这句断言才有意义（否则删一个本来就没有的东西
    也"通过"了 —— 那正是幂等语义，会让这条断言永远为真）。
    """
    storage.put(key, b"x", "image/png")
    asset = Asset(
        user_id=user_id,
        kind=kind,
        object_key=key,
        mime_type="image/png",
        size_bytes=1,
        is_deleted=is_deleted,
        is_adopted=is_adopted,
        is_favorite=is_favorite,
    )
    db.add(asset)
    db.commit()
    return asset


def purge(db: Session, storage: InMemoryAssetStorage, **kwargs) -> dict[str, int]:
    """跑一轮清理。注入的 `storage_factory` 必须返回**同一个实例**，
    否则断言不了 `storage.objects`（也就验证不了"对象真的被删了"）。"""
    return purge_expired_assets(db, storage_factory=lambda: storage, **kwargs)


# ---------------------------------------------------------------- 规则 A：回收站超期


def test_trashed_asset_past_retention_is_purged(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """回收站里超过保留期的素材 → 对象与记录**都**消失。

    这是本模块存在的全部理由：软删除只标记了一行，字节还在对象存储里。
    """
    asset = seed(db, storage, user.id, "uploads/1/old.png", is_deleted=True)

    result = purge(db, storage, now=later(40), trash_days=30)

    assert result == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert asset.object_key not in storage.objects  # 对象真的没了
    assert db.get(Asset, asset.id) is None  # 记录也没了
    assert db.query(Asset).count() == 0


def test_trashed_asset_within_retention_survives(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """保留期是给误删的**挽回窗口**：没到期就不许动。"""
    asset = seed(db, storage, user.id, "uploads/1/recent.png", is_deleted=True)

    result = purge(db, storage, now=later(10), trash_days=30)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


def test_retention_counts_from_deletion_moment_not_creation(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """保留期从**进回收站的时刻**算起，不是从素材被创建的时刻算起。

    `updated_at` 就是进回收站的时刻：软删除只做 `asset.is_deleted = True`，
    而该列带 `onupdate=func.now()`，那次 UPDATE 会把 updated_at 刷成删除时间
    （见 `api/v1/assets.py` 的 `delete_asset`）。

    这条专门区分两个字段（上一条用例区分不了：那里两者都约等于"此刻"）。
    若实现误用 `created_at`，下面这种"素材很老、但刚刚才被删"的记录会被**立刻**清掉 ——
    用户刚点完删除，挽回窗口一天都没给到，而回收站的意义正是这个窗口。
    """
    asset = seed(db, storage, user.id, "uploads/1/ancient-just-deleted.png", is_deleted=True)
    asset.created_at = utcnow() - timedelta(days=100)  # 素材很老
    asset.updated_at = utcnow()  # 但刚刚才进回收站
    db.commit()

    result = purge(db, storage, now=later(1), trash_days=30)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


@pytest.mark.parametrize("flag", ["is_adopted", "is_favorite"])
def test_trashed_asset_is_purged_even_if_adopted_or_favorited(db, user, storage, flag) -> None:  # type: ignore[no-untyped-def]
    """**回收站规则不豁免"已采纳/已收藏"** —— 专门钉住"两条规则不要混成一条"。

    用户是**主动**把它删进回收站的，保留期只是给误删一个挽回窗口；
    如果在这里豁免，那"删掉的图"就永远占着存储 —— 而存储只增不减正是
    本模块要消灭的问题。（对比：产物过期规则里这两个标记是**必须**豁免的，
    见下面两条用例。两条规则的条件不同，所以不能合并成一个 `WHERE`。）
    """
    asset = seed(
        db,
        storage,
        user.id,
        f"uploads/1/trashed-{flag}.png",
        kind=AssetKind.OUTPUT.value,
        is_deleted=True,
        **{flag: True},
    )

    result = purge(db, storage, now=later(40), trash_days=30)

    assert result == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert asset.object_key not in storage.objects
    assert db.get(Asset, asset.id) is None


# ---------------------------------------------------------------- 规则 B：产物超期


def test_output_never_expires_when_retention_is_zero(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """`output_days = 0`（V1 默认）= **永久保留**，哪怕产物"老得离谱"。

    这里用 `now=十年后` 来表达"产物已经很久以前了"。
    若实现漏了 `output_days > 0` 这个 gate（0 天直接进 SQL），
    这条就会红 —— 而那意味着**所有历史产物在第一次 beat 之后就全没了**。
    """
    asset = seed(db, storage, user.id, "outputs/1/1/keep.png", kind=AssetKind.OUTPUT.value)

    result = purge(db, storage, now=later(3650), trash_days=30, output_days=0)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


def test_unadopted_output_past_retention_is_purged(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """设了保留期、产物已超期、且用户没有任何"我要它"的标记 → 清理。"""
    asset = seed(db, storage, user.id, "outputs/1/1/stale.png", kind=AssetKind.OUTPUT.value)

    result = purge(db, storage, now=later(31), trash_days=30, output_days=30)

    assert result == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert asset.object_key not in storage.objects
    assert db.get(Asset, asset.id) is None


def test_adopted_output_past_retention_survives(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """已采纳的产物**永不**按时间清理。

    它是用户明确标记"这张我要"的资产（还是良品率指标的分子）——
    到点删掉它，用户看到的是"采纳过的图自己没了"，信任一次性清零。
    """
    asset = seed(
        db,
        storage,
        user.id,
        "outputs/1/1/adopted.png",
        kind=AssetKind.OUTPUT.value,
        is_adopted=True,
    )

    result = purge(db, storage, now=later(3650), trash_days=30, output_days=30)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


def test_favorite_output_past_retention_survives(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """已收藏同理（它表达的是"我以后还要用"）。"""
    asset = seed(
        db,
        storage,
        user.id,
        "outputs/1/1/favorite.png",
        kind=AssetKind.OUTPUT.value,
        is_favorite=True,
    )

    result = purge(db, storage, now=later(3650), trash_days=30, output_days=30)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


def test_live_upload_never_expires(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """**未删且非产物**的素材永远不在候选里 —— 这条守的是最危险的一类错误。

    把 `trash_days` 设成 0（规则 A 最激进的取值）、`output_days` 设成 3650
    （足以覆盖任何产物），用户**正在用**的上传素材也必须毫发无伤。
    如果哪天有人把两条规则合并、或漏了 `kind` 判断，这条会立刻变红。
    """
    asset = seed(db, storage, user.id, "uploads/1/live.png", kind=AssetKind.UPLOAD.value)

    result = purge(db, storage, now=later(3650), trash_days=0, output_days=3650)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert asset.object_key in storage.objects
    assert db.get(Asset, asset.id) is not None


# ---------------------------------------------------------------- 删除顺序（核心纪律）


def test_storage_failure_keeps_record_and_continues(db, user) -> None:  # type: ignore[no-untyped-def]
    """⭐ **对象删不掉时，记录必须保留**，且不能挡住后面的记录。

    为什么不能"删对象失败也删记录"：平台**没有**任何"列出存储里所有对象"的能力
    （`AssetStorage` 只有 put/get/delete），所以一旦记录没了，那个对象就成了
    谁也查不到的孤儿 —— 空间永久泄漏，且不报错、无人知晓。
    保留记录则下一轮会重新挑到它，存储恢复后自愈（见下一条用例）。

    后半段（`good` 那条被正常清理）同样重要：一条坏数据不能把整轮清理打断，
    否则排在它后面的所有过期素材永远轮不到。
    """
    storage = FlakyDeleteStorage()
    storage.fail_keys.add("uploads/1/poison.png")
    bad = seed(db, storage, user.id, "uploads/1/poison.png", is_deleted=True)
    good = seed(db, storage, user.id, "uploads/1/ok.png", is_deleted=True)

    result = purge(db, storage, now=later(40), trash_days=30)

    assert result == {"candidates": 2, "deleted": 1, "storage_failed": 1}
    # 失败的这条：对象还在、记录也还在（下一轮重试）
    assert bad.object_key in storage.objects
    assert db.get(Asset, bad.id) is not None
    # 后面的这条：照常清理干净
    assert good.object_key not in storage.objects
    assert db.get(Asset, good.id) is None


def test_record_is_purged_once_storage_recovers(db, user) -> None:  # type: ignore[no-untyped-def]
    """存储恢复后自愈：上一轮被跳过的记录，下一轮会被重新挑到并清掉。

    这就是"宁可慢，不可漏"的兑现方式 —— 不需要人工介入，也不需要重试逻辑。
    """
    storage = FlakyDeleteStorage()
    key = "uploads/1/flaky.png"
    storage.fail_keys.add(key)
    asset = seed(db, storage, user.id, key, is_deleted=True)

    first = purge(db, storage, now=later(40), trash_days=30)
    assert first["storage_failed"] == 1
    assert db.get(Asset, asset.id) is not None

    storage.fail_keys.clear()  # 存储恢复
    second = purge(db, storage, now=later(40), trash_days=30)

    assert second == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert key not in storage.objects
    assert db.get(Asset, asset.id) is None


def test_purge_is_idempotent_when_object_already_gone(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """对象**早就不在了**（上次删了对象但没删成记录）→ 记录照样正常删掉，不报错。

    没有这条幂等性，"删了对象、进程被杀"这种半完成状态会永久失败：
    记录删不掉、下一轮又挑到同一条，清理从此不再推进（且不报错）。
    """
    asset = Asset(
        user_id=user.id,
        kind=AssetKind.UPLOAD.value,
        object_key="uploads/1/ghost.png",
        mime_type="image/png",
        size_bytes=1,
        is_deleted=True,
    )
    db.add(asset)
    db.commit()
    assert "uploads/1/ghost.png" not in storage.objects  # 对象本来就不存在

    result = purge(db, storage, now=later(40), trash_days=30)

    assert result == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert db.get(Asset, asset.id) is None
    # 真的幂等：再跑一轮无事可做（而不是换个姿势重复报错）
    assert purge(db, storage, now=later(40), trash_days=30)["candidates"] == 0


# ---------------------------------------------------------------- 批量上限与作用域


def test_batch_limit_caps_work_per_round(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """一轮最多处理 `limit` 条 —— 全表扫百万行会把 DB 连接占满、影响出图主链路。

    顺带钉住挑选顺序（按 id 升序 = 最早入库的先清）：剩下来的必须是**最后那条**，
    否则积压可能在某种排序抖动下被反复跳过、永远清不掉。
    """
    assets = [
        seed(db, storage, user.id, f"uploads/1/batch-{i}.png", is_deleted=True) for i in range(3)
    ]

    result = purge(db, storage, now=later(40), trash_days=30, limit=2)

    assert result == {"candidates": 2, "deleted": 2, "storage_failed": 0}
    assert db.get(Asset, assets[0].id) is None
    assert db.get(Asset, assets[1].id) is None
    assert db.get(Asset, assets[2].id) is not None  # 下一轮接着清
    assert assets[2].object_key in storage.objects
    assert db.query(Asset).count() == 1


def test_purge_is_system_wide_not_scoped_to_one_user(db, user, storage) -> None:  # type: ignore[no-untyped-def]
    """清理是**系统行为**，不分用户 —— 别的用户的过期素材同样被清掉。

    选的语义：清理由 beat 定时触发，不在任何请求上下文里，
    策略（保留期）也是平台级的。按用户切分只会引入"谁的素材先被清"这种
    毫无意义的不公平，而对收益（把空间还回来）没有任何影响。
    用户侧的隔离由接口层保证（只能看见自己的素材，见 `api/v1/assets.py`），
    与这里的系统级回收是两件事。
    """
    other = User(
        email="other@example.com",
        password_hash="x",
        role=UserRole.USER.value,
        quota_total=10,
        quota_used=0,
    )
    db.add(other)
    db.commit()

    mine = seed(db, storage, user.id, "uploads/1/mine.png", is_deleted=True)
    theirs = seed(db, storage, other.id, "uploads/2/theirs.png", is_deleted=True)

    result = purge(db, storage, now=later(40), trash_days=30)

    assert result == {"candidates": 2, "deleted": 2, "storage_failed": 0}
    assert db.get(Asset, mine.id) is None
    assert db.get(Asset, theirs.id) is None


# ---------------------------------------------------------------- 空库 / 无候选


def test_no_candidates_returns_zeroes_without_touching_storage(db) -> None:  # type: ignore[no-untyped-def]
    """空表上跑一轮清理：返回全零结果、不抛异常，**且根本不去创建存储客户端**。

    为什么"什么都没做也要能安静跑完"值得单独钉一条：beat 每 6 小时**无条件**触发
    一次，而"没有任何过期数据"才是常态（V1 的 `asset_output_retention_days`
    默认 0、回收站通常也是空的）。这条路径上一旦抛异常，表现就是"清理任务常年
    一半是 failed"—— 而它其实什么都不需要做，人会被这种噪音训练到不再看告警。

    后半段（`storage_factory` 抛异常却仍然成功）钉的是同一件事的另一半：
    存储配置错（MinIO 地址/密钥写错、bucket 不存在）不该让一次**无事可做**的
    清理变成 error。真去连它的话，`MinioAssetStorage` 懒建的客户端会在
    `bucket_exists()` 上把异常抛出来 —— 那就把一个"没有配置问题也能发现"的
    故障变成了每次定时都报的假警报。
    """

    def exploding_factory() -> InMemoryAssetStorage:
        raise AssertionError("没有候选时不该创建存储客户端")

    result = purge_expired_assets(db, storage_factory=exploding_factory)

    assert result == {"candidates": 0, "deleted": 0, "storage_failed": 0}


def test_purge_on_empty_table_is_repeatable(db) -> None:  # type: ignore[no-untyped-def]
    """空库连续跑两轮都是零值 —— 清理不会因为"跑了但没删东西"而自我污染。"""
    storage = InMemoryAssetStorage()

    first = purge(db, storage)
    second = purge(db, storage)

    assert first == {"candidates": 0, "deleted": 0, "storage_failed": 0}
    assert second == first


# ---------------------------------------------------------------- Celery 薄壳


def test_celery_task_commits_and_reports_counts(db, engine, user, storage, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """薄壳的职责只有两件：建 session、commit。

    **`commit` 必须被验证**：`Session.close()` 会回滚未提交的事务，
    所以漏了 commit 的表现是"日志照打、返回的计数也对，但库里一条都没少"——
    静默、且看起来完全正常。这条用例就是为它准备的。

    这里不得不把 `updated_at` 往前挪来构造超期：真实调度路径上没有 `now` 可注入
    （beat 不会传），这与 `test_worker.py` 里构造 `requeue_orphans` 的方式一致。
    """
    from sqlalchemy.orm import sessionmaker

    from app.worker import maintenance

    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(maintenance, "SessionLocal", session_factory)

    asset = seed(db, storage, user.id, "uploads/1/wrapped.png", is_deleted=True)
    asset.updated_at = utcnow() - timedelta(days=100)
    db.commit()

    result = cleanup_assets(storage_factory=lambda: storage)

    assert result == {"candidates": 1, "deleted": 1, "storage_failed": 0}
    assert asset.object_key not in storage.objects
    verify = session_factory()
    try:
        # 用一个**新的** session 读：本 session 的 identity map 里还留着那个对象，
        # 直接 `db.get(...)` 会拿到缓存（假绿）。
        assert verify.get(Asset, asset.id) is None
    finally:
        verify.close()
