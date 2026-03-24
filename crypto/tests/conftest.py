"""Shared pytest fixtures for crypto service tests."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("COINBASE_API_KEY", "test")
os.environ.setdefault("COINBASE_API_SECRET", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SERPER_API_KEY", "test")
os.environ.setdefault("CRYPTO_MAX_CAPITAL", "1000")
os.environ.setdefault("CRYPTO_PAIRS", "BTC/USD,ETH/USD")
