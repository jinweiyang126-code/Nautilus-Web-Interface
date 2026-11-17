"""
Positions router.

Open positions come from:
1. Live exchange balances when an adapter is connected (preferred)
2. Otherwise the DB positions table / backtest results

The UI-layer close action marks them as closed in the DB (and may send a
closing order when live).
"""

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

import database
from auth_jwt import get_current_user
import market_data_service as svc
from state import live_manager, nautilus_system

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["positions"])


def _norm_symbol(instrument: str) -> str:
    return (instrument or "").upper().replace("-", "").replace("/", "").split(".")[0]


async def _enrich_current_prices(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best-effort: add current_price and live unrealized_pnl to each position."""
    if not positions:
        return positions

    symbols_needed = set()
    for p in positions:
        sym = _norm_symbol(p.get("instrument", ""))
        if sym in svc.SYMBOLS:
            symbols_needed.add(sym)

    if not symbols_needed:
        return positions

    price_map: Dict[str, float] = {}

    async def _fetch(sym: str) -> None:
        try:
            data = await svc.get_symbol_data(sym)
            price_map[sym] = data.get("price", 0.0)
        except Exception:
            pass

    await asyncio.gather(*[_fetch(s) for s in symbols_needed])

    enriched = []
    for pos in positions:
        p = dict(pos)
        sym = _norm_symbol(p.get("instrument", ""))
        if sym in price_map and price_map[sym] > 0:
            current = price_map[sym]
            p["current_price"] = current
            entry = float(p.get("entry_price") or 0)
            qty = float(p.get("quantity") or 0)
            if entry > 0 and qty > 0:
                side = str(p.get("side", "")).upper()
                multiplier = 1.0 if "LONG" in side or "BUY" in side else -1.0
                p["pnl"] = round((current - entry) * qty * multiplier, 4)
        enriched.append(p)
    return enriched


def _normalize_live_position(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure live sync rows have fields the Positions UI expects."""
    exchange = str(raw.get("exchange") or "LIVE").upper()
    instrument = str(raw.get("instrument") or "UNKNOWN")
    asset_key = instrument.replace("-", "").replace("/", "")
    return {
        "id": raw.get("id") or f"POS-{exchange}-{asset_key}",
        "instrument": instrument,
        "side": raw.get("side") or "LONG",
        "quantity": float(raw.get("quantity") or 0),
        "entry_price": raw.get("entry_price"),
        "exit_price": raw.get("exit_price"),
        "pnl": float(raw.get("pnl") or 0),
        "is_open": True,
        "opened_at": raw.get("opened_at"),
        "timestamp": raw.get("opened_at") or raw.get("timestamp"),
        "exchange": exchange,
        "source": "live",
        "free": raw.get("free"),
        "locked": raw.get("locked"),
        "demo": raw.get("demo"),
    }


@router.get("/positions")
async def list_positions():
    """
    List open positions.
    When a live adapter is connected, sync balances from the exchange first.
    """
    if not live_manager.is_connected():
        await live_manager.ensure_connected_from_db()

    positions: List[Dict[str, Any]] = []
    source = "cached"

    if live_manager.is_connected():
        try:
            live_positions = await live_manager.sync_positions()
            positions = [
                _normalize_live_position(p)
                for p in live_positions
                if float(p.get("quantity") or 0) > 0
            ]
            # Persist for close/history; mark as open
            if positions:
                await database.save_positions(
                    [{**p, "is_open": True} for p in positions],
                    strategy_id="live",
                )
            source = "live"
        except Exception as exc:
            logger.warning("Live position sync failed: %s", exc)

    if not positions:
        db_positions = await database.list_db_positions(open_only=True)
        positions = db_positions
        if not positions:
            for results in nautilus_system.backtest_results.values():
                positions.extend(
                    p for p in results.get("positions", []) if p.get("is_open", False)
                )

    enriched = await _enrich_current_prices(positions)
    for pos in enriched:
        pos["source"] = pos.get("source") or source
    return {"positions": enriched, "count": len(enriched), "source": source}


@router.post("/positions/sync")
async def sync_positions(_user: dict = Depends(get_current_user)):
    """Sync open positions from the connected exchange."""
    if not live_manager.is_connected():
        await live_manager.ensure_connected_from_db()
    live_positions = await live_manager.sync_positions()
    normalized = [
        _normalize_live_position(p)
        for p in live_positions
        if float(p.get("quantity") or 0) > 0
    ]
    if normalized:
        await database.save_positions(
            [{**p, "is_open": True} for p in normalized],
            strategy_id="live",
        )

    return {
        "success": True,
        "synced_count": len(normalized),
        "positions": normalized,
    }


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, _user: dict = Depends(get_current_user)):
    # If adapter connected, send a close order to the exchange
    if live_manager.is_connected():
        try:
            positions = await database.list_db_positions(open_only=False)
            target = next((p for p in positions if p["id"] == position_id), None)
            close_side = "SELL"
            instrument = "UNKNOWN"
            quantity = 0.0
            if target:
                instrument = target.get("instrument", "UNKNOWN")
                quantity = float(target.get("quantity", 0))
                pos_side = str(target.get("side", "LONG")).upper()
                close_side = "SELL" if "LONG" in pos_side or "BUY" in pos_side else "BUY"

            await live_manager.submit_order({
                "instrument": instrument,
                "side": close_side,
                "type": "MARKET",
                "quantity": quantity,
            })
        except Exception as exc:
            logger.warning("Exchange close order failed: %s", exc)

    closed = await database.close_db_position(position_id)
    return {"success": True, "closed_in_db": closed, "message": f"Position {position_id} closed"}
