"""Tests for config module."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_settings_loads():
    from config import get_settings
    s = get_settings()
    assert s.crypto.max_capital >= 0
    assert len(s.crypto.pair_list) >= 1


def test_pair_list_parsing():
    from config import CryptoTradingSettings
    cs = CryptoTradingSettings(pairs="BTC/USD, ETH/USD, SOL/USD")
    assert cs.pair_list == ["BTC/USD", "ETH/USD", "SOL/USD"]


def test_pair_list_empty():
    from config import CryptoTradingSettings
    cs = CryptoTradingSettings(pairs="")
    assert cs.pair_list == []
