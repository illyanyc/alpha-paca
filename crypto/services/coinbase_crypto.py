"""Coinbase Advanced Trade API wrapper for crypto trading.

Uses public endpoints (no auth) for market data (prices, candles).
Uses CDP API keys (JWT/PEM) for account data and order execution.

CDP keys must be created at https://portal.cdp.coinbase.com/projects/api-keys
with ECDSA (ES256) signature algorithm.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests
import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

API_BASE = "https://api.coinbase.com"

_AUTH_FAIL_COUNT = 0
_AUTH_FAIL_LAST_LOG: float = 0
_AUTH_CIRCUIT_OPEN_UNTIL: float = 0
_AUTH_CIRCUIT_BACKOFF = 300  # 5 min cooldown after repeated failures
_AUTH_FAIL_THRESHOLD = 3


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
        granularity: str | None = None,
        lookback_minutes: int = 120,
        timeframe: Any = None,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles. Uses public endpoints.

        Args:
            granularity: Coinbase granularity string (e.g. ONE_MINUTE, FIVE_MINUTE,
                         FIFTEEN_MINUTE, ONE_HOUR, FOUR_HOUR, ONE_DAY).
                         If not provided, inferred from lookback_minutes.
            lookback_minutes: How far back to fetch.
        """
        product_id = _to_product_id(pair)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)

        if granularity is None:
            if lookback_minutes <= 300:
                granularity = "ONE_MINUTE"
            elif lookback_minutes <= 1500:
                granularity = "FIVE_MINUTE"
            elif lookback_minutes <= 6000:
                granularity = "FIFTEEN_MINUTE"
            else:
                granularity = "ONE_HOUR"

        # Coinbase limits to 300 candles per request — paginate if needed
        granularity_minutes = {
            "ONE_MINUTE": 1, "FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15,
            "ONE_HOUR": 60, "FOUR_HOUR": 240, "SIX_HOUR": 360, "ONE_DAY": 1440,
        }
        bar_size = granularity_minutes.get(granularity, 60)
        max_candles_per_req = 300
        max_span_minutes = max_candles_per_req * bar_size

        all_bars: list[dict[str, Any]] = []
        chunk_end = end
        while chunk_end > start:
            chunk_start = max(start, chunk_end - timedelta(minutes=max_span_minutes))
            start_str = str(int(chunk_start.timestamp()))
            end_str = str(int(chunk_end.timestamp()))

            raw = self._public.get_candles(
                product_id=product_id,
                start=start_str,
                end=end_str,
                granularity=granularity,
            )

            candles = raw.get("candles", [])
            for c in candles:
                ts = int(c.get("start", 0))
                all_bars.append({
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                    "vwap": None,
                })

            if len(candles) == 0:
                break
            chunk_end = chunk_start

        all_bars.sort(key=lambda b: b["timestamp"] or datetime.min.replace(tzinfo=timezone.utc))
        seen_ts: set[datetime | None] = set()
        deduped: list[dict[str, Any]] = []
        for b in all_bars:
            if b["timestamp"] not in seen_ts:
                seen_ts.add(b["timestamp"])
                deduped.append(b)
        return deduped

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
        global _AUTH_CIRCUIT_OPEN_UNTIL
        if time.monotonic() < _AUTH_CIRCUIT_OPEN_UNTIL:
            remaining = int(_AUTH_CIRCUIT_OPEN_UNTIL - time.monotonic())
            raise RuntimeError(f"Coinbase auth circuit open — retrying in {remaining}s")

    @staticmethod
    def _record_auth_success() -> None:
        global _AUTH_FAIL_COUNT, _AUTH_CIRCUIT_OPEN_UNTIL
        _AUTH_FAIL_COUNT = 0
        _AUTH_CIRCUIT_OPEN_UNTIL = 0

    @staticmethod
    def _record_auth_failure(error: str) -> None:
        global _AUTH_FAIL_COUNT, _AUTH_FAIL_LAST_LOG, _AUTH_CIRCUIT_OPEN_UNTIL
        _AUTH_FAIL_COUNT += 1
        now = time.monotonic()
        if _AUTH_FAIL_COUNT >= _AUTH_FAIL_THRESHOLD:
            _AUTH_CIRCUIT_OPEN_UNTIL = now + _AUTH_CIRCUIT_BACKOFF
            if now - _AUTH_FAIL_LAST_LOG > 60:
                logger.error(
                    "coinbase_auth_circuit_open",
                    consecutive_failures=_AUTH_FAIL_COUNT,
                    retry_in_sec=_AUTH_CIRCUIT_BACKOFF,
                    error=error[:120],
                )
                _AUTH_FAIL_LAST_LOG = now
        elif now - _AUTH_FAIL_LAST_LOG > 30:
            logger.warning("coinbase_auth_failed", error=error[:120], attempt=_AUTH_FAIL_COUNT)
            _AUTH_FAIL_LAST_LOG = now

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
        try:
            raw = self._to_dict(self._auth.get_accounts(limit=250))
        except requests.exceptions.HTTPError as e:
            self._record_auth_failure(str(e))
            raise
        self._record_auth_success()
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
        try:
            raw = self._to_dict(self._auth.get_accounts(limit=250))
        except requests.exceptions.HTTPError as e:
            self._record_auth_failure(str(e))
            raise
        self._record_auth_success()
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

    def get_available_balance(self, pair: str) -> Decimal:
        """Return the actual available balance for a currency on Coinbase."""
        self._require_auth()
        currency = pair.split("/")[0]
        raw = self._to_dict(self._auth.get_accounts(limit=250))
        for acct in raw.get("accounts", []):
            if acct.get("currency", "") == currency:
                return Decimal(str(acct.get("available_balance", {}).get("value", "0")))
        return Decimal(0)

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

    def submit_limit_order(
        self,
        pair: str,
        qty: Decimal,
        side: str,
        limit_price: float,
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Submit a limit GTC order with optional post_only for maker fees."""
        self._require_auth()
        product_id = _to_product_id(pair)
        client_order_id = str(uuid.uuid4())
        qty = self._quantize_qty(pair, qty)
        if qty <= 0:
            raise RuntimeError(f"Order qty too small after rounding for {pair}")

        order_config = {
            "limit_limit_gtc": {
                "base_size": str(qty),
                "limit_price": f"{limit_price:.2f}",
                "post_only": post_only,
            }
        }

        raw = self._to_dict(self._auth.create_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side.upper(),
            order_configuration=order_config,
        ))

        success = raw.get("success", False)
        success_resp = raw.get("success_response", {}) or {}
        error_resp = raw.get("error_response", {}) or {}
        order_id = success_resp.get("order_id", "") or raw.get("order_id", client_order_id)

        if not success:
            error_msg = error_resp.get("message", "") or error_resp.get("error", "") or str(raw)
            raise RuntimeError(f"Coinbase limit order failed: {error_msg}")

        logger.info(
            "limit_order_submitted", pair=pair, side=side,
            qty=str(qty), limit_price=f"${limit_price:,.2f}",
            post_only=post_only, order_id=order_id,
        )

        return {
            "order_id": order_id,
            "status": "pending",
            "filled_qty": 0,
            "filled_avg_price": 0,
            "submitted_at": datetime.now(timezone.utc),
        }

    def submit_bracket_order(
        self,
        pair: str,
        qty: Decimal,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Submit a limit BUY with attached bracket TP/SL (trigger_bracket_gtc)."""
        self._require_auth()
        product_id = _to_product_id(pair)
        client_order_id = str(uuid.uuid4())
        qty = self._quantize_qty(pair, qty)
        if qty <= 0:
            raise RuntimeError(f"Order qty too small after rounding for {pair}")

        order_config = {
            "limit_limit_gtc": {
                "base_size": str(qty),
                "limit_price": f"{entry_price:.2f}",
                "post_only": post_only,
            }
        }

        attached_order_config = {
            "trigger_bracket_gtc": {
                "limit_price": f"{tp_price:.2f}",
                "stop_trigger_price": f"{sl_price:.2f}",
            }
        }

        try:
            raw = self._to_dict(self._auth.create_order(
                client_order_id=client_order_id,
                product_id=product_id,
                side="BUY",
                order_configuration=order_config,
                attached_order_configuration=attached_order_config,
            ))
        except TypeError:
            logger.warning("bracket_order_not_supported_by_sdk", pair=pair)
            return self.submit_limit_order(pair, qty, "BUY", entry_price, post_only)

        success = raw.get("success", False)
        success_resp = raw.get("success_response", {}) or {}
        error_resp = raw.get("error_response", {}) or {}
        order_id = success_resp.get("order_id", "") or raw.get("order_id", client_order_id)

        if not success:
            error_msg = error_resp.get("message", "") or error_resp.get("error", "") or str(raw)
            logger.warning("bracket_order_failed_fallback_limit", error=error_msg)
            return self.submit_limit_order(pair, qty, "BUY", entry_price, post_only)

        logger.info(
            "bracket_order_submitted", pair=pair,
            qty=str(qty), entry=f"${entry_price:,.2f}",
            tp=f"${tp_price:,.2f}", sl=f"${sl_price:,.2f}",
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

    def get_fills(
        self,
        product_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Fetch trade fill history from Coinbase. Requires auth."""
        self._require_auth()
        kwargs: dict[str, Any] = {"limit": limit}
        if product_id:
            kwargs["product_id"] = product_id
        if start_date:
            kwargs["start_sequence_timestamp"] = start_date
        if end_date:
            kwargs["end_sequence_timestamp"] = end_date
        if cursor:
            kwargs["cursor"] = cursor
        raw = self._to_dict(self._auth.get_fills(**kwargs))
        return raw

    def get_all_fills(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Paginate through all fills. Returns list of fill dicts."""
        self._require_auth()
        all_fills: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(200):
            raw = self.get_fills(
                start_date=start_date, end_date=end_date,
                cursor=cursor, limit=100,
            )
            fills = raw.get("fills", [])
            all_fills.extend(fills)
            cursor = raw.get("cursor")
            if not cursor or not fills:
                break

        return all_fills

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
