"""Tests for RiskValidatorAgent."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.risk_validator import RiskValidatorAgent


@pytest.fixture
def risk_agent():
    return RiskValidatorAgent()


@pytest.mark.asyncio
async def test_approve_valid_buy(risk_agent):
    result = await risk_agent.run(
        decision={"action": "BUY", "pair": "BTC/USD", "size_pct": 10, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 0, "drawdown_pct": 0},
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_reject_drawdown_breach(risk_agent):
    result = await risk_agent.run(
        decision={"action": "BUY", "pair": "BTC/USD", "size_pct": 10, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 900, "cash": 900, "total_exposure_pct": 0, "drawdown_pct": 12},
    )
    assert result["approved"] is False
    assert "Drawdown" in result["reasons"]


@pytest.mark.asyncio
async def test_reject_exposure_breach(risk_agent):
    result = await risk_agent.run(
        decision={"action": "BUY", "pair": "BTC/USD", "size_pct": 50, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 60, "drawdown_pct": 0},
    )
    assert result["approved"] is False
    assert "Exposure" in result["reasons"]


@pytest.mark.asyncio
async def test_reject_position_limit(risk_agent):
    result = await risk_agent.run(
        decision={"action": "BUY", "pair": "BTC/USD", "size_pct": 40, "confidence": 0.8},
        positions=[{"pair": "BTC/USD", "market_value_usd": 200, "nav": 1000}],
        portfolio_state={"nav": 1000, "cash": 800, "total_exposure_pct": 20, "drawdown_pct": 0},
    )
    assert result["approved"] is False
    assert "position" in result["reasons"].lower()


@pytest.mark.asyncio
async def test_sell_always_approved(risk_agent):
    result = await risk_agent.run(
        decision={"action": "SELL", "pair": "BTC/USD", "size_pct": 0, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 80, "drawdown_pct": 12},
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_cover_always_approved(risk_agent):
    result = await risk_agent.run(
        decision={"action": "COVER", "pair": "BTC/USD", "size_pct": 0, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 80, "drawdown_pct": 12},
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_approve_valid_short(risk_agent):
    result = await risk_agent.run(
        decision={"action": "SHORT", "pair": "BTC/USD", "size_pct": 10, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 0, "drawdown_pct": 0},
    )
    assert result["approved"] is True


@pytest.mark.asyncio
async def test_reject_short_exposure_breach(risk_agent):
    result = await risk_agent.run(
        decision={"action": "SHORT", "pair": "ETH/USD", "size_pct": 50, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 60, "drawdown_pct": 0},
    )
    assert result["approved"] is False
    assert "Exposure" in result["reasons"]


@pytest.mark.asyncio
async def test_anti_churn(risk_agent):
    # First trade — should pass
    r1 = await risk_agent.run(
        decision={"action": "BUY", "pair": "ETH/USD", "size_pct": 10, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 0, "drawdown_pct": 0},
    )
    assert r1["approved"] is True
    # Immediate second trade — should be rejected (anti-churn)
    r2 = await risk_agent.run(
        decision={"action": "BUY", "pair": "ETH/USD", "size_pct": 10, "confidence": 0.8},
        positions=[],
        portfolio_state={"nav": 1000, "cash": 1000, "total_exposure_pct": 10, "drawdown_pct": 0},
    )
    assert r2["approved"] is False
    assert "traded" in r2["reasons"].lower()
