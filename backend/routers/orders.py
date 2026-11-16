from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import database
from auth_jwt import get_current_user
from risk_engine import risk_engine, RiskCheckError
from state import live_manager, nautilus_system
from utils import normalize_order

router = APIRouter(prefix="/api", tags=["orders"])


class OrderCreateRequest(BaseModel):
    instrument: str = Field("EUR/USD.SIM", min_length=1, max_length=50)
    side: str = Field("BUY", pattern="^(BUY|SELL)$")
    type: str = Field("MARKET", pattern="^(MARKET|LIMIT|STOP)$")
    quantity: float = Field(..., gt=0)
    price: Optional[float] = Field(None, ge=0)
    leverage: float = Field(1.0, ge=1.0, le=1000.0)


@router.get("/orders")
async def list_orders():
    """List orders: backtest orders + persistent user-created orders."""
    all_orders: List[Dict[str, Any]] = []

    for results in nautilus_system.backtest_results.values():
        for o in results.get("orders", []):
            row = normalize_order(o)
            row["timestamp"] = datetime.now(timezone.utc).isoformat()
            all_orders.append(row)

    db_orders = await database.list_orders()
    all_orders.extend(db_orders)
    return {"orders": all_orders, "count": len(all_orders)}


@router.post("/orders")
async def create_order(req: OrderCreateRequest, _user: dict = Depends(get_current_user)):
    if req.type == "LIMIT" and (req.price is None or req.price <= 0):
        raise HTTPException(status_code=400, detail="LIMIT orders require price > 0")

    order_dict = req.model_dump()

    # 1. Risk check — runs before anything else
    try:
        await risk_engine.check_order(order_dict)
    except RiskCheckError:
        raise  # Re-raise with 422

    # 2. Restore in-memory adapter session from DB if UI still shows Connected
    # but the process restarted (or live_manager was cleared).
    if not live_manager.is_connected() and not req.instrument.endswith(".SIM"):
        await live_manager.ensure_connected_from_db()

    # 3. Require adapter for live-exchange instruments when no risk limits configured.
    # When risk limits have never been explicitly set, the system is in pristine/demo
    # mode and live-instrument orders need a connected adapter.
    # Once limits are explicitly configured, paper trading is permitted on any instrument.
    if not live_manager.is_connected() and not req.instrument.endswith(".SIM"):
        limits_configured = await database.risk_limits_explicitly_set()
        if not limits_configured:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No live adapter session. Open Adapters → OKX → Disconnect, "
                    "then Connect again with Demo API Key / Secret / Passphrase, "
                    "and retry the order."
                ),
            )

    # 4. Live routing when adapter is connected
    exchange_order_id = None
    if live_manager.is_connected():
        try:
            exchange_result = await live_manager.submit_order(order_dict)
            if isinstance(exchange_result, dict):
                # Surface OKX per-order rejection (sCode != 0)
                scode = str(exchange_result.get("sCode") or "0")
                if scode not in ("0", ""):
                    raise HTTPException(
                        status_code=400,
                        detail=f"OKX rejected order (sCode={scode}): {exchange_result.get('sMsg') or 'unknown'}",
                    )
                exchange_order_id = (
                    exchange_result.get("exchange_order_id")
                    or exchange_result.get("order_id")
                )
        except HTTPException:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Exchange error: {str(exc)}")

    # 3. Persist order to DB (paper mode when no adapter, live mode otherwise)
    order = await database.create_order(
        instrument=req.instrument,
        side=req.side,
        order_type=req.type,
        quantity=req.quantity,
        price=req.price,
    )
    await database.log_action(
        action="order_created",
        user_id=_user.get("sub", ""),
        resource=f"order:{order['id']}",
        details=f"instrument={req.instrument} side={req.side} qty={req.quantity} price={req.price}",
    )
    result: Dict[str, Any] = {"success": True, "order": order}
    if exchange_order_id:
        result["exchange_order_id"] = exchange_order_id
    return result


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, _user: dict = Depends(get_current_user)):
    # If adapter is connected, also cancel on exchange
    if live_manager.is_connected():
        try:
            await live_manager.cancel_order(order_id)
        except Exception:
            pass  # Exchange cancel failure should not prevent DB cancel

    cancelled = await database.cancel_order(order_id)
    if not cancelled:
        # Still return success if connected (exchange order may not be in DB)
        if live_manager.is_connected():
            await database.log_action(
                action="order_cancelled",
                user_id=_user.get("sub", ""),
                resource=f"order:{order_id}",
                details="cancel sent to exchange",
            )
            return {"success": True, "message": f"Order {order_id} cancel sent to exchange"}
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found or already closed")
    await database.log_action(
        action="order_cancelled",
        user_id=_user.get("sub", ""),
        resource=f"order:{order_id}",
    )
    return {"success": True, "message": f"Order {order_id} cancelled"}
