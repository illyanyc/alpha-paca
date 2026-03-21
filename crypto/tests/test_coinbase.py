"""Tests for CoinbaseCryptoService — uses mocked REST responses."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from services.coinbase_crypto import (
    CoinbaseCryptoService,
    _to_product_id,
    _to_pair,
)


def test_to_product_id():
    assert _to_product_id("BTC/USD") == "BTC-USD"
    assert _to_product_id("ETH/USD") == "ETH-USD"
    assert _to_product_id("DOGE/USD") == "DOGE-USD"


def test_to_pair():
    assert _to_pair("BTC-USD") == "BTC/USD"
    assert _to_pair("ETH-USD") == "ETH/USD"


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
def mock_client():
    client = MagicMock()
    client.get_best_bid_ask.return_value = MOCK_BID_ASK
    client.get_candles.return_value = MOCK_CANDLES
    client.get_accounts.return_value = MOCK_ACCOUNTS
    client.get_product.return_value = {"price": "83000"}
    client.market_order_buy.return_value = MOCK_ORDER_SUCCESS
    client.market_order_sell.return_value = MOCK_ORDER_SUCCESS
    client.get_order.return_value = MOCK_ORDER_FILLED
    return client


@pytest.fixture
def service(mock_client):
    with patch("services.coinbase_crypto.RESTClient", return_value=mock_client):
        svc = CoinbaseCryptoService()
    svc._client = mock_client
    return svc


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


class TestGetBars:
    def test_returns_ohlcv_bars(self, service):
        bars = service.get_bars("BTC/USD", lookback_minutes=120)
        assert len(bars) == 2
        bar = bars[0]
        assert "open" in bar
        assert "high" in bar
        assert "low" in bar
        assert "close" in bar
        assert "volume" in bar
        assert "timestamp" in bar

    def test_bars_are_sorted_chronologically(self, service):
        bars = service.get_bars("BTC/USD")
        timestamps = [b["timestamp"] for b in bars]
        assert timestamps == sorted(timestamps)

    def test_selects_correct_granularity(self, service):
        service.get_bars("BTC/USD", lookback_minutes=60)
        call_args = service._client.get_candles.call_args
        assert call_args.kwargs["granularity"] == "ONE_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=500)
        call_args = service._client.get_candles.call_args
        assert call_args.kwargs["granularity"] == "FIVE_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=2000)
        call_args = service._client.get_candles.call_args
        assert call_args.kwargs["granularity"] == "FIFTEEN_MINUTE"

        service.get_bars("BTC/USD", lookback_minutes=10000)
        call_args = service._client.get_candles.call_args
        assert call_args.kwargs["granularity"] == "ONE_HOUR"


class TestGetAccount:
    def test_returns_account_summary(self, service):
        acct = service.get_account()
        assert "equity" in acct
        assert "cash" in acct
        assert "buying_power" in acct
        assert "portfolio_value" in acct
        assert acct["cash"] == 5000.0
        assert acct["buying_power"] == 5000.0

    def test_includes_crypto_value_in_equity(self, service):
        acct = service.get_account()
        # USD (5000) + BTC (0.05 * 83000 = 4150) = 9150
        assert acct["equity"] == pytest.approx(9150.0)


class TestGetPositions:
    def test_returns_non_zero_crypto_positions(self, service):
        positions = service.get_positions()
        assert len(positions) == 1  # only BTC has non-zero balance
        assert positions[0]["symbol"] == "BTC/USD"
        assert positions[0]["qty"] == 0.05

    def test_excludes_usd_and_zero_balances(self, service):
        positions = service.get_positions()
        symbols = [p["symbol"] for p in positions]
        assert "USD" not in symbols
        assert "ETH/USD" not in symbols  # zero balance


class TestSubmitMarketOrder:
    def test_buy_order(self, service):
        result = service.submit_market_order("BTC/USD", Decimal("0.001"), "BUY")
        assert result["order_id"] == "order-abc-123"
        assert result["status"] == "pending"
        service._client.market_order_buy.assert_called_once()

    def test_sell_order(self, service):
        result = service.submit_market_order("BTC/USD", Decimal("0.001"), "SELL")
        assert result["order_id"] == "order-abc-123"
        service._client.market_order_sell.assert_called_once()

    def test_failed_order_raises(self, service):
        service._client.market_order_buy.return_value = {
            "success": False,
            "success_response": None,
            "error_response": {"message": "Insufficient funds"},
        }
        with pytest.raises(RuntimeError, match="Coinbase order failed"):
            service.submit_market_order("BTC/USD", Decimal("0.001"), "BUY")


class TestGetOrder:
    def test_filled_order(self, service):
        result = service.get_order("order-abc-123")
        assert result["status"] == "filled"
        assert result["filled_qty"] == 0.001
        assert result["filled_avg_price"] == 83050.0

    def test_pending_order(self, service):
        service._client.get_order.return_value = {
            "order": {"order_id": "x", "status": "PENDING", "filled_size": "0", "average_filled_price": "0"}
        }
        result = service.get_order("x")
        assert result["status"] == "pending"


class TestWaitForFill:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_filled(self, service):
        result = await service.wait_for_fill("order-abc-123", timeout_sec=5)
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_returns_on_cancel(self, service):
        service._client.get_order.return_value = {
            "order": {"order_id": "x", "status": "CANCELLED", "filled_size": "0", "average_filled_price": "0"}
        }
        result = await service.wait_for_fill("x", timeout_sec=2)
        assert result["status"] == "cancelled"


class TestReplaceClient:
    def test_replace_client_creates_new_rest_client(self, service):
        with patch("services.coinbase_crypto.RESTClient") as mock_cls:
            service.replace_client("new-key", "new-secret")
            mock_cls.assert_called_once_with(api_key="new-key", api_secret="new-secret")


class TestIntegrationLive:
    """Integration tests that hit the real Coinbase API — only run with real keys."""

    @pytest.fixture
    def live_service(self):
        import os
        key = os.environ.get("COINBASE_API_KEY", "")
        secret = os.environ.get("COINBASE_API_SECRET", "")
        if not key or key == "test":
            pytest.skip("No real Coinbase API keys set")
        return CoinbaseCryptoService()

    def test_live_get_quotes(self, live_service):
        quotes = live_service.get_latest_quotes(["BTC/USD", "ETH/USD"])
        assert "BTC/USD" in quotes
        assert quotes["BTC/USD"]["mid"] > 0

    def test_live_get_bars(self, live_service):
        bars = live_service.get_bars("BTC/USD", lookback_minutes=60)
        assert len(bars) > 0
        assert bars[-1]["close"] > 0

    def test_live_get_account(self, live_service):
        acct = live_service.get_account()
        assert acct["equity"] >= 0
        assert acct["cash"] >= 0
