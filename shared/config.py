import os
from decimal import Decimal


def _env(key: str, default: str | None = None) -> str:
    val = os.environ.get(key, default)
    if val is None:
        raise RuntimeError(f"Required env var {key!r} is not set")
    return val


def _float(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


class DB:
    user = _env("POSTGRES_USER", "trader")
    password = _env("POSTGRES_PASSWORD", "")
    host = _env("POSTGRES_HOST", "postgres")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    name = _env("POSTGRES_DB", "trader_v5")

    @classmethod
    def url(cls) -> str:
        return f"postgresql+asyncpg://{cls.user}:{cls.password}@{cls.host}:{cls.port}/{cls.name}"

    @classmethod
    def sync_url(cls) -> str:
        return f"postgresql://{cls.user}:{cls.password}@{cls.host}:{cls.port}/{cls.name}"


class OKX:
    api_key = _env("OKX_API_KEY", "")
    api_secret = _env("OKX_API_SECRET", "")
    passphrase = _env("OKX_PASSPHRASE", "")
    base_url = _env("OKX_BASE_URL", "https://www.okx.com")


class Anthropic:
    api_key = _env("ANTHROPIC_API_KEY", "")
    model = "claude-sonnet-4-6"


class Telegram:
    token = _env("TELEGRAM_BOT_TOKEN", "")
    chat_id = _env("TELEGRAM_CHAT_ID", "")


class RiskParams:
    # System-wide circuit breaker
    circuit_breaker_drawdown = _float("CIRCUIT_BREAKER_DRAWDOWN", 0.15)
    circuit_breaker_warning = _float("CIRCUIT_BREAKER_WARNING", 0.10)

    # Module A
    module_a_daily_drawdown = _float("MODULE_A_DAILY_DRAWDOWN", 0.03)
    module_a_risk_per_trade = _float("MODULE_A_RISK_PER_TRADE", 0.01)

    # Module B
    module_b_funding_threshold = _float("MODULE_B_FUNDING_THRESHOLD", 0.0005)
    module_b_max_pair_share = _float("MODULE_B_MAX_PAIR_SHARE", 0.25)
    module_b_delta_tolerance = _float("MODULE_B_DELTA_TOLERANCE", 0.005)
    module_b_negative_funding_n = int(os.environ.get("MODULE_B_NEGATIVE_FUNDING_N", "3"))

    # Overlay
    overlay_sentiment_threshold = _float("OVERLAY_SENTIMENT_THRESHOLD", -0.5)
