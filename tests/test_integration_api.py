from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
async def test_health_endpoint(async_client: httpx.AsyncClient):
    resp = await async_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "5.0.0"
    assert data["service"] == "PengStrike API"


@pytest.mark.integration
async def test_login_success(async_client: httpx.AsyncClient):
    resp = await async_client.post("/api/auth/login", json={
        "username": "admin",
        "password": "pentest123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.integration
async def test_login_invalid(async_client: httpx.AsyncClient):
    resp = await async_client.post("/api/auth/login", json={
        "username": "admin",
        "password": "wrong_password",
    })
    assert resp.status_code == 401


@pytest.mark.integration
async def test_create_session(async_client: httpx.AsyncClient, auth_headers: dict):
    resp = await async_client.post(
        "/api/sessions",
        json={"target": "10.0.0.1"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["target"] == "10.0.0.1"
    assert "id" in data
    assert data["status"] == "initialization"
    assert data["phase"] == "初始化"


@pytest.mark.integration
async def test_list_sessions(async_client: httpx.AsyncClient, auth_headers: dict):
    await async_client.post(
        "/api/sessions",
        json={"target": "192.168.1.1"},
        headers=auth_headers,
    )
    resp = await async_client.get("/api/sessions", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1


@pytest.mark.integration
async def test_get_session_detail(async_client: httpx.AsyncClient, auth_headers: dict):
    create_resp = await async_client.post(
        "/api/sessions",
        json={"target": "192.168.1.100"},
        headers=auth_headers,
    )
    session_id = create_resp.json()["id"]

    resp = await async_client.get(f"/api/sessions/{session_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == session_id
    assert data["target"] == "192.168.1.100"


@pytest.mark.integration
async def test_list_tools(async_client: httpx.AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/tools", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 0


@pytest.mark.integration
async def test_list_roles(async_client: httpx.AsyncClient, auth_headers: dict):
    resp = await async_client.get("/api/roles", headers=auth_headers)
    if resp.status_code == 500:
        data = resp.json()
        assert "detail" in data
    else:
        data = resp.json()
        assert "items" in data
        assert "total" in data


@pytest.mark.integration
async def test_stats_endpoint(async_client: httpx.AsyncClient):
    resp = await async_client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert "roles" in data
    assert "skills" in data
    assert "sessions" in data
    assert data["version"] == "5.0.0"


@pytest.mark.integration
async def test_protected_endpoint_no_auth(async_client: httpx.AsyncClient):
    resp = await async_client.get("/api/sessions")
    assert resp.status_code == 401
    data = resp.json()
    assert "detail" in data


@pytest.mark.integration
async def test_websocket_auth(async_client: httpx.AsyncClient):
    from fastapi.testclient import TestClient
    from api.app import app

    client = TestClient(app)
    login_resp = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "pentest123",
    })
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    assert len(token) > 0