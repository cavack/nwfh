from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    log_level: str = "INFO"
    environment: str = "production"
    registry_db_path: str = "/app/data/waterfall_registry.db"
    source_revision: str | None = None
    backtest_artifact_hmac_key: str | None = None

    # Safety boundary: WaterfallHunter remains SIGNAL_ONLY and never places orders.
    live_trading_enabled: bool = False

    # Temporary, explicitly versioned signal-discovery profile.
    experimental_pretrigger_enabled: bool = False
    experimental_pretrigger_threshold: float = 45.0

    # LBank execution shadow observation (observational only).
    lbank_execution_shadow_enabled: bool = False
    lbank_execution_shadow_batch_size: int = 8
    lbank_execution_shadow_interval_seconds: float = 60.0
    lbank_execution_shadow_success_recheck_seconds: float = 1800.0
    lbank_execution_shadow_failure_recheck_seconds: float = 600.0

    # Telegram command bot + signal delivery.
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_signal_delivery_enabled: bool = False
    telegram_signal_delivery_cutover_at: int | None = 1720000000
    # 12-hour health report interval (in seconds, default 43200 = 12h)
    telegram_health_report_interval: int = 43200

    # AI advisory: TypeSafe System One (Jev). Observational only.
    typesafe_api_key: str | None = None
    typesafe_base_url: str = "https://api.typesafe.ai"
    typesafe_model: str = "jev-latest"
    typesafe_timeout_seconds: float = 30.0

    # CoinGlass derivatives.
    coinglass_api_key: str | None = None
    coinglass_base_url: str = "https://open-api-v4.coinglass.com"

    # DexScreener DEX data.
    dexscreener_enabled: bool = False
    dexscreener_token_map_json: str = "{}"

    # CoinGecko market data (free API, no key needed for basic tier).
    coingecko_api_key: str | None = None
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"

    # LunarCrush social sentiment.
    lunarcrush_api_key: str | None = None
    lunarcrush_base_url: str = "https://lunarcrush.com/api4/public"

    # X/Twitter social data (optional, uses free Nitter fallback if no key).
    twitter_api_key: str | None = None
    twitter_api_secret: str | None = None
    twitter_bearer_token: str | None = None

    # Etherscan/Solscan on-chain data.
    etherscan_api_key: str | None = None
    solscan_api_key: str | None = None
    onchain_large_transfer_usd: float = 100_000.0

    # Operator token for dashboard settings mutations. Unset means settings
    # changes are refused outright — the panel is read-only until an operator
    # deliberately provisions a token.
    operator_token: str | None = None

    # Backtester settings.
    backtester_initial_capital: float = 200.0
    backtester_risk_per_trade: float = 0.02
    backtester_db_path: str = "/app/data/backtest.db"
    # Live per-signal observational outcome tracking. The path was previously hardcoded in
    # three places (main.py, backtester_v2.py, telegram_enhanced.py).
    backtester_v2_db_path: str = "/app/data/backtest_v2.db"


settings = Settings()
