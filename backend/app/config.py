"""Application configuration loaded from environment variables via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlpacaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALPACA_")

    api_key: str = ""
    api_secret: str = ""
    paper: bool = False


class APIKeySettings(BaseSettings):
    fmp_api_key: str = Field("", alias="FMP_API_KEY")
    serper_api_key: str = Field("", alias="SERPER_API_KEY")
    tavily_api_key: str = Field("", alias="TAVILY_API_KEY")
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")


class DatabaseSettings(BaseSettings):
    database_url: str = Field(
        "postgresql+asyncpg://localhost:5432/alphapaca", alias="DATABASE_URL"
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")


class AuthSettings(BaseSettings):
    nextauth_secret: str = Field("", alias="NEXTAUTH_SECRET")
    dashboard_password: str = Field("", alias="DASHBOARD_PASSWORD")


class PodAllocationSettings(BaseSettings):
    """Capital allocation percentages per strategy pod (must sum to <=100)."""

    model_config = SettingsConfigDict(env_prefix="POD_ALLOC_")

    momentum: int = 25
    mean_reversion: int = 20
    event_driven: int = 15
    sector_rotation: int = 0
    stat_arb: int = 25
    volatility: int = 15


class RiskEngineSettings(BaseSettings):
    """Portfolio-level risk engine parameters."""

    model_config = SettingsConfigDict(env_prefix="RISK_")

    target_market_beta: float = 0.3
    max_factor_exposure: float = 0.3
    max_daily_var_pct: float = 2.0
    max_stress_loss_pct: float = 10.0
    max_gross_exposure_pct: float = 120.0
    max_net_exposure_pct: float = 80.0
    max_pod_return_corr: float = 0.60


class CapitalSettings(BaseSettings):
    """Capital allocation controls -- caps how much of the Alpaca account the system may use."""

    model_config = SettingsConfigDict(env_prefix="CAPITAL_")

    max_tradable: float = Field(
        0.0,
        description="Maximum capital the system is allowed to deploy (USD). 0 means use full account equity. If set, effective NAV = min(account_equity, max_tradable).",
    )
    reserve_cash_pct: float = 5.0


class PositionSizingSettings(BaseSettings):
    """Per-trade and portfolio position sizing constraints."""

    model_config = SettingsConfigDict(env_prefix="POS_")

    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 5.0
    max_swing_position_gap_adj: float = 2.0
    max_concurrent_positions: int = 12
    max_positions_per_pod: int = 4
    max_event_driven_positions: int = 2


class DrawdownThresholdSettings(BaseSettings):
    """Portfolio state-machine drawdown thresholds."""

    model_config = SettingsConfigDict(env_prefix="DD_")

    reduced_pct: float = 1.5
    halted_pct: float = 3.0
    panic_pct: float = 5.0
    weekly_halt_pct: float = 5.0
    monthly_halt_pct: float = 8.0


class ExecutionSettings(BaseSettings):
    """Order execution and paper-to-live promotion gates."""

    model_config = SettingsConfigDict(env_prefix="EXEC_")

    fill_timeout_sec: int = 300
    max_fill_retries: int = 2
    min_paper_days_for_live: int = 30
    min_paper_sharpe_for_live: float = 1.0
    min_paper_trades_for_live: int = 50
    min_shadow_days: int = 7


class PreTradeSettings(BaseSettings):
    """Pre-trade liquidity and correlation filters."""

    model_config = SettingsConfigDict(env_prefix="PRETRADE_")

    min_avg_volume: int = 500_000
    min_avg_dollar_vol: int = 5_000_000
    max_spread_pct: float = 0.10
    max_pairwise_correlation: float = 0.80


class SignalQualificationSettings(BaseSettings):
    """Out-of-sample signal qualification gates."""

    model_config = SettingsConfigDict(env_prefix="SIG_")

    min_oos_samples_fast: int = 20
    min_oos_samples_slow: int = 12
    min_oos_winrate: int = 52
    min_oos_profit_factor: float = 1.3
    min_signal_ic: float = 0.03
    signal_ic_disable_threshold: float = 0.0


class RegimeSettings(BaseSettings):
    """HMM regime detection parameters."""

    model_config = SettingsConfigDict(env_prefix="REGIME_")

    benchmark_symbol: str = "SPY"
    training_window_days: int = 252
    retrain_interval_days: int = 7
    min_observations: int = 60
    n_regimes: int = 4


class KellySettings(BaseSettings):
    """Kelly criterion position sizing parameters."""

    model_config = SettingsConfigDict(env_prefix="KELLY_")

    fraction: float = 0.25
    min_risk_pct: float = 0.5
    max_risk_pct: float = 3.0
    rolling_window: int = 100


class Settings(BaseSettings):
    """Root settings aggregating all sub-configurations."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca: AlpacaSettings = AlpacaSettings()
    api_keys: APIKeySettings = APIKeySettings()
    database: DatabaseSettings = DatabaseSettings()
    auth: AuthSettings = AuthSettings()
    pods: PodAllocationSettings = PodAllocationSettings()
    risk: RiskEngineSettings = RiskEngineSettings()
    capital: CapitalSettings = CapitalSettings()
    position_sizing: PositionSizingSettings = PositionSizingSettings()
    drawdown: DrawdownThresholdSettings = DrawdownThresholdSettings()
    execution: ExecutionSettings = ExecutionSettings()
    pre_trade: PreTradeSettings = PreTradeSettings()
    signal_qualification: SignalQualificationSettings = SignalQualificationSettings()
    regime: RegimeSettings = RegimeSettings()
    kelly: KellySettings = KellySettings()


_settings: Settings | None = None


def get_settings() -> Settings:
    """Factory cached at module level; import this for DI."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
