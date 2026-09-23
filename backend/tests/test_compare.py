"""P8-07 A/B 对比接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth_header(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={"username": "compare_user", "password": "test123456", "email": "cmp@test.com"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "cmp@test.com", "password": "test123456"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestCompare:
    """A/B 对比端点测试。"""

    def test_submit_compare(self, client: TestClient) -> None:
        headers = _auth_header(client)
        r = client.post(
            "/api/v1/compare",
            json={
                "workflow_name": "t2i_v1",
                "seed": 42,
                "variants": [
                    {"label": "A: steps=20", "params": {"steps": 20}},
                    {"label": "B: steps=30", "params": {"steps": 30}},
                ],
            },
            headers=headers,
        )
        assert r.status_code == 202
        data = r.json()
        assert data["workflow_name"] == "t2i_v1"
        assert data["seed"] == 42
        assert len(data["variants"]) == 2
        assert data["variants"][0]["label"] == "A: steps=20"
        assert data["variants"][0]["status"] == "pending"

    def test_submit_requires_at_least_two_variants(self, client: TestClient) -> None:
        headers = _auth_header(client)
        r = client.post(
            "/api/v1/compare",
            json={
                "workflow_name": "t2i_v1",
                "seed": 42,
                "variants": [{"label": "only one", "params": {}}],
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_submit_rejects_unknown_workflow(self, client: TestClient) -> None:
        headers = _auth_header(client)
        r = client.post(
            "/api/v1/compare",
            json={
                "workflow_name": "nonexistent_v1",
                "seed": 42,
                "variants": [
                    {"label": "A", "params": {}},
                    {"label": "B", "params": {}},
                ],
            },
            headers=headers,
        )
        assert r.status_code == 404

    def test_get_compare_group(self, client: TestClient) -> None:
        headers = _auth_header(client)
        # Submit first
        r = client.post(
            "/api/v1/compare",
            json={
                "workflow_name": "t2i_v1",
                "seed": 99,
                "variants": [
                    {"label": "X", "params": {"cfg": 7}},
                    {"label": "Y", "params": {"cfg": 12}},
                ],
            },
            headers=headers,
        )
        group_id = r.json()["id"]

        # Get
        r = client.get(f"/api/v1/compare/{group_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == group_id
        assert r.json()["seed"] == 99

    def test_get_nonexistent_group(self, client: TestClient) -> None:
        headers = _auth_header(client)
        r = client.get("/api/v1/compare/nonexistent", headers=headers)
        assert r.status_code == 404

    def test_list_compare_groups(self, client: TestClient) -> None:
        headers = _auth_header(client)
        # Submit two groups
        for seed in [1, 2]:
            client.post(
                "/api/v1/compare",
                json={
                    "workflow_name": "t2i_v1",
                    "seed": seed,
                    "variants": [
                        {"label": "A", "params": {}},
                        {"label": "B", "params": {}},
                    ],
                },
                headers=headers,
            )
        r = client.get("/api/v1/compare", headers=headers)
        assert r.status_code == 200
        groups = r.json()
        assert len(groups) >= 2

    def test_requires_auth(self) -> None:
        from app.main import app

        raw = TestClient(app, raise_server_exceptions=False)
        r = raw.get("/api/v1/compare")
        assert r.status_code == 401

    def test_base_params_merge(self, client: TestClient) -> None:
        """基础参数应与变体参数合并（变体优先）。"""
        headers = _auth_header(client)
        r = client.post(
            "/api/v1/compare",
            json={
                "workflow_name": "t2i_v1",
                "seed": 1,
                "base_params": {"width": 1024, "steps": 20},
                "variants": [
                    {"label": "A", "params": {"steps": 30}},
                    {"label": "B", "params": {"cfg": 12}},
                ],
            },
            headers=headers,
        )
        assert r.status_code == 202
        v0 = r.json()["variants"][0]
        assert v0["params"]["width"] == 1024  # from base
        assert v0["params"]["steps"] == 30  # overridden by variant
        v1 = r.json()["variants"][1]
        assert v1["params"]["width"] == 1024  # from base
        assert v1["params"]["cfg"] == 12  # from variant
