"""Test 安全鉴权 — auth_token 非空时 /api 要求 X-Auth-Token。"""
from fastapi.testclient import TestClient

import serving.interfaces.app as app_mod
import serving.interfaces.project_routes as routes_mod
from serving.interfaces.app import create_app


def test_token_required_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "auth_token", lambda: "secret-123")
    monkeypatch.setattr(routes_mod, "WAREHOUSE", tmp_path)
    client = TestClient(create_app())

    # 无 token → 401
    r = client.get("/api/checkpoint/latest")
    assert r.status_code == 401
    # 错误 token → 401
    r = client.get("/api/checkpoint/latest",
                   headers={"X-Auth-Token": "wrong"})
    assert r.status_code == 401
    # 正确 token → 放行
    r = client.get("/api/checkpoint/latest",
                   headers={"X-Auth-Token": "secret-123"})
    assert r.status_code == 200


def test_no_token_configured_means_open(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "auth_token", lambda: "")
    monkeypatch.setattr(routes_mod, "WAREHOUSE", tmp_path)
    client = TestClient(create_app())
    r = client.get("/api/checkpoint/latest")
    assert r.status_code == 200
