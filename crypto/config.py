"""Crypto trading service configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = str(Path(__file__).resolve().parent.parent / ".env.local")


class CoinbaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COINBASE_", env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )
    api_key: str = ""
    api_secret: str = ""


class APIKeySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )
    serper_api_key: str = Field("", alias="SERPER_API_KEY")
    tavily_api_key: str = Field("", alias="TAVILY_API_KEY")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )
    database_url: str = Field(
        "postgresql+asyncpg://localhost:5432/alphapaca", alias="DATABASE_URL"
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )
    bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    chat_id: str = Field("", alias="TELEGRAM_CHAT_ID")


class CryptoTradingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRYPTO_", env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore",
    )
    max_capital: float = 0.0
    pairs: str = "BTC/USD,ETH/USD,SOL/USD"
    max_risk_per_trade_pct: float = 5.0
    max_leverage: float = 5.0
    min_conviction: float = 0.75
    daily_loss_halt_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_concurrent_per_bot: int = 3
    max_concurrent_total: int = 5
    # Day bot
    day_min_rr_ratio: float = 1.5
    day_min_trade_interval_sec: int = 300
    day_max_hold_hours: float = 6.0
    day_eval_interval_sec: int = 30
    # Swing bot
    swing_min_rr_ratio: float = 2.0
    swing_min_trade_interval_sec: int = 3600
    swing_eval_interval_sec: int = 3600
    # Cooldown
    cooldown_after_losses: int = 3
    cooldown_halt_after_losses: int = 5

    @property
    def pair_list(self) -> list[str]:
        return [p.strip() for p in self.pairs.split(",") if p.strip()]

    # Backward-compat aliases for legacy modules (position_sizer, risk_validator)
    @property
    def risk_per_trade_pct(self) -> float:
        return self.max_risk_per_trade_pct

    @property
    def max_position_pct(self) -> float:
        return self.max_risk_per_trade_pct * 10

    @property
    def max_total_exposure_pct(self) -> float:
        return 100.0

    @property
    def min_trade_interval_sec(self) -> int:
        return self.day_min_trade_interval_sec


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )
    coinbase: CoinbaseSettings = CoinbaseSettings()
    api_keys: APIKeySettings = APIKeySettings()
    database: DatabaseSettings = DatabaseSettings()
    telegram: TelegramSettings = TelegramSettings()
    crypto: CryptoTradingSettings = CryptoTradingSettings()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
