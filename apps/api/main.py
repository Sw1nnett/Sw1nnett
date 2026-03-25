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

from packages.backtest.analytics import compute_metrics
from packages.backtest.data_loader import generate_synthetic_bars
from packages.backtest.engine import BacktestEngine
from packages.backtest.optimizer import grid_search
from packages.backtest.walk_forward import run_walk_forward
from packages.core.config import get_settings
from packages.core.models import StrategyConfig
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


# ------------------------------------------------------------------ #
# Backtest API
# ------------------------------------------------------------------ #

class BacktestRequest(BaseModel):
    strategy: str = "MomentumBreakoutStrategy"
    symbols: list[str] = ["SPY"]
    params: dict = {}
    n_bars: int = 1000
    seed: int = 42
    initial_capital: float = 100_000.0
    warmup_bars: int = 60
    commission_per_share: float = 0.005
    slippage_bps: float = 2.0


class OptimizeRequest(BaseModel):
    strategy: str = "MomentumBreakoutStrategy"
    symbols: list[str] = ["SPY"]
    param_grid: dict[str, list[Any]] = {}
    n_bars: int = 1000
    seed: int = 42
    initial_capital: float = 100_000.0
    warmup_bars: int = 60


class WalkForwardRequest(BaseModel):
    strategy: str = "MomentumBreakoutStrategy"
    symbols: list[str] = ["SPY"]
    params: dict = {}
    n_bars: int = 2000
    seed: int = 42
    n_splits: int = 5
    train_pct: float = 0.7
    initial_capital: float = 100_000.0
    warmup_bars: int = 60


@app.post("/backtest/run")
async def run_backtest(body: BacktestRequest, claims: dict = Depends(_verify_token)):
    """Run a full backtest and return metrics + summary."""
    bars_by_symbol = {
        sym: generate_synthetic_bars(sym, n_bars=body.n_bars, seed=body.seed)
        for sym in body.symbols
    }
    cfg = StrategyConfig(
        cls_name=body.strategy,
        symbols=body.symbols,
        params=body.params,
    )
    engine = BacktestEngine(
        strategy_configs=[cfg],
        initial_capital=body.initial_capital,
        commission_per_share=body.commission_per_share,
        slippage_bps=body.slippage_bps,
        warmup_bars=body.warmup_bars,
    )
    try:
        result = engine.run(bars_by_symbol)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    metrics = compute_metrics(result)
    return {
        "strategy": body.strategy,
        "symbols": body.symbols,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "initial_capital": float(result.initial_capital),
        "final_capital": float(result.final_capital),
        "total_trades": len(result.trades),
        "risk_events": len(result.risk_events),
        "blocked_orders": len(result.blocked_orders),
        "metrics": metrics,
    }


@app.post("/backtest/optimize")
async def optimize_strategy(body: OptimizeRequest, claims: dict = Depends(_verify_token)):
    """Grid-search strategy parameters and return ranked results."""
    if not body.param_grid:
        raise HTTPException(status_code=400, detail="param_grid must not be empty")

    bars_by_symbol = {
        sym: generate_synthetic_bars(sym, n_bars=body.n_bars, seed=body.seed)
        for sym in body.symbols
    }
    try:
        result = grid_search(
            strategy_cls=body.strategy,
            symbols=body.symbols,
            bars_by_symbol=bars_by_symbol,
            param_grid=body.param_grid,
            initial_capital=body.initial_capital,
            warmup_bars=body.warmup_bars,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    ranked = result.ranked[:20]  # return top 20
    return {
        "strategy": body.strategy,
        "total_combinations": len(result.runs),
        "best_params": result.best.params if result.best else None,
        "best_sharpe": result.best.sharpe if result.best else None,
        "ranked": [
            {
                "params": r.params,
                "sharpe": r.sharpe,
                "total_return_pct": r.total_return_pct,
                "win_rate_pct": r.win_rate_pct,
                "total_trades": r.total_trades,
            }
            for r in ranked
        ],
    }


@app.post("/backtest/walk-forward")
async def walk_forward(body: WalkForwardRequest, claims: dict = Depends(_verify_token)):
    """Run walk-forward validation and return split results."""
    bars_by_symbol = {
        sym: generate_synthetic_bars(sym, n_bars=body.n_bars, seed=body.seed)
        for sym in body.symbols
    }
    cfg = StrategyConfig(
        cls_name=body.strategy,
        symbols=body.symbols,
        params=body.params,
    )
    try:
        result = run_walk_forward(
            strategy_config=cfg,
            bars_by_symbol=bars_by_symbol,
            n_splits=body.n_splits,
            train_pct=body.train_pct,
            initial_capital=body.initial_capital,
            warmup_bars=body.warmup_bars,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "strategy": body.strategy,
        "n_splits": len(result.splits),
        "avg_sharpe": result.avg_sharpe,
        "avg_return_pct": result.avg_return_pct,
        "avg_win_rate": result.avg_win_rate,
        "consistency_score": result.consistency_score,
        "total_test_trades": result.total_test_trades,
        "splits": [
            {
                "split_index": s.split_index,
                "train_bars": s.train_bars,
                "test_bars": s.test_bars,
                "test_sharpe": s.test_metrics.get("sharpe_ratio", 0.0),
                "test_return_pct": s.test_metrics.get("total_return_pct", 0.0),
                "test_trades": s.test_metrics.get("total_trades", 0),
            }
            for s in result.splits
        ],
    }
