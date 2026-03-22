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
    pairs: str = "BTC/USD,ETH/USD,SOL/USD,DOGE/USD,LINK/USD"
    risk_per_trade_pct: float = 2.0
    max_position_pct: float = 30.0
    max_drawdown_pct: float = 10.0
    max_total_exposure_pct: float = 90.0
    min_trade_interval_sec: int = 120
    confidence_threshold: float = 0.55
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 12.0

    @property
    def pair_list(self) -> list[str]:
        return [p.strip() for p in self.pairs.split(",") if p.strip()]


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
