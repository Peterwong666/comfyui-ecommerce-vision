"""P8-05 质量看板接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth_header(client: TestClient) -> dict[str, str]:
    """注册并返回 Bearer 头。"""
    client.post(
        "/api/v1/auth/register",
        json={"username": "stats_user", "password": "test123456", "email": "st@test.com"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "st@test.com", "password": "test123456"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class TestQualityDashboard:
    """质量看板端点测试。"""

    def test_returns_200(self, client: TestClient) -> None:
        headers = _auth_header(client)
        r = client.get("/api/v1/stats/quality", headers=headers)
        assert r.status_code == 200

    def test_overview_fields(self, client: TestClient) -> None:
        headers = _auth_header(client)
        data = client.get("/api/v1/stats/quality", headers=headers).json()
        overview = data["overview"]
        assert "total_outputs" in overview
        assert "total_adopted" in overview
        assert "yield_rate" in overview
        assert "total_tasks" in overview
        assert "adopted_events" in overview
        assert "downloaded_events" in overview

    def test_by_workflow_list(self, client: TestClient) -> None:
        headers = _auth_header(client)
        data = client.get("/api/v1/stats/quality", headers=headers).json()
        assert isinstance(data["by_workflow"], list)
        # Should have at least the seeded workflows
        assert len(data["by_workflow"]) >= 1
        wf = data["by_workflow"][0]
        assert "workflow_id" in wf
        assert "workflow_name" in wf
        assert "total_outputs" in wf
        assert "adopted_count" in wf
        assert "yield_rate" in wf

    def test_task_stats_fields(self, client: TestClient) -> None:
        headers = _auth_header(client)
        data = client.get("/api/v1/stats/quality", headers=headers).json()
        ts = data["task_stats"]
        assert isinstance(ts["total"], int)
        assert isinstance(ts["succeeded"], int)
        assert isinstance(ts["failed"], int)
        assert isinstance(ts["canceled"], int)
        assert isinstance(ts["queued"], int)
        assert isinstance(ts["running"], int)

    def test_top_defects_list(self, client: TestClient) -> None:
        headers = _auth_header(client)
        data = client.get("/api/v1/stats/quality", headers=headers).json()
        assert isinstance(data["top_defects"], list)

    def test_requires_auth(self) -> None:
        from app.main import app

        raw = TestClient(app, raise_server_exceptions=False)
        r = raw.get("/api/v1/stats/quality")
        assert r.status_code == 401

    def test_limit_defects_param(self, client: TestClient) -> None:
        headers = _auth_header(client)
        data = client.get(
            "/api/v1/stats/quality?limit_defects=3", headers=headers
        ).json()
        assert len(data["top_defects"]) <= 3
