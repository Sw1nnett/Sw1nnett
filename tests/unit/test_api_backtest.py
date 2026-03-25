"""Tests for the backtest REST API endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

# Skip the entire module if the cryptography native backend is broken.
# jose → cryptography → _rust extension → panics when _cffi_backend is missing.
try:
    import _cffi_backend  # noqa: F401
    _CFFI_OK = True
except ImportError:
    _CFFI_OK = False

if not _CFFI_OK:
    pytest.skip(
        "cryptography native backend (_cffi_backend) not available; skipping API tests",
        allow_module_level=True,
    )


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def client():
    """Return a TestClient for the FastAPI app without live engines."""
    from fastapi.testclient import TestClient
    from apps.api.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def auth_token(client):
    """Obtain a JWT token using the default test password."""
    import os
    os.environ["API_ADMIN_PASSWORD"] = "testpass"
    resp = client.post("/auth/token", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ #
# /backtest/run
# ------------------------------------------------------------------ #

class TestBacktestRun:
    def test_run_returns_metrics(self, client, auth_token):
        resp = client.post(
            "/backtest/run",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["SPY"],
                "n_bars": 300,
                "seed": 1,
                "warmup_bars": 30,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "metrics" in data
        assert "total_trades" in data
        assert "initial_capital" in data
        assert data["strategy"] == "MomentumBreakoutStrategy"

    def test_run_with_custom_params(self, client, auth_token):
        resp = client.post(
            "/backtest/run",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["AAPL"],
                "params": {"breakout_period": 15, "rvol_threshold": 1.2},
                "n_bars": 300,
                "warmup_bars": 30,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 200

    def test_run_requires_auth(self, client):
        resp = client.post("/backtest/run", json={"strategy": "MomentumBreakoutStrategy"})
        assert resp.status_code == 403  # no bearer → 403 from HTTPBearer

    def test_run_multiple_symbols(self, client, auth_token):
        resp = client.post(
            "/backtest/run",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["SPY", "QQQ"],
                "n_bars": 300,
                "warmup_bars": 30,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 200
        assert set(resp.json()["symbols"]) == {"SPY", "QQQ"}


# ------------------------------------------------------------------ #
# /backtest/optimize
# ------------------------------------------------------------------ #

class TestBacktestOptimize:
    def test_optimize_returns_ranked(self, client, auth_token):
        resp = client.post(
            "/backtest/optimize",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["SPY"],
                "param_grid": {"breakout_period": [10, 20]},
                "n_bars": 300,
                "warmup_bars": 30,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "ranked" in data
        assert data["total_combinations"] == 2
        assert len(data["ranked"]) <= 20

    def test_optimize_empty_grid_rejected(self, client, auth_token):
        resp = client.post(
            "/backtest/optimize",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["SPY"],
                "param_grid": {},
                "n_bars": 300,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 400

    def test_optimize_requires_auth(self, client):
        resp = client.post("/backtest/optimize", json={})
        assert resp.status_code == 403


# ------------------------------------------------------------------ #
# /backtest/walk-forward
# ------------------------------------------------------------------ #

class TestBacktestWalkForward:
    def test_walk_forward_returns_splits(self, client, auth_token):
        resp = client.post(
            "/backtest/walk-forward",
            json={
                "strategy": "MomentumBreakoutStrategy",
                "symbols": ["SPY"],
                "n_bars": 1000,
                "n_splits": 3,
                "train_pct": 0.7,
                "warmup_bars": 30,
            },
            headers=_headers(auth_token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "splits" in data
        assert "avg_sharpe" in data
        assert "consistency_score" in data
        assert data["n_splits"] == len(data["splits"])

    def test_walk_forward_requires_auth(self, client):
        resp = client.post("/backtest/walk-forward", json={})
        assert resp.status_code == 403
