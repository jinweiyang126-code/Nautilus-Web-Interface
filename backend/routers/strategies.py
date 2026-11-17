"""
Strategies router — full CRUD with SQLite persistence.
Supports: sma_crossover, rsi, macd
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import database
from auth_jwt import get_current_user
import state as _state


def _nautilus():
    """Accessor so tests can patch state.nautilus_system at runtime."""
    return _state.nautilus_system


def _live_runner():
    return _state.live_strategy_runner

router = APIRouter(prefix="/api", tags=["strategies"])

# ── Supported strategy types ──────────────────────────────────────────────────

_STRATEGY_TYPES = {
    "sma_crossover": {
        "label": "SMA Crossover",
        "description": "Buy when fast SMA crosses above slow SMA, sell on cross-down.",
        "default_config": {
            "instrument_id": "EUR/USD.SIM",
            "bar_type": "EUR/USD.SIM-1-MINUTE-BID-INTERNAL",
            "fast_period": 10,
            "slow_period": 20,
            "trade_size": "100000",
        },
    },
    "rsi": {
        "label": "RSI Mean-Reversion",
        "description": "Enter long on RSI oversold cross-up, short on overbought cross-down.",
        "default_config": {
            "instrument_id": "EUR/USD.SIM",
            "bar_type": "EUR/USD.SIM-1-MINUTE-BID-INTERNAL",
            "rsi_period": 14,
            "oversold_level": 30.0,
            "overbought_level": 70.0,
            "trade_size": "100000",
        },
    },
    "macd": {
        "label": "MACD Crossover",
        "description": "Buy when MACD line crosses above signal line, sell on cross-down.",
        "default_config": {
            "instrument_id": "EUR/USD.SIM",
            "bar_type": "EUR/USD.SIM-1-MINUTE-BID-INTERNAL",
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "trade_size": "100000",
        },
    },
}


class StrategyCreateRequest(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=100)
    type: str = "sma_crossover"
    description: str = ""
    instrument_id: str = "EUR/USD.SIM"
    bar_type: str = "EUR/USD.SIM-1-MINUTE-BID-INTERNAL"
    # SMA params
    fast_period: int = Field(10, ge=1, le=500)
    slow_period: int = Field(20, ge=1, le=500)
    # RSI params
    rsi_period: int = Field(14, ge=2, le=200)
    oversold_level: float = Field(30.0, ge=1, le=49)
    overbought_level: float = Field(70.0, ge=51, le=99)
    # MACD params
    signal_period: int = Field(9, ge=1, le=200)
    trade_size: str = "100000"


class StrategyStartRequest(BaseModel):
    """Optional body for POST /strategies/{id}/start.

    Live order size/instrument come from the strategy config (trade_size,
    instrument_id / live_instrument). Request fields only override when set.
    """
    live: bool = False
    exchange: Optional[str] = None  # defaults to strategy live_exchange, else okx
    instrument: Optional[str] = None
    quantity: Optional[float] = Field(None, gt=0, le=1000)
    bar: str = "1m"
    interval_sec: int = Field(60, ge=15, le=3600)


def _resolve_live_instrument(config: Dict[str, Any], override: Optional[str] = None) -> str:
    """Pick OKX spot instrument from request override or strategy config."""
    if override and str(override).strip():
        return str(override).strip().replace("/", "-").upper().split(".")[0]
    for key in ("live_instrument", "instrument_id", "instrument"):
        raw = str(config.get(key) or "").strip()
        if not raw:
            continue
        # Skip backtest SIM symbols — not tradeable on OKX
        if ".SIM" in raw.upper() or raw.upper().startswith("EUR/USD"):
            continue
        return raw.replace("/", "-").upper().split(".")[0]
    raise HTTPException(
        status_code=400,
        detail=(
            "This strategy has no OKX-tradable instrument "
            "(got a backtest symbol like EUR/USD.SIM, or instrument missing). "
            "Create a new strategy with Instrument e.g. BTC-USDT and Trade size e.g. 0.01, "
            "then Start that one — do not start old backtest-only strategies."
        ),
    )


def _resolve_live_quantity(config: Dict[str, Any], override: Optional[float] = None) -> float:
    """Order size must come from strategy trade_size (or explicit override)."""
    if override is not None:
        qty = float(override)
    else:
        raw = config.get("trade_size")
        if raw is None or str(raw).strip() == "":
            raise HTTPException(
                status_code=400,
                detail="Strategy trade_size is required for live trading. Set it when creating the strategy.",
            )
        try:
            qty = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid strategy trade_size: {raw!r}",
            ) from exc
    if qty <= 0:
        raise HTTPException(status_code=400, detail="trade_size must be > 0")
    # FX backtest defaults (e.g. 100000) are not valid OKX BTC base sizes
    if qty > 100:
        raise HTTPException(
            status_code=400,
            detail=(
                f"trade_size={qty} looks like an FX lot size, not a crypto base quantity. "
                "Update the strategy trade_size (e.g. 0.01 for BTC-USDT) then start again."
            ),
        )
    return qty


def _db_row_to_strategy(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a DB row into the dict shape used by _nautilus().strategies."""
    cfg = {}
    try:
        cfg = json.loads(row.get("config", "{}"))
    except Exception:
        pass
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "status": row.get("status", "stopped"),
        "description": row.get("description", ""),
        "config": cfg,
    }


async def load_strategies_from_db() -> None:
    """Called at startup to restore persisted strategies into _nautilus()."""
    rows = await database.list_strategies()
    for row in rows:
        sid = row["id"]
        if sid not in _nautilus().strategies:
            s = _db_row_to_strategy(row)
            _nautilus().strategies[sid] = s
            # Register with NautilusTrader engine (SMA and RSI supported)
            if s["type"] in ("sma_crossover", "rsi", "macd"):
                cfg = s["config"]
                _nautilus().create_strategy({
                    "id": sid,
                    "name": s["name"],
                    "type": s["type"],
                    # common
                    "instrument_id": cfg.get("instrument_id", "EUR/USD.SIM"),
                    "bar_type": cfg.get("bar_type", "EUR/USD.SIM-1-MINUTE-BID-INTERNAL"),
                    "trade_size": cfg.get("trade_size", "100000"),
                    # SMA params
                    "fast_period": cfg.get("fast_period", 10),
                    "slow_period": cfg.get("slow_period", 20),
                    # RSI params
                    "rsi_period": cfg.get("rsi_period", 14),
                    "oversold_level": cfg.get("oversold_level", 30.0),
                    "overbought_level": cfg.get("overbought_level", 70.0),
                    # MACD params
                    "signal_period": cfg.get("signal_period", 9),
                })
                # Restore the persisted status after create (create sets to 'created')
                if sid in _nautilus().strategies:
                    _nautilus().strategies[sid]["status"] = s["status"]


# ── Strategy types metadata endpoint ─────────────────────────────────────────

@router.get("/strategy-types")
async def list_strategy_types():
    types_list = [{"id": k, **v} for k, v in _STRATEGY_TYPES.items()]
    return {"types": types_list, "strategy_types": types_list}


# ── Nautilus native endpoints ─────────────────────────────────────────────────

@router.get("/nautilus/strategies")
async def nautilus_list_strategies():
    strategies = _nautilus().get_all_strategies()
    return {"success": True, "strategies": strategies, "count": len(strategies)}


@router.get("/nautilus/strategies/{strategy_id}")
async def nautilus_get_strategy(strategy_id: str):
    strategy = _nautilus().get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    return {"success": True, "strategy": strategy}


@router.post("/nautilus/strategies")
async def nautilus_create_strategy(request: StrategyCreateRequest, _user: dict = Depends(get_current_user)):
    result = _nautilus().create_strategy(request.model_dump())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── UI-facing endpoints ───────────────────────────────────────────────────────

@router.get("/strategies")
async def list_strategies():
    # Check daily loss limit and auto-stop running strategies if exceeded
    try:
        from risk_engine import risk_engine
        await risk_engine.check_daily_loss_auto_stop()
    except Exception:
        pass  # Never fail the list endpoint due to risk check errors
    strategies = _nautilus().get_all_strategies()
    result = []
    for strategy in strategies:
        br = _nautilus().get_backtest_results(strategy["id"])
        cfg = strategy.get("config") or {}
        if hasattr(cfg, "instrument_id"):
            instrument = str(cfg.instrument_id)
        elif isinstance(cfg, dict):
            instrument = cfg.get("instrument_id", "EUR/USD.SIM")
        else:
            instrument = "EUR/USD.SIM"
        # Merge backtest results with live DB orders for up-to-date performance
        db_orders = await database.list_orders()
        filled_orders = [o for o in db_orders if o.get("status") == "filled"]
        db_pnl = sum(float(o.get("pnl") or 0) for o in filled_orders)
        db_trades = len(filled_orders)
        total_pnl = br.get("total_pnl", 0.0) if br else 0.0
        total_trades = br.get("total_trades", 0) if br else 0
        # Use DB values if greater (live orders supplement backtest)
        if db_pnl != 0.0 or db_trades > 0:
            total_pnl = db_pnl
            total_trades = db_trades
        live_status = None
        try:
            live_status = _live_runner().get_status(strategy["id"])
        except Exception:
            live_status = None
        cfg_dict = cfg if isinstance(cfg, dict) else {}
        result.append(
            {
                "id": strategy["id"],
                "name": strategy["name"],
                "type": strategy["type"],
                "status": strategy["status"],
                "description": strategy.get("description", ""),
                "instrument": instrument,
                "trade_size": cfg_dict.get("trade_size"),
                "live_exchange": cfg_dict.get("live_exchange") or cfg_dict.get("exchange"),
                "config": cfg_dict,
                "live": live_status,
                "performance": {
                    "total_pnl": total_pnl,
                    "total_trades": total_trades,
                    "win_rate": (br.get("win_rate", 0.0) / 100.0) if br else 0.0,
                },
            }
        )
    return {"strategies": result, "count": len(result)}


@router.post("/strategies")
async def create_strategy(body: Dict[str, Any] = Body(...), _user: dict = Depends(get_current_user)):
    strategy_type = body.get("type", "sma_crossover")
    if strategy_type not in _STRATEGY_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy type: {strategy_type}")

    name = body.get("name", "")
    if not name or not str(name).strip():
        raise HTTPException(status_code=422, detail="'name' is required and must not be empty")

    # SMA period validation
    if strategy_type == "sma_crossover":
        fast = int(body.get("fast_period") or 10)
        slow = int(body.get("slow_period") or 20)
        if fast >= slow:
            raise HTTPException(
                status_code=422,
                detail=f"fast_period ({fast}) must be less than slow_period ({slow})",
            )

    # RSI validation
    if strategy_type == "rsi":
        rsi_period = int(body.get("rsi_period") or 14)
        oversold = float(body.get("oversold_level") or 30.0)
        overbought = float(body.get("overbought_level") or 70.0)
        if rsi_period < 2:
            raise HTTPException(status_code=422, detail="rsi_period must be >= 2")
        if oversold >= overbought:
            raise HTTPException(
                status_code=422,
                detail=f"oversold_level ({oversold}) must be less than overbought_level ({overbought})",
            )

    # MACD validation
    if strategy_type == "macd":
        fast = int(body.get("fast_period") or 12)
        slow = int(body.get("slow_period") or 26)
        if fast >= slow:
            raise HTTPException(
                status_code=422,
                detail=f"MACD fast_period ({fast}) must be less than slow_period ({slow})",
            )

    defaults = _STRATEGY_TYPES[strategy_type]["default_config"].copy()
    sid = body.get("id") or f"STR-{uuid.uuid4().hex[:8].upper()}"
    description = body.get("description", _STRATEGY_TYPES[strategy_type]["description"])

    # Build config from request body, falling back to defaults for missing/null values
    config = {k: (body.get(k) if body.get(k) is not None else defaults[k]) for k in defaults}
    # Live trading venue (Orders-style selector); not part of backtest defaults
    live_exchange = body.get("live_exchange") or body.get("exchange")
    if live_exchange:
        config["live_exchange"] = str(live_exchange).strip().lower()

    now = datetime.now(timezone.utc).isoformat()
    strategy_row = {
        "id": sid,
        "name": name,
        "type": strategy_type,
        "status": "stopped",
        "description": description,
        "config": config,
        "created_at": now,
        "updated_at": now,
    }

    # Persist to DB
    await database.save_strategy(strategy_row)

    # Register with Nautilus engine (supports sma_crossover and rsi)
    result = _nautilus().create_strategy({**config, "id": sid, "name": name, "type": strategy_type})
    # Validate result is a real dict with a string strategy_id (guards against MagicMock in tests)
    result_sid = result.get("strategy_id") if isinstance(result, dict) else None
    if result_sid and isinstance(result_sid, str) and result.get("success"):
        actual_sid = result_sid
        # If nautilus assigned a different ID, update DB
        if actual_sid != sid:
            strategy_row["id"] = actual_sid
            await database.delete_strategy(sid)
            await database.save_strategy(strategy_row)
        sys = _nautilus()
        if hasattr(sys, "strategies") and isinstance(sys.strategies, dict) and actual_sid in sys.strategies:
            sys.strategies[actual_sid]["description"] = description
            sys.strategies[actual_sid]["status"] = "stopped"
        return {"success": True, "strategy_id": actual_sid, "strategy": strategy_row}

    return {"success": True, "strategy_id": sid, "strategy": strategy_row}


@router.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str, _user: dict = Depends(get_current_user)):
    if not await _strategy_exists(strategy_id):
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    try:
        await _live_runner().stop(strategy_id)
    except Exception:
        pass
    sys = _nautilus()
    if hasattr(sys, "strategies") and isinstance(sys.strategies, dict) and strategy_id in sys.strategies:
        del sys.strategies[strategy_id]
    await database.delete_strategy(strategy_id)
    return {"success": True, "message": f"Strategy {strategy_id} deleted"}


async def _strategy_exists(strategy_id: str) -> bool:
    """Check if a strategy exists — in-memory first, DB as fallback."""
    sys = _nautilus()
    if hasattr(sys, "strategies") and isinstance(sys.strategies, dict):
        if strategy_id in sys.strategies:
            return True
    # Fallback: check DB (handles mocked nautilus_system in tests)
    rows = await database.list_strategies()
    return any(r["id"] == strategy_id for r in rows)


@router.post("/strategies/{strategy_id}/start")
async def start_strategy(
    strategy_id: str,
    body: Optional[StrategyStartRequest] = Body(default=None),
    _user: dict = Depends(get_current_user),
):
    if not await _strategy_exists(strategy_id):
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    _nautilus().start_strategy(strategy_id)
    await database.update_strategy_status(strategy_id, "running")

    live_info = None
    req = body or StrategyStartRequest()
    if req.live:
        # Prefer DB config (authoritative); merge in-memory fields if present
        strategy_type = "sma_crossover"
        config: Dict[str, Any] = {}
        db_rows = await database.list_strategies()
        match = next((r for r in db_rows if r["id"] == strategy_id), None)
        if match:
            strategy_type = match.get("type") or strategy_type
            try:
                raw_cfg = match.get("config") or "{}"
                config = json.loads(raw_cfg) if isinstance(raw_cfg, str) else dict(raw_cfg or {})
            except Exception:
                config = {}
        sys = _nautilus()
        if hasattr(sys, "strategies") and isinstance(sys.strategies, dict) and strategy_id in sys.strategies:
            row = sys.strategies[strategy_id]
            strategy_type = row.get("type") or strategy_type
            cfg = row.get("config") or {}
            if isinstance(cfg, dict):
                # DB wins on conflicts only when memory has empty values
                merged = dict(cfg)
                merged.update({k: v for k, v in config.items() if v not in (None, "")})
                # Prefer non-SIM instrument_id from either side
                for key in ("instrument_id", "live_instrument", "trade_size"):
                    mem_v = cfg.get(key) if isinstance(cfg, dict) else None
                    db_v = config.get(key)
                    if db_v not in (None, ""):
                        merged[key] = db_v
                    elif mem_v not in (None, ""):
                        merged[key] = mem_v
                config = merged
            elif hasattr(cfg, "__dict__"):
                for k, v in dict(cfg.__dict__).items():
                    if k not in config or config.get(k) in (None, ""):
                        config[k] = v

        instrument = _resolve_live_instrument(config, req.instrument)
        qty = _resolve_live_quantity(config, req.quantity)
        exchange = str(
            req.exchange
            or config.get("live_exchange")
            or config.get("exchange")
            or "okx"
        ).strip().lower()

        try:
            live_info = await _live_runner().start(
                strategy_id=strategy_id,
                strategy_type=strategy_type,
                config=config,
                exchange=exchange,
                instrument=instrument,
                quantity=qty,
                bar=req.bar,
                interval_sec=req.interval_sec,
            )
        except Exception as exc:
            # Roll back status so UI is not stuck on "running"
            try:
                _nautilus().stop_strategy(strategy_id)
            except Exception:
                pass
            await database.update_strategy_status(strategy_id, "stopped")
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "message": f"Strategy {strategy_id} started",
        "live_engine_registered": True,
        "live": live_info,
    }


@router.post("/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str, _user: dict = Depends(get_current_user)):
    if not await _strategy_exists(strategy_id):
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    try:
        await _live_runner().stop(strategy_id)
    except Exception:
        pass
    _nautilus().stop_strategy(strategy_id)
    await database.update_strategy_status(strategy_id, "stopped")
    return {
        "success": True,
        "message": f"Strategy {strategy_id} stopped",
        "live_engine_registered": False,
    }
