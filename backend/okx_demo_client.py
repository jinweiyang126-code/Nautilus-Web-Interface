"""
OKX REST client for Demo Trading (and optional live).

Demo Trading uses the same host as production (`https://www.okx.com`) with
header `x-simulated-trading: 1` and a Demo Trading API key.

Auth: HMAC-SHA256 over timestamp + METHOD + requestPath + body, Base64-encoded.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OKX_REST_BASE = "https://www.okx.com"


class OkxAuthError(ConnectionError):
    """Raised when OKX explicitly rejects credentials."""


class OkxApiError(RuntimeError):
    """Raised when OKX returns a business error (code != 0)."""


class OkxRestClient:
    """Minimal async OKX v5 REST client (spot + account)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        *,
        demo: bool = True,
        base_url: str = OKX_REST_BASE,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.demo = demo
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _timestamp() -> str:
        # ISO 8601 UTC with millisecond precision, e.g. 2020-12-08T09:08:57.715Z
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _sign(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(self, timestamp: str, sign: str) -> Dict[str, str]:
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.demo:
            headers["x-simulated-trading"] = "1"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Perform a signed request. `path` is the API path without query string;
        query params go in `params` and are included in the signed requestPath.
        """
        method = method.upper()
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""

        request_path = path
        if params:
            from urllib.parse import urlencode

            request_path = f"{path}?{urlencode(params)}"

        ts = self._timestamp()
        # GET: body must be empty in the prehash; POST uses exact JSON body string
        body_for_sign = "" if method == "GET" else body_text
        sign = self._sign(ts, method, request_path, body_for_sign)

        url = f"{self.base_url}{request_path}"
        headers = self._headers(ts, sign)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.request(
                        method, url, headers=headers, content=body_text or None
                    )
        except httpx.HTTPError as exc:
            raise ConnectionError(f"OKX API unreachable: {exc}") from exc

        if resp.status_code in (401, 403):
            raise OkxAuthError(
                f"OKX rejected credentials (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except Exception as exc:
            raise ConnectionError(
                f"OKX returned non-JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            ) from exc

        code = str(payload.get("code", ""))
        if code == "0":
            return payload

        msg = self._format_error_message(payload)
        # Auth / key / passphrase / demo mismatch
        if code in ("50111", "50113", "50114", "50115", "50119", "50101") or any(
            k in msg.lower() for k in ("api key", "passphrase", "signature", "invalid key")
        ):
            raise OkxAuthError(f"OKX rejected credentials (code {code}): {msg}")

        if resp.status_code >= 400:
            raise ConnectionError(f"OKX HTTP {resp.status_code}: {msg}")

        raise OkxApiError(f"OKX error {code}: {msg}")

    @staticmethod
    def _format_error_message(payload: Dict[str, Any]) -> str:
        """Prefer nested data[].sCode/sMsg over generic 'All operations failed'."""
        top = str(payload.get("msg") or "")
        parts: List[str] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            scode = item.get("sCode")
            smsg = item.get("sMsg")
            if scode or smsg:
                parts.append(f"sCode={scode}: {smsg}")
        if parts:
            detail = "; ".join(parts)
            return f"{top} ({detail})" if top else detail
        return top or str(payload)

    @staticmethod
    def format_number(value: Any) -> str:
        """Format size/price without scientific notation or trailing .0 noise."""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            try:
                value = float(text)
            except ValueError:
                return text
        text = f"{float(value):.12f}".rstrip("0").rstrip(".")
        return text or "0"

    async def get_balance(self, ccy: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"ccy": ccy} if ccy else None
        payload = await self.request("GET", "/api/v5/account/balance", params=params)
        return list(payload.get("data") or [])

    async def get_order(
        self,
        *,
        inst_id: str,
        ord_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {"instId": inst_id}
        if ord_id:
            params["ordId"] = str(ord_id)
        if cl_ord_id:
            params["clOrdId"] = str(cl_ord_id)
        payload = await self.request("GET", "/api/v5/trade/order", params=params)
        return (payload.get("data") or [{}])[0]

    @staticmethod
    def map_order_state(okx_order: Dict[str, Any]) -> Dict[str, Any]:
        """Map OKX order payload → local status / filled_qty / avg price."""
        state = str(okx_order.get("state") or "").lower()
        filled = float(okx_order.get("accFillSz") or 0)
        avg_px = okx_order.get("avgPx")
        price = float(avg_px) if avg_px not in (None, "") else None
        if state == "filled":
            status = "FILLED"
        elif state in ("canceled", "cancelled", "mmp_canceled"):
            status = "CANCELLED"
        elif state == "partially_filled":
            status = "PENDING"
        else:
            # live / unknown
            status = "PENDING"
        return {"status": status, "filled_qty": filled, "price": price, "state": state}

    async def place_spot_order(
        self,
        *,
        inst_id: str,
        side: str,
        ord_type: str,
        sz: str,
        px: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
        td_mode: str = "cash",
    ) -> Dict[str, Any]:
        ord_type_l = ord_type.lower()
        side_l = side.lower()
        body: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side_l,
            "ordType": ord_type_l,
            "sz": self.format_number(sz),
        }
        # Spot market orders: OKX defaults buy sz to quote (USDT). Our UI quantity
        # is always base (e.g. 0.001 BTC), so force base_ccy to avoid 51020 on tiny USDT.
        if ord_type_l == "market":
            body["tgtCcy"] = "base_ccy"
        if px is not None and str(px).strip() != "":
            body["px"] = self.format_number(px)
        if cl_ord_id:
            # OKX clOrdId: 1-32 alphanumeric
            body["clOrdId"] = str(cl_ord_id)[:32]

        logger.info(
            "OKX place order demo=%s body=%s",
            self.demo,
            {k: body[k] for k in body if k != "clOrdId"},
        )
        try:
            payload = await self.request("POST", "/api/v5/trade/order", body=body)
        except OkxApiError as exc:
            # Account mode may reject cash — retry once with cross for spot
            if td_mode == "cash" and "51010" in str(exc):
                body["tdMode"] = "cross"
                logger.info("OKX retry place order with tdMode=cross")
                payload = await self.request("POST", "/api/v5/trade/order", body=body)
            else:
                raise
        data = (payload.get("data") or [{}])[0]
        scode = str(data.get("sCode") or "0")
        if scode not in ("0", ""):
            raise OkxApiError(f"OKX order rejected (sCode={scode}): {data.get('sMsg') or data}")
        return data

    async def cancel_order(
        self,
        *,
        inst_id: str,
        ord_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = ord_id
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        payload = await self.request("POST", "/api/v5/trade/cancel-order", body=body)
        data = (payload.get("data") or [{}])[0]
        scode = str(data.get("sCode") or "0")
        if scode not in ("0", ""):
            raise OkxApiError(f"OKX cancel rejected (sCode={scode}): {data.get('sMsg') or data}")
        return data
