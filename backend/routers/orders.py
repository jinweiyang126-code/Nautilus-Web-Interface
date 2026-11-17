from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
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
    # Target venue: okx / binance / bybit (or display name OKX / Binance / Bybit)
    exchange: Optional[str] = Field(None, max_length=32)
    adapter_id: Optional[str] = Field(None, max_length=32)


def _local_order_id() -> str:
    return f"ORD-{uuid4().hex[:8].upper()}"


def _okx_cl_ord_id(local_id: str) -> str:
    # OKX clOrdId: 1-32 alphanumeric only (no hyphens)
    return "".join(ch for ch in local_id if ch.isalnum())[:32]


@router.get("/orders/venues")
async def list_order_venues():
    """Connected exchanges that can receive new orders."""
    await live_manager.ensure_connected_from_db()
    venues = live_manager.list_connected_venues()
    return {"venues": venues, "count": len(venues)}


@router.get("/orders")
async def list_orders():
    """List orders: backtest orders + persistent user-created orders."""
    all_orders: List[Dict[str, Any]] = []

    for results in nautilus_system.backtest_results.values():
        for o in results.get("orders", []):
            row = normalize_order(o)
            row["timestamp"] = datetime.now(timezone.utc).isoformat()
            all_orders.append(row)

    # Refresh PENDING live orders from OKX so market fills show as FILLED
    if not live_manager.is_connected():
        await live_manager.ensure_connected_from_db()
    if live_manager.is_connected("okx"):
        for row in await database.list_orders():
            if row.get("status") != "PENDING":
                continue
            venue_id = (row.get("exchange_order_id") or "").strip()
            if not venue_id:
                continue
            try:
                mapped = await live_manager.refresh_okx_order(
                    instrument=row.get("instrument") or "BTC-USDT",
                    exchange_order_id=venue_id,
                )
                if not mapped:
                    continue
                await database.update_order_fill(
                    row["id"],
                    status=mapped["status"],
                    filled_qty=mapped["filled_qty"],
                    price=mapped["price"],
                )
            except Exception:
                continue

    db_orders = await database.list_orders()
    all_orders.extend(db_orders)
    return {"orders": all_orders, "count": len(all_orders)}


@router.post("/orders")
async def create_order(req: OrderCreateRequest, _user: dict = Depends(get_current_user)):
    if req.type == "LIMIT" and (req.price is None or req.price <= 0):
        raise HTTPException(status_code=400, detail="LIMIT orders require price > 0")

    order_dict = req.model_dump()
    local_id = _local_order_id()
    order_dict["client_order_id"] = _okx_cl_ord_id(local_id)
    # Prefer explicit adapter_id; exchange is the UI venue selector
    if not order_dict.get("adapter_id") and order_dict.get("exchange"):
        order_dict["adapter_id"] = order_dict["exchange"]

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
    exchange_name = None
    local_status = "PENDING"
    filled_qty = 0.0
    persist_price = req.price
    if live_manager.is_connected():
        try:
            exchange_result = await live_manager.submit_order(order_dict)
            if isinstance(exchange_result, dict):
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
                exchange_name = exchange_result.get("exchange")
                status_raw = str(exchange_result.get("status") or "PENDING").upper()
                if status_raw in ("FILLED", "CANCELLED", "PENDING"):
                    local_status = status_raw
                filled_qty = float(exchange_result.get("filled_qty") or 0)
                if exchange_result.get("price") is not None:
                    persist_price = float(exchange_result["price"])
                # Market default: if venue accepted but state unknown, treat as filled
                if req.type == "MARKET" and local_status == "PENDING" and exchange_order_id:
                    local_status = "FILLED"
                    filled_qty = float(req.quantity)
        except HTTPException:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Exchange error: {str(exc)}")

    # 5. Persist order (include venue order id + exchange + fill status)
    order = await database.create_order(
        instrument=req.instrument,
        side=req.side,
        order_type=req.type,
        quantity=req.quantity,
        price=persist_price,
        exchange_order_id=exchange_order_id,
        order_id=local_id,
        status=local_status,
        filled_qty=filled_qty,
        exchange=exchange_name,
    )
    await database.log_action(
        action="order_created",
        user_id=_user.get("sub", ""),
        resource=f"order:{order['id']}",
        details=(
            f"instrument={req.instrument} side={req.side} qty={req.quantity} "
            f"price={req.price} exchange_order_id={exchange_order_id}"
        ),
    )
    result: Dict[str, Any] = {"success": True, "order": order}
    if exchange_order_id:
        result["exchange_order_id"] = exchange_order_id
    return result


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, _user: dict = Depends(get_current_user)):
    row = await database.get_order(order_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if row.get("status") != "PENDING":
        raise HTTPException(status_code=400, detail=f"Order {order_id} is already {row.get('status')}")

    venue_id = (row.get("exchange_order_id") or "").strip()
    instrument = row.get("instrument") or "BTC-USDT"

    if venue_id or not str(instrument).endswith(".SIM"):
        if not live_manager.is_connected():
            await live_manager.ensure_connected_from_db()
        if live_manager.is_connected():
            try:
                # Prefer OKX ordId; fall back to alphanumeric client id
                cancel_id = venue_id or _okx_cl_ord_id(order_id)
                result = await live_manager.cancel_order(cancel_id, symbol=instrument)
                err_text = ""
                if isinstance(result, dict) and result.get("success") is False:
                    err_text = str(result.get("error") or result)
                scode = str((result or {}).get("sCode") or "0")
                # 51400 only means "already gone" when we had a real venue ordId.
                # Old local-only IDs often 51400 while the App order is still live.
                already_gone = bool(venue_id) and (
                    "51400" in err_text or scode == "51400"
                )
                if ("51400" in err_text or scode == "51400") and not venue_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot cancel this order on OKX from the website: "
                            "it was placed before venue order IDs were stored. "
                            "Tap 撤单 in the OKX App, then ignore/cancel locally."
                        ),
                    )
                if err_text and not already_gone:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Exchange cancel failed: {err_text}",
                    )
                if scode not in ("0", "", "51400") and not already_gone:
                    raise HTTPException(
                        status_code=502,
                        detail=f"OKX cancel rejected (sCode={scode})",
                    )
            except HTTPException:
                raise
            except Exception as exc:
                err = str(exc)
                if "51400" in err and venue_id:
                    pass  # venue says already gone → continue to local cancel
                elif "51400" in err and not venue_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot cancel this order on OKX from the website: "
                            "it was placed before venue order IDs were stored. "
                            "Tap 撤单 in the OKX App, then ignore/cancel locally."
                        ),
                    ) from exc
                else:
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Failed to cancel on exchange: {exc}. "
                            "Local status was NOT changed — cancel again or use OKX App."
                        ),
                    ) from exc
        elif venue_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No live adapter session to cancel on OKX. "
                    "Reconnect OKX in Adapters, or cancel in the OKX App."
                ),
            )

    cancelled = await database.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found or already closed")
    await database.log_action(
        action="order_cancelled",
        user_id=_user.get("sub", ""),
        resource=f"order:{order_id}",
        details=f"exchange_order_id={venue_id}",
    )
    return {"success": True, "message": f"Order {order_id} cancelled"}
