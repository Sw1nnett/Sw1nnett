"""FastAPI control-plane API."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from packages.core.config import get_settings
from packages.observability.logging import configure_logging, get_logger


log = get_logger(__name__)
settings = get_settings()
configure_logging(settings.log_level, settings.log_dir)

app = FastAPI(
    title="Trading Bot Control Plane",
    description="Operator API for the modular day-trading platform",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# ------------------------------------------------------------------ #
# In-memory shared state (injected by the trader process in production)
# ------------------------------------------------------------------ #
_risk_engine = None
_execution_engine = None
_portfolio_tracker = None
_market_data_worker = None


def set_engines(risk, execution, portfolio, market_data) -> None:
    global _risk_engine, _execution_engine, _portfolio_tracker, _market_data_worker
    _risk_engine = risk
    _execution_engine = execution
    _portfolio_tracker = portfolio
    _market_data_worker = market_data


# ------------------------------------------------------------------ #
# Auth
# ------------------------------------------------------------------ #
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


def _create_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    data.update({"exp": expire})
    return jwt.encode(data, settings.api_secret_key, algorithm=ALGORITHM)


def _verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials, settings.api_secret_key, algorithms=[ALGORITHM]
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ------------------------------------------------------------------ #
# IP allowlist middleware
# ------------------------------------------------------------------ #
@app.middleware("http")
async def ip_allowlist_middleware(request: Request, call_next):
    allowed = settings.allowed_ips
    if "*" in allowed:
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in allowed:
        log.warning("ip_blocked", ip=client_ip)
        return JSONResponse(status_code=403, content={"detail": "IP not allowed"})
    return await call_next(request)


# ------------------------------------------------------------------ #
# Auth endpoints
# ------------------------------------------------------------------ #
class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/token")
async def login(body: LoginRequest):
    # In production: verify against a proper user store
    expected_pass = os.environ.get("API_ADMIN_PASSWORD", "changeme")
    if body.username != "admin" or body.password != expected_pass:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _create_token({"sub": body.username, "role": "operator"})
    return {"access_token": token, "token_type": "bearer"}


# ------------------------------------------------------------------ #
# Health
# ------------------------------------------------------------------ #
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------ #
# Status
# ------------------------------------------------------------------ #
@app.get("/status")
async def status_endpoint(claims: dict = Depends(_verify_token)):
    return {
        "trading_mode": settings.trading_mode.value,
        "broker": settings.broker.value,
        "kill_switch_active": (
            await _risk_engine.get_risk_summary() if _risk_engine else {"kill_switch_active": "unknown"}
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ #
# Risk
# ------------------------------------------------------------------ #
@app.get("/risk")
async def get_risk(claims: dict = Depends(_verify_token)):
    if not _risk_engine:
        return {"error": "risk engine not connected"}
    return await _risk_engine.get_risk_summary()


class KillSwitchRequest(BaseModel):
    confirm: bool = False
    reason: str = "manual"


@app.post("/risk/kill-switch/activate")
async def activate_kill_switch(
    body: KillSwitchRequest,
    claims: dict = Depends(_verify_token),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to activate kill switch")
    if not _risk_engine:
        raise HTTPException(status_code=503, detail="Risk engine not connected")
    actor = claims.get("sub", "api")
    await _risk_engine.trigger_kill_switch(reason=body.reason, actor=actor)
    log.critical("kill_switch_activated_via_api", actor=actor, reason=body.reason)
    return {"status": "kill_switch_activated", "reason": body.reason}


@app.post("/risk/kill-switch/deactivate")
async def deactivate_kill_switch(claims: dict = Depends(_verify_token)):
    if not _risk_engine:
        raise HTTPException(status_code=503, detail="Risk engine not connected")
    actor = claims.get("sub", "api")
    await _risk_engine.reset_kill_switch(actor=actor)
    return {"status": "kill_switch_deactivated"}


# ------------------------------------------------------------------ #
# Positions
# ------------------------------------------------------------------ #
@app.get("/positions")
async def get_positions(claims: dict = Depends(_verify_token)):
    if not _portfolio_tracker:
        return {"positions": []}
    return {
        "positions": [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pnl": float(p.unrealized_pnl),
                "side": p.side,
            }
            for p in _portfolio_tracker.positions.values()
        ]
    }


# ------------------------------------------------------------------ #
# Orders
# ------------------------------------------------------------------ #
@app.get("/orders")
async def get_orders(claims: dict = Depends(_verify_token)):
    if not _execution_engine:
        return {"orders": []}
    return {
        "orders": [
            {
                "client_order_id": o.client_order_id,
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "status": o.status.value,
                "strategy_id": o.strategy_id,
            }
            for o in _execution_engine.pending_orders.values()
        ]
    }


@app.get("/orders/blocked")
async def get_blocked_orders(claims: dict = Depends(_verify_token)):
    if not _execution_engine:
        return {"blocked": []}
    return {"blocked": _execution_engine.blocked_orders}


# ------------------------------------------------------------------ #
# Flatten all
# ------------------------------------------------------------------ #
@app.post("/flatten-all")
async def flatten_all(
    body: KillSwitchRequest,
    claims: dict = Depends(_verify_token),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to flatten all")
    if not _execution_engine:
        raise HTTPException(status_code=503, detail="Execution engine not connected")
    orders = await _execution_engine.flatten_all()
    log.warning("flatten_all_via_api", actor=claims.get("sub"), orders=len(orders))
    return {"status": "flatten_initiated", "orders_submitted": len(orders)}


# ------------------------------------------------------------------ #
# Reports
# ------------------------------------------------------------------ #
@app.get("/reports/daily")
async def daily_report(claims: dict = Depends(_verify_token)):
    if not _portfolio_tracker:
        return {}
    report = _portfolio_tracker.generate_daily_report()
    return {
        "date": report.date.isoformat(),
        "total_trades": report.total_trades,
        "winning_trades": report.winning_trades,
        "losing_trades": report.losing_trades,
        "gross_pnl": float(report.gross_pnl),
        "net_pnl": float(report.net_pnl),
        "total_commission": float(report.total_commission),
    }


# ------------------------------------------------------------------ #
# Metrics summary
# ------------------------------------------------------------------ #
@app.get("/metrics/summary")
async def metrics_summary(claims: dict = Depends(_verify_token)):
    if not _portfolio_tracker:
        return {}
    return {
        "equity": float(_portfolio_tracker.equity),
        "cash": float(_portfolio_tracker.cash),
        "realized_pnl": float(_portfolio_tracker.realized_pnl),
        "open_positions": len(_portfolio_tracker.positions),
    }
