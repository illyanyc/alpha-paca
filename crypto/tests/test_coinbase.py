"""Tests for CoinbaseCryptoService — unit, public client, and live integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from services.coinbase_crypto import (
    CoinbaseCryptoService,
    _PublicClient,
    _to_product_id,
    _to_pair,
    _is_pem_key,
    _make_auth_client,
)


# ── Helpers ──────────────────────────────────────────────────────────


def test_to_product_id():
    assert _to_product_id("BTC/USD") == "BTC-USD"
    assert _to_product_id("ETH/USD") == "ETH-USD"
    assert _to_product_id("DOGE/USD") == "DOGE-USD"


def test_to_pair():
    assert _to_pair("BTC-USD") == "BTC/USD"
    assert _to_pair("ETH-USD") == "ETH/USD"


class TestKeyDetection:
    def test_pem_key_detected(self):
        pem = "-----BEGIN EC PRIVATE KEY-----\nMIGk...\n-----END EC PRIVATE KEY-----"
        assert _is_pem_key(pem) is True

    def test_pem_key_with_escaped_newlines(self):
        pem = "-----BEGIN EC PRIVATE KEY-----\\nMIGk...\\n-----END EC PRIVATE KEY-----"
        assert _is_pem_key(pem) is True

    def test_hmac_key_detected_as_non_pem(self):
        hmac_secret = "HzkGAKOSKEBEvtCCAMh9ffxIa6if6q/lWS50NifO1W1lv7qi1oqgGuML22sA191wOXYAz26B0ZHR9y59roPLPA=="
        assert _is_pem_key(hmac_secret) is False

    def test_empty_key(self):
        assert _is_pem_key("") is False
        assert _is_pem_key("test") is False


class TestMakeAuthClient:
    def test_pem_key_returns_rest_client(self):
        with patch("coinbase.rest.RESTClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = _make_auth_client(
                "organizations/org/apiKeys/key",
                "-----BEGIN EC PRIVATE KEY-----\nfake\n-----END EC PRIVATE KEY-----",
            )
            assert result is not None
            mock_cls.assert_called_once()

    def test_hmac_key_returns_none(self):
        result = _make_auth_client("uuid-key", "base64secret==")
        assert result is None

    def test_empty_keys_returns_none(self):
        assert _make_auth_client("", "") is None
        assert _make_auth_client("key", "") is None
        assert _make_auth_client("", "secret") is None


# ── Mock data ────────────────────────────────────────────────────────


MOCK_BID_ASK = {
    "pricebooks": [
        {
            "product_id": "BTC-USD",
            "bids": [{"price": "83000.50", "size": "0.5"}],
            "asks": [{"price": "83001.00", "size": "0.3"}],
        },
        {
            "product_id": "ETH-USD",
            "bids": [{"price": "1900.00", "size": "2.0"}],
            "asks": [{"price": "1900.50", "size": "1.0"}],
        },
    ]
}

MOCK_CANDLES = {
    "candles": [
        {"start": "1711000000", "open": "82500", "high": "83500", "low": "82000", "close": "83000", "volume": "150.5"},
        {"start": "1711000060", "open": "83000", "high": "83200", "low": "82900", "close": "83100", "volume": "80.2"},
    ]
}

MOCK_PRODUCT = {"price": "83000"}

MOCK_ACCOUNTS = {
    "accounts": [
        {
            "uuid": "acc-1",
            "currency": "USD",
            "available_balance": {"value": "5000.00", "currency": "USD"},
            "hold": {"value": "0", "currency": "USD"},
        },
        {
            "uuid": "acc-2",
            "currency": "BTC",
            "available_balance": {"value": "0.05", "currency": "BTC"},
            "hold": {"value": "0", "currency": "BTC"},
        },
        {
            "uuid": "acc-3",
            "currency": "ETH",
            "available_balance": {"value": "0", "currency": "ETH"},
            "hold": {"value": "0", "currency": "ETH"},
        },
    ]
}

MOCK_ORDER_SUCCESS = {
    "success": True,
    "success_response": {
        "order_id": "order-abc-123",
        "product_id": "BTC-USD",
        "side": "BUY",
        "client_order_id": "test-uuid",
    },
    "error_response": None,
}

MOCK_ORDER_FILLED = {
    "order": {
        "order_id": "order-abc-123",
        "status": "FILLED",
        "filled_size": "0.001",
        "average_filled_price": "83050.00",
    }
}


@pytest.fixture
def mock_public():
    pub = MagicMock(spec=_PublicClient)
    pub.get_best_bid_ask.return_value = MOCK_BID_ASK
    pub.get_candles.return_value = MOCK_CANDLES
    pub.get_product.return_value = MOCK_PRODUCT
    return pub


@pytest.fixture
def mock_auth():
    auth = MagicMock()
    auth.get_accounts.return_value = MOCK_ACCOUNTS
    auth.get_product.return_value = MOCK_PRODUCT
    auth.market_order_buy.return_value = MOCK_ORDER_SUCCESS
    auth.market_order_sell.return_value = MOCK_ORDER_SUCCESS
    auth.get_order.return_value = MOCK_ORDER_FILLED
    return auth


@pytest.fixture
def service(mock_public, mock_auth):
    with patch("services.coinbase_crypto._make_auth_client", return_value=mock_auth):
        with patch("services.coinbase_crypto._PublicClient", return_value=mock_public):
            svc = CoinbaseCryptoService()
    return svc


@pytest.fixture
def unauth_service(mock_public):
    """Service without trading auth — only public market data."""
    with patch("services.coinbase_crypto._make_auth_client", return_value=None):
        with patch("services.coinbase_crypto._PublicClient", return_value=mock_public):
            svc = CoinbaseCryptoService()
    return svc


# ── Market data tests (public, no auth needed) ──────────────────────


class TestGetLatestQuotes:
    def test_returns_quotes_for_pairs(self, service):
        result = service.get_latest_quotes(["BTC/USD", "ETH/USD"])
        assert "BTC/USD" in result
        assert "ETH/USD" in result

    def test_quote_has_required_fields(self, service):
        result = service.get_latest_quotes(["BTC/USD"])
        btc = result["BTC/USD"]
        assert btc["bid"] == 83000.50
        assert btc["ask"] == 83001.00
        assert btc["mid"] == pytest.approx(83000.75)
        assert "bid_size" in btc
        assert "ask_size" in btc
        assert "timestamp" in btc

    def test_empty_pricebooks_returns_empty(self, service):
        service._public.get_best_bid_ask.return_value = {"pricebooks": []}
        result = service.get_latest_quotes(["BTC/USD"])
        assert result == {}

    def test_missing_bids_asks_handled(self, service):
        service._public.get_best_bid_ask.return_value = {
            "pricebooks": [{"product_id": "BTC-USD", "bids": [], "asks": []}]
        }
        result = service.get_latest_quotes(["BTC/USD"])
        assert result["BTC/USD"]["bid"] == 0.0
        assert result["BTC/USD"]["ask"] == 0.0

    def test_works_without_auth(self, unauth_service):
        """Market data should work even without CDP keys."""
        result = unauth_service.get_latest_quotes(["BTC/USD"])
        assert "BTC/USD" in result


class TestGetBars:
    def test_returns_ohlcv_bars(self, service):
        bars = service.get_bars("BTC/USD", lookback_minutes=120)
        assert len(bars) == 2
        bar = bars[0]
        for field in ("open", "high", "low", "close", "volume", "timestamp"):
            assert field in bar

    def test_bars_are_sorted_chronologically(self, service):
        bars = service.get_bars("BTC/USD")
        timestamps = [b["timestamp"] for b in bars]
        assert timestamps == sorted(timestamps)

    def test_selects_correct_granularity(self, service):
        service.get_bars("BTC/USD", lookback_minutes=60)
        assert service._public.get_candles.call_args.kwargs["granularity"] == "ONE_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=500)
        assert service._public.get_candles.call_args.kwargs["granularity"] == "FIVE_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=2000)
        assert service._public.get_candles.call_args.kwargs["granularity"] == "FIFTEEN_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=10000)
        assert service._public.get_candles.call_args.kwargs["granularity"] == "ONE_HOUR"

    def test_empty_candles(self, service):
        service._public.get_candles.return_value = {"candles": []}
        assert service.get_bars("BTC/USD") == []

    def test_works_without_auth(self, unauth_service):
        bars = unauth_service.get_bars("BTC/USD", lookback_minutes=60)
        assert len(bars) == 2


# ── Auth property tests ─────────────────────────────────────────────


class TestAuthProperties:
    def test_is_authenticated_true(self, service):
        assert service.is_authenticated is True

    def test_is_authenticated_false(self, unauth_service):
        assert unauth_service.is_authenticated is False

    def test_auth_error_message_when_unauth(self, unauth_service):
        msg = unauth_service.auth_error_message
        assert msg is not None
        assert "CDP" in msg

    def test_auth_error_message_none_when_auth(self, service):
        assert service.auth_error_message is None


# ── Account tests (require auth) ────────────────────────────────────


class TestGetAccount:
    def test_returns_account_summary(self, service):
        service._public.get_product.return_value = {"price": "83000"}
        acct = service.get_account()
        assert "equity" in acct
        assert "cash" in acct
        assert acct["cash"] == 5000.0

    def test_includes_crypto_value(self, service):
        service._public.get_product.return_value = {"price": "83000"}
        acct = service.get_account()
        assert acct["equity"] == pytest.approx(9150.0)

    def test_empty_accounts(self, service):
        service._auth.get_accounts.return_value = {"accounts": []}
        acct = service.get_account()
        assert acct["cash"] == 0.0

    def test_raises_without_auth(self, unauth_service):
        with pytest.raises(RuntimeError, match="CDP API keys required"):
            unauth_service.get_account()


class TestGetPositions:
    def test_returns_non_zero_positions(self, service):
        service._public.get_product.return_value = {"price": "83000"}
        positions = service.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC/USD"

    def test_excludes_usd_and_zero(self, service):
        service._public.get_product.return_value = {"price": "83000"}
        positions = service.get_positions()
        symbols = [p["symbol"] for p in positions]
        assert "USD" not in symbols
        assert "ETH/USD" not in symbols

    def test_raises_without_auth(self, unauth_service):
        with pytest.raises(RuntimeError, match="CDP API keys required"):
            unauth_service.get_positions()


# ── Trading tests (require auth) ────────────────────────────────────


class TestSubmitMarketOrder:
    def test_buy_order(self, service):
        result = service.submit_market_order("BTC/USD", Decimal("0.001"), "BUY")
        assert result["order_id"] == "order-abc-123"
        assert result["status"] == "pending"
        service._auth.market_order_buy.assert_called_once()

    def test_sell_order(self, service):
        result = service.submit_market_order("BTC/USD", Decimal("0.001"), "SELL")
        assert result["order_id"] == "order-abc-123"
        service._auth.market_order_sell.assert_called_once()

    def test_failed_order_raises(self, service):
        service._auth.market_order_buy.return_value = {
            "success": False,
            "success_response": None,
            "error_response": {"message": "Insufficient funds"},
        }
        with pytest.raises(RuntimeError, match="Coinbase order failed"):
            service.submit_market_order("BTC/USD", Decimal("0.001"), "BUY")

    def test_raises_without_auth(self, unauth_service):
        with pytest.raises(RuntimeError, match="CDP API keys required"):
            unauth_service.submit_market_order("BTC/USD", Decimal("0.001"), "BUY")


class TestGetOrder:
    def test_filled_order(self, service):
        result = service.get_order("order-abc-123")
        assert result["status"] == "filled"
        assert result["filled_qty"] == 0.001
        assert result["filled_avg_price"] == 83050.0

    def test_pending_order(self, service):
        service._auth.get_order.return_value = {
            "order": {"order_id": "x", "status": "PENDING", "filled_size": "0", "average_filled_price": "0"}
        }
        assert service.get_order("x")["status"] == "pending"

    def test_raises_without_auth(self, unauth_service):
        with pytest.raises(RuntimeError, match="CDP API keys required"):
            unauth_service.get_order("x")


class TestWaitForFill:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_filled(self, service):
        result = await service.wait_for_fill("order-abc-123", timeout_sec=5)
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_returns_on_cancel(self, service):
        service._auth.get_order.return_value = {
            "order": {"order_id": "x", "status": "CANCELLED", "filled_size": "0", "average_filled_price": "0"}
        }
        result = await service.wait_for_fill("x", timeout_sec=2)
        assert result["status"] == "cancelled"


class TestReplaceClient:
    def test_replace_with_hmac_becomes_unauth(self, service):
        service.replace_client("uuid-key", "base64secret==")
        assert service.is_authenticated is False

    def test_replace_with_pem_becomes_auth(self, service):
        with patch("coinbase.rest.RESTClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            pem = "-----BEGIN EC PRIVATE KEY-----\nfake\n-----END EC PRIVATE KEY-----"
            service.replace_client("organizations/org/apiKeys/key", pem)
            assert service.is_authenticated is True


# ── Live Integration tests ──────────────────────────────────────────


class TestPublicEndpointsLive:
    """Live tests for public market data — always work, no keys needed."""

    @pytest.fixture
    def live_public(self):
        return _PublicClient()

    def test_live_best_bid_ask(self, live_public):
        result = live_public.get_best_bid_ask(["BTC-USD", "ETH-USD"])
        assert "pricebooks" in result
        pricebooks = result["pricebooks"]
        assert len(pricebooks) >= 2
        for pb in pricebooks:
            assert "bids" in pb
            assert "asks" in pb
            assert len(pb["bids"]) > 0
            assert float(pb["bids"][0]["price"]) > 0

    def test_live_product(self, live_public):
        result = live_public.get_product("BTC-USD")
        assert "price" in result
        assert float(result["price"]) > 10000

    def test_live_candles(self, live_public):
        import time
        end = str(int(time.time()))
        start = str(int(time.time()) - 3600)
        result = live_public.get_candles("BTC-USD", start, end, "ONE_MINUTE")
        assert "candles" in result
        assert len(result["candles"]) > 0
        candle = result["candles"][0]
        assert float(candle["open"]) > 0


class TestServicePublicLive:
    """Live tests for CoinbaseCryptoService using real public endpoints."""

    @pytest.fixture
    def live_service(self):
        """Service with no auth — only public endpoints."""
        with patch("services.coinbase_crypto._make_auth_client", return_value=None):
            svc = CoinbaseCryptoService()
        return svc

    def test_live_get_quotes(self, live_service):
        quotes = live_service.get_latest_quotes(["BTC/USD", "ETH/USD"])
        assert "BTC/USD" in quotes
        assert quotes["BTC/USD"]["mid"] > 0
        assert quotes["BTC/USD"]["ask"] >= quotes["BTC/USD"]["bid"]

    def test_live_get_bars(self, live_service):
        bars = live_service.get_bars("BTC/USD", lookback_minutes=30)
        assert len(bars) > 0
        for bar in bars:
            assert bar["high"] >= bar["low"]
            assert bar["close"] > 0

    def test_live_multi_pair_quotes(self, live_service):
        pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "LINK/USD"]
        quotes = live_service.get_latest_quotes(pairs)
        for pair in pairs:
            assert pair in quotes, f"Missing quote for {pair}"
            assert quotes[pair]["mid"] > 0, f"Zero mid for {pair}"

    def test_live_product_price(self, live_service):
        price = live_service.get_product_price("BTC/USD")
        assert price > 10000

    def test_live_eth_candles(self, live_service):
        bars = live_service.get_bars("ETH/USD", lookback_minutes=30)
        assert len(bars) > 0
        for bar in bars:
            assert bar["high"] >= bar["open"]
            assert bar["high"] >= bar["close"]
            assert bar["low"] <= bar["open"]
            assert bar["low"] <= bar["close"]

    def test_auth_status(self, live_service):
        assert live_service.is_authenticated is False
        assert live_service.auth_error_message is not None
