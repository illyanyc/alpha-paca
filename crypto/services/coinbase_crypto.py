"""Coinbase Advanced Trade API wrapper for crypto trading.

Uses public endpoints (no auth) for market data (prices, candles).
Uses CDP API keys (JWT/PEM) for account data and order execution.

CDP keys must be created at https://portal.cdp.coinbase.com/projects/api-keys
with ECDSA (ES256) signature algorithm.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests
import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

API_BASE = "https://api.coinbase.com"


def _to_product_id(pair: str) -> str:
    """Convert 'BTC/USD' → 'BTC-USD' for Coinbase."""
    return pair.replace("/", "-")


def _to_pair(product_id: str) -> str:
    """Convert 'BTC-USD' → 'BTC/USD' for internal use."""
    return product_id.replace("-", "/")


def _is_pem_key(secret: str) -> bool:
    s = secret.replace("\\n", "\n").strip()
    return s.startswith("-----BEGIN")


def _normalize_pem_secret(secret: str) -> str:
    return secret.replace("\\n", "\n").strip()


class _PublicClient:
    """Unauthenticated client for Coinbase public market data endpoints."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def get_product_book(self, product_id: str, limit: int = 1) -> dict:
        """Public product book with best bid/ask."""
        resp = self.session.get(
            f"{API_BASE}/api/v3/brokerage/market/product_book",
            params={"product_id": product_id, "limit": str(limit)},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_best_bid_ask(self, product_ids: list[str] | None = None) -> dict:
        """Aggregate bid/ask from individual product_book calls."""
        pricebooks = []
        for pid in (product_ids or []):
            try:
                book = self.get_product_book(pid, limit=1)
                pb = book.get("pricebook", {})
                if pb:
                    pricebooks.append(pb)
            except Exception as e:
                logger.warning("public_bid_ask_failed", product_id=pid, error=str(e))
        return {"pricebooks": pricebooks}

    def get_product(self, product_id: str) -> dict:
        resp = self.session.get(
            f"{API_BASE}/api/v3/brokerage/market/products/{product_id}",
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_candles(self, product_id: str, start: str, end: str, granularity: str) -> dict:
        resp = self.session.get(
            f"{API_BASE}/api/v3/brokerage/market/products/{product_id}/candles",
            params={"start": start, "end": end, "granularity": granularity},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def _make_auth_client(api_key: str, api_secret: str):
    """Return an authenticated RESTClient for trading — requires CDP PEM keys."""
    if not api_key or not api_secret:
        return None
    if not _is_pem_key(api_secret):
        logger.warning(
            "coinbase_key_type_mismatch",
            hint=(
                "Your COINBASE_API_SECRET is not a PEM private key. "
                "CDP keys are required for trading. "
                "Create new keys at https://portal.cdp.coinbase.com/projects/api-keys "
                "using ECDSA (ES256) signature algorithm."
            ),
        )
        return None
    from coinbase.rest import RESTClient

    return RESTClient(api_key=api_key, api_secret=_normalize_pem_secret(api_secret))


class CoinbaseCryptoService:
    """Unified interface to Coinbase Advanced Trade data + trading endpoints.

    Market data always works (public endpoints, no auth).
    Trading requires CDP API keys (JWT/PEM).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._public = _PublicClient()
        self._auth = _make_auth_client(
            settings.coinbase.api_key, settings.coinbase.api_secret,
        )
        if self._auth:
            logger.info("coinbase_authenticated", key_prefix=settings.coinbase.api_key[:12])
        else:
            logger.warning(
                "coinbase_no_auth",
                hint="Market data available. Trading disabled until CDP PEM keys are configured.",
            )

    @property
    def is_authenticated(self) -> bool:
        return self._auth is not None

    @property
    def auth_error_message(self) -> str | None:
        if self._auth:
            return None
        return (
            "Coinbase trading requires CDP API keys (PEM format). "
            "Create at https://portal.cdp.coinbase.com/projects/api-keys — "
            "select ECDSA (ES256). Your current key is a legacy Cloud API key "
            "which is no longer supported for Advanced Trade."
        )

    def replace_client(self, api_key: str, api_secret: str) -> None:
        """Hot-swap credentials. Validates key type before accepting."""
        self._auth = _make_auth_client(api_key, api_secret)

    # ── Market data (public — always works) ─────────────────────────

    def get_latest_quotes(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch latest bid/ask quotes. Uses public endpoints."""
        product_ids = [_to_product_id(p) for p in pairs]
        raw = self._public.get_best_bid_ask(product_ids=product_ids)

        result: dict[str, dict[str, Any]] = {}
        for pricebook in raw.get("pricebooks", []):
            pid = pricebook.get("product_id", "")
            pair = _to_pair(pid)

            bids = pricebook.get("bids", [])
            asks = pricebook.get("asks", [])

            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            bid_size = float(bids[0]["size"]) if bids else 0.0
            ask_size = float(asks[0]["size"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0.0

            result[pair] = {
                "bid": best_bid,
                "ask": best_ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "mid": mid,
                "timestamp": datetime.now(timezone.utc),
            }
        return result

    def get_bars(
        self,
        pair: str,
        timeframe: Any = None,
        lookback_minutes: int = 120,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles. Uses public endpoints."""
        product_id = _to_product_id(pair)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        if lookback_minutes <= 300:
            granularity = "ONE_MINUTE"
        elif lookback_minutes <= 1500:
            granularity = "FIVE_MINUTE"
        elif lookback_minutes <= 6000:
            granularity = "FIFTEEN_MINUTE"
        else:
            granularity = "ONE_HOUR"

        start_str = str(int(start.timestamp()))
        end_str = str(int(end.timestamp()))

        raw = self._public.get_candles(
            product_id=product_id,
            start=start_str,
            end=end_str,
            granularity=granularity,
        )

        candles = raw.get("candles", [])
        bars = []
        for c in candles:
            ts = int(c.get("start", 0))
            bars.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
                "vwap": None,
            })

        bars.sort(key=lambda b: b["timestamp"] or datetime.min.replace(tzinfo=timezone.utc))
        return bars

    def get_product_price(self, pair: str) -> float:
        """Get current price for a single product. Public endpoint."""
        product_id = _to_product_id(pair)
        try:
            product = self._public.get_product(product_id)
            return float(product.get("price", 0))
        except Exception:
            return 0.0

    # ── Account data (authenticated) ────────────────────────────────

    def _require_auth(self) -> None:
        if not self._auth:
            raise RuntimeError(
                "Coinbase trading not available — CDP API keys required. "
                "Create at https://portal.cdp.coinbase.com/projects/api-keys"
            )

    @staticmethod
    def _to_dict(resp: Any) -> dict:
        """Normalize SDK typed response objects to plain dicts."""
        if isinstance(resp, dict):
            return resp
        if hasattr(resp, "to_dict"):
            return resp.to_dict()
        return dict(resp)

    def get_account(self) -> dict[str, Any]:
        """Return account summary. Requires CDP auth."""
        self._require_auth()
        raw = self._to_dict(self._auth.get_accounts(limit=250))
        accounts = raw.get("accounts", [])

        cash = 0.0
        total_value = 0.0

        for acct in accounts:
            currency = acct.get("currency", "")
            available = float(acct.get("available_balance", {}).get("value", 0))
            hold = float(acct.get("hold", {}).get("value", 0))
            total = available + hold

            if currency in ("USD", "USDC", "USDT"):
                cash += total
                total_value += total
            elif total > 0:
                price = self.get_product_price(f"{currency}/USD")
                if price > 0:
                    total_value += total * price

        return {
            "equity": total_value,
            "cash": cash,
            "buying_power": cash,
            "portfolio_value": total_value,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        """Return non-zero crypto holdings. Requires CDP auth."""
        self._require_auth()
        raw = self._to_dict(self._auth.get_accounts(limit=250))
        accounts = raw.get("accounts", [])
        positions: list[dict[str, Any]] = []

        for acct in accounts:
            currency = acct.get("currency", "")
            if currency in ("USD", "USDC", "USDT"):
                continue

            available = float(acct.get("available_balance", {}).get("value", 0))
            hold = float(acct.get("hold", {}).get("value", 0))
            qty = available + hold
            if qty <= 0:
                continue

            pair = f"{currency}/USD"
            current_price = self.get_product_price(pair)
            market_value = qty * current_price

            positions.append({
                "symbol": pair,
                "qty": qty,
                "avg_entry_price": current_price,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pl": 0,
                "unrealized_plpc": 0,
            })

        return positions

    # ── Trading (authenticated) ─────────────────────────────────────

    def _quantize_qty(self, pair: str, qty: Decimal) -> Decimal:
        """Truncate qty to the product's allowed base_increment precision."""
        product_id = _to_product_id(pair)
        try:
            product = self._public.get_product(product_id)
            increment = Decimal(product.get("base_increment", "0.00000001"))
            return (qty // increment) * increment
        except Exception:
            return qty.quantize(Decimal("0.00000001"))

    def submit_market_order(
        self, pair: str, qty: Decimal, side: str
    ) -> dict[str, Any]:
        """Submit a market order. Requires CDP auth."""
        self._require_auth()
        product_id = _to_product_id(pair)
        client_order_id = str(uuid.uuid4())
        qty = self._quantize_qty(pair, qty)
        if qty <= 0:
            raise RuntimeError(f"Order qty too small after rounding for {pair}")
        base_size = str(qty)

        if side.upper() == "BUY":
            raw = self._to_dict(self._auth.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
            ))
        else:
            raw = self._to_dict(self._auth.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=base_size,
            ))

        success = raw.get("success", False)
        success_resp = raw.get("success_response", {}) or {}
        error_resp = raw.get("error_response", {}) or {}

        order_id = success_resp.get("order_id", "")
        if not order_id:
            order_id = raw.get("order_id", client_order_id)

        if not success:
            error_msg = error_resp.get("message", "") or error_resp.get("error", "") or str(raw)
            raise RuntimeError(f"Coinbase order failed: {error_msg}")

        logger.info(
            "order_submitted",
            pair=pair,
            side=side,
            qty=str(qty),
            order_id=order_id,
        )

        return {
            "order_id": order_id,
            "status": "pending",
            "filled_qty": 0,
            "filled_avg_price": 0,
            "submitted_at": datetime.now(timezone.utc),
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        self._require_auth()
        raw = self._to_dict(self._auth.get_order(order_id))
        order = raw.get("order", raw)

        status_map = {
            "FILLED": "filled",
            "CANCELLED": "cancelled",
            "EXPIRED": "expired",
            "PENDING": "pending",
            "OPEN": "open",
        }
        raw_status = order.get("status", "UNKNOWN")
        status = status_map.get(raw_status, raw_status.lower())

        filled_qty = float(order.get("filled_size", 0))
        avg_price = float(order.get("average_filled_price", 0))

        return {
            "order_id": order.get("order_id", order_id),
            "status": status,
            "filled_qty": filled_qty,
            "filled_avg_price": avg_price,
        }

    async def wait_for_fill(self, order_id: str, timeout_sec: int = 60) -> dict[str, Any]:
        """Poll until order is filled or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            info = await asyncio.to_thread(self.get_order, order_id)
            if info["status"] in ("filled", "partially_filled"):
                return info
            if info["status"] in ("cancelled", "expired"):
                return info
            await asyncio.sleep(1)
        return await asyncio.to_thread(self.get_order, order_id)
