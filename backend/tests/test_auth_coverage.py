"""`AC-N5`：**接口全部鉴权** + **端口不暴露**（NFR-4 / PRD §7）。

为什么用「遍历路由表」而不是手写一份路径清单
============================================

手写清单的失效方式是**静默的**：新增一条路由却忘了加用例 —— 清单照旧全绿，
于是"接口全部鉴权"这句话在**没人知道**的情况下变成假的。这个仓库已经吃过
同型的亏（`项目进展.md` #21「提交信息说 A、仓库是 B」）。

所以本文件从 `app.main:app` 的 **`app.routes` 枚举**出全部路由，**默认要求 401**，
只有显式列进白名单的端点才允许匿名访问 —— 将来新增路由会被**自动纳入**：
- 新路由忘了加鉴权 ⇒ 本文件立刻变红（404/200 ≠ 401）；
- 新路由确实是公开的 ⇒ 必须显式加进白名单并写清理由（一次有意识的决定，而不是疏漏）。

⚠️ **两条元测试**（`test_enumeration_is_not_empty_*` / `test_whitelist_*`）是必需的：
参数化用例最大的失败模式是"枚举为空 ⇒ 一条用例都没有 ⇒ 全绿"。元测试把
"遍历真的看到了路由"和"白名单里的名字真的存在"都变成可执行判据。

⚠️ 本文件**不**覆盖「越权访问别人的资源返回 404」（契约 §2.2）—— 那是 `_owned_task` /
`get_batch` 的归属校验，由 `test_api_tasks.py` / `test_quota.py` 覆盖；
这里只回答"**没有凭证**时会不会被放进去"。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ================================================================
# 白名单：**显式**列出不需要鉴权的端点 + 理由
# ================================================================

#: 允许匿名访问的端点（`(method, path)`）。每条都必须写清"为什么可以不鉴权"。
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    # 存活探针：由负载均衡 / 容器编排（k8s livenessProbe）调用。
    # 让探针持有凭证会把"服务是否活着"与"凭证是否有效"耦合起来 ——
    # 令牌过期会让编排系统把健康实例判死并重启，那是自伤。
    ("GET", "/health"): "存活探针：编排系统调用，凭证失效不应导致实例被重启",
    # 就绪探针：同上，且它只回依赖项的 ok/degraded，不泄露业务数据。
    ("GET", "/health/ready"): "就绪探针：编排系统调用，返回值只有依赖项健康状态",
    # 注册：此刻用户**还没有账号**，无从持有令牌。
    ("POST", "/api/v1/auth/register"): "注册：尚无账号，不可能持有令牌",
    # 登录：令牌的唯一获取入口。
    ("POST", "/api/v1/auth/login"): "登录：令牌的唯一获取入口",
    # FastAPI 自动生成的文档端点（`docs_url` 在 prod 下为 None，即生产不暴露）。
    # 它们只暴露接口形状（本就写给调用方看），不含数据、不接受写操作。
    ("GET", "/openapi.json"): "OpenAPI 文档：只暴露接口形状，prod 下 docs_url=None 即不注册",
    ("GET", "/docs"): "Swagger UI：同上",
    ("GET", "/docs/oauth2-redirect"): "Swagger UI 的 OAuth2 回调页：同上",
}


#: OpenAPI 操作里可能出现的 HTTP 方法（用于从 schema 里筛出操作方法名）。
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _api_routes_from_openapi() -> set[tuple[str, str]]:
    """API 操作清单 `(method, path)`，从 `app.openapi()` 取。

    ⚠️ **为什么不直接遍历 `app.routes`**（本轮实测发现的事实，值得记下来）：
    FastAPI 现在把 `app.include_router(...)` 的结果包成一个 **`_IncludedRouter`
    容器节点** —— 它**没有** `path` / `methods`，前缀挂在容器的 `include_context` 上，
    内层 `original_router.routes` 里的路径是**不带前缀**的（`/tasks` 而不是
    `/api/v1/tasks`）。直接遍历 `app.routes` 会**一条 API 路由都看不到**，
    而"看不到"的后果是参数化为空 ⇒ 全部用例消失 ⇒ 假绿。

    要手工下钻就得自己拼前缀，等于把 FastAPI 的路由逻辑抄一份（迟早分叉）。
    `app.openapi()` 是从同一份路由表生成的、**带正确前缀**的权威清单，
    且参数化仍然随新路由自动增长 —— 与"遍历路由表"的目的完全一致。
    """
    paths = app.openapi().get("paths") or {}
    out: set[tuple[str, str]] = set()
    for path, operations in paths.items():
        for method in operations:
            upper = method.upper()
            if upper in _HTTP_METHODS and upper not in {"HEAD", "OPTIONS"}:
                out.add((upper, path))
    return out


def _framework_routes() -> set[tuple[str, str]]:
    """Starlette 层**直接注册**在 `app.routes` 上的路由（文档端点等）。

    这些**不会**出现在 OpenAPI schema 里（它们不是"API 操作"），但同样是可访问的
    端点，必须一起纳入"是否需要鉴权"的判定：`/docs`、`/openapi.json` 与
    `/docs/oauth2-redirect` 都在这里。
    """
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add((method, path))
    return out


def _http_routes() -> list[tuple[str, str]]:
    """全部可访问的 HTTP 端点 = API 操作 ∪ Starlette 层路由。

    过滤掉 `HEAD` / `OPTIONS`（自动生成的辅助方法）：断言它们只是重复同一个
    处理函数，加进来只会让参数化翻倍。
    """
    return sorted(_api_routes_from_openapi() | _framework_routes())


HTTP_ROUTES = _http_routes()
PROTECTED_ROUTES = [r for r in HTTP_ROUTES if r not in PUBLIC_ROUTES]


def _concrete(path: str) -> str:
    """把路径模板里的参数换成占位值：`/tasks/{task_id}` → `/tasks/1`。

    鉴权发生在**参数校验之前**（依赖先于当前 dependant 的 path/body 解析），
    所以占位值不会影响结果；`test_placeholder_paths_reach_the_auth_check` 把这一点
    钉成可执行判据 —— 若哪天顺序反了（先校验参数后鉴权），那条会变红。
    """
    out = path
    while "{" in out and "}" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + "1" + out[end + 1 :]
    return out


@pytest.fixture
def anon_client():
    """**不覆盖任何依赖**的客户端 —— 走真实鉴权链（这正是本文件要测的）。

    ⚠️ 不要用 `conftest.client`：它把 `get_current_user` 覆盖成固定用户，
    用它测鉴权等于把被测对象替换掉了（会全绿，且绿得毫无意义）。
    """
    with TestClient(app) as c:
        yield c


# ================================================================
# 元测试：证明遍历与白名单都不是空的
# ================================================================


def test_enumeration_is_not_empty_and_sees_the_known_routes() -> None:
    """遍历必须**真的**看到受保护路由（否则参数化为空，全部用例一条都不跑 ⇒ 假绿）。

    这里列出的是一份**子集**（不是完整清单）：它只用来回答"枚举坏了没有"。
    真正的覆盖面由 `PROTECTED_ROUTES` 决定 —— 所以新增路由**不需要**改这里。
    """
    must_be_seen = {
        ("POST", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks/{task_id}"),
        ("POST", "/api/v1/tasks/{task_id}/cancel"),
        ("POST", "/api/v1/tasks/{task_id}/retry"),
        ("POST", "/api/v1/tasks/estimate"),
        ("POST", "/api/v1/batches"),
        ("GET", "/api/v1/batches"),
        ("GET", "/api/v1/batches/{batch_id}"),
        ("POST", "/api/v1/batches/{batch_id}/retry-failed"),
        ("GET", "/api/v1/workflows"),
        ("GET", "/api/v1/workflows/{name}/schema"),
        ("GET", "/api/v1/templates"),
        ("GET", "/api/v1/templates/categories"),
        ("POST", "/api/v1/templates"),
        ("GET", "/api/v1/models"),
        ("POST", "/api/v1/assets"),
        ("GET", "/api/v1/assets"),
        ("GET", "/api/v1/assets/{asset_id}"),
        ("GET", "/api/v1/assets/{asset_id}/content"),
        ("PATCH", "/api/v1/assets/{asset_id}"),
        ("DELETE", "/api/v1/assets/{asset_id}"),
        ("POST", "/api/v1/assets/pack"),
        ("GET", "/api/v1/auth/me"),
    }
    seen = set(HTTP_ROUTES)
    missing = sorted(must_be_seen - seen)
    assert not missing, f"路由遍历漏掉了这些路由（枚举实现有问题）：{missing}"
    assert len(PROTECTED_ROUTES) >= len(must_be_seen), (
        f"受保护路由只有 {len(PROTECTED_ROUTES)} 条，少于已知子集 —— 白名单被写宽了？"
    )


def test_whitelist_entries_all_exist() -> None:
    """白名单里的每个端点都必须**真的存在**（防拼写错误悄悄放宽）。

    如果 `/health` 被改名而白名单没跟着改，白名单就会**失效**（新路由默认要求 401，
    于是探针开始返回 401）—— 那条会在下面的参数化用例里变红，但报错信息远不如这条清楚。
    """
    unknown = sorted(set(PUBLIC_ROUTES) - set(HTTP_ROUTES))
    assert not unknown, f"白名单里有路由表里不存在的端点（拼写错误或路由已删除）：{unknown}"


# ================================================================
# 主体：全部非白名单路由，不带 Authorization ⇒ 401
# ================================================================


@pytest.mark.parametrize(
    ("method", "path"), PROTECTED_ROUTES, ids=[f"{m} {p}" for m, p in PROTECTED_ROUTES]
)
def test_protected_route_without_token_is_401(anon_client, method: str, path: str) -> None:  # type: ignore[no-untyped-def]
    """不带 `Authorization` 头 ⇒ **401**（契约 §2.2：缺少/无效/过期令牌）。

    期望值写**字面量 401**，不从 `status.HTTP_401_UNAUTHORIZED` 推导 ——
    同一个常量被改坏时，测试要能发现。
    """
    resp = anon_client.request(method, _concrete(path), json={})

    assert resp.status_code == 401, (
        f"{method} {path} 在**没有凭证**时返回了 {resp.status_code}（应为 401）：{resp.text[:200]}"
    )
    # 401 必须带 WWW-Authenticate（契约 §2.2 的备注栏），否则浏览器/客户端不会弹认证
    assert resp.headers.get("www-authenticate") == "Bearer", (
        f"{method} {path} 的 401 缺少 WWW-Authenticate: Bearer"
    )


@pytest.mark.parametrize(
    ("method", "path"), sorted(PUBLIC_ROUTES), ids=[f"{m} {p}" for m, p in sorted(PUBLIC_ROUTES)]
)
def test_public_route_does_not_require_token(anon_client, method: str, path: str) -> None:  # type: ignore[no-untyped-def]
    """白名单里的端点**不得**返回 401（否则探针 / 注册 / 登录直接不可用）。

    只断言"不是 401"：`/health/ready` 在本机依赖缺失时会返回 `degraded`（仍是 200），
    注册/登录在空 body 下会 422 —— 那些都与"是否需要凭证"无关。
    """
    resp = anon_client.request(method, _concrete(path), json={})

    assert resp.status_code != 401, f"{method} {path} 被要求鉴权了（它在白名单里）：{resp.text[:200]}"


def test_placeholder_paths_reach_the_auth_check(anon_client) -> None:  # type: ignore[no-untyped-def]
    """⭐ 钉住"**鉴权在参数校验之前**"这条前提（本文件其余用例都建立在它上面）。

    判据：带路径参数的路由**用占位值 1**（一个几乎肯定不存在的资源）请求，
    得到的必须是 **401 而不是 404** —— 404 意味着处理函数已经跑起来查过库了，
    也就是"没鉴权就先进了业务代码"。同时 `json={}` 是**不合法**的 body，
    若拿到 422 则说明 FastAPI 先校验参数后鉴权（顺序假设不成立，必须上报处理）。
    """
    for method, path in [
        ("GET", "/api/v1/tasks/{task_id}"),
        ("POST", "/api/v1/tasks/{task_id}/cancel"),
        ("POST", "/api/v1/tasks/{task_id}/retry"),
        ("GET", "/api/v1/assets/{asset_id}"),
    ]:
        resp = anon_client.request(method, _concrete(path), json={})
        assert resp.status_code == 401, (
            f"{method} {path} 在无凭证时返回 {resp.status_code} —— "
            "鉴权没有排在参数校验/业务查询之前"
        )


# ================================================================
# 另一半：**端口不暴露**（ComfyUI 无鉴权，PRD NFR-4）
# ================================================================


def test_comfyui_base_url_rejects_0_0_0_0() -> None:
    """`settings.comfyui_base_url` 指向 `0.0.0.0` 必须**构造失败**（校验器直接报错）。

    ComfyUI **没有任何鉴权**：绑到 `0.0.0.0` 等于把 GPU 送人（PRD NFR-4 的安全红线）。
    这条用 `Settings(...)` 直接构造（而不是读全局 `settings`），才能验证校验器本身；
    读全局单例只能证明"当前这份配置恰好没踩雷"。
    """
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="0.0.0.0"):
        Settings(comfyui_base_url="http://0.0.0.0:8188")

    # 变体：端口不同、协议不同，只要含 0.0.0.0 就拒
    with pytest.raises(ValidationError):
        Settings(comfyui_base_url="0.0.0.0:8188")


def test_current_binding_is_loopback() -> None:
    """**当前生效的配置**必须指向回环地址（不是"某天有人改坏了但没人发现"）。

    上一条证明"校验器在"，这一条证明"现在这份配置是对的" —— 两者缺一不可。
    """
    from app.core.config import settings

    assert "0.0.0.0" not in settings.comfyui_base_url
    assert any(host in settings.comfyui_base_url for host in ("127.0.0.1", "localhost", "[::1]")), (
        f"comfyui_base_url={settings.comfyui_base_url!r} 不是回环地址："
        "ComfyUI 无鉴权，指向非回环地址等于对外暴露 GPU"
    )
