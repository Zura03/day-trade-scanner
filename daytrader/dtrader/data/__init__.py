from __future__ import annotations

from ..config import alpaca_keys, kite_keys, load_config, schwab_keys
from .base import DataProvider


def get_provider(cfg: dict | None = None, source: str | None = None) -> DataProvider:
    """US:    auto = Alpaca if its keys are in .env, else Schwab, else free Yahoo.
    India: auto = your broker (Groww, else Zerodha Kite) once today's login is
           done, else free Yahoo (NSE)."""
    cfg = cfg or load_config()
    market = cfg["market"]
    source = source or cfg.get("data_source", "auto")
    if market.key == "india":
        return _india_provider(source)
    akeys, skeys = alpaca_keys(), schwab_keys()
    if source == "alpaca" or (source == "auto" and akeys):
        if not akeys:
            raise RuntimeError("data_source is 'alpaca' but ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set in .env")
        from .alpaca_provider import AlpacaProvider

        return AlpacaProvider(akeys[0], akeys[1], (cfg.get("alpaca") or {}).get("feed", "sip_delayed"))
    if source == "schwab" or (source == "auto" and skeys):
        if not skeys:
            raise RuntimeError("data_source is 'schwab' but SCHWAB_APP_KEY / SCHWAB_APP_SECRET are not set in .env")
        from .schwab_provider import SchwabProvider

        s = cfg.get("schwab", {})
        return SchwabProvider(skeys[0], skeys[1], s.get("callback_url", "https://127.0.0.1:8182"),
                              s.get("token_path", "data/schwab_token.json"))
    if source not in ("auto", "yfinance"):
        raise RuntimeError(f"Data source '{source}' is not available for the US market")
    from .yf_provider import YFinanceProvider

    return YFinanceProvider()


# ---- India brokers ----------------------------------------------------------
def india_broker() -> str | None:
    """Which broker API is configured in .env (Groww preferred)."""
    from .groww_provider import groww_credentials

    if groww_credentials():
        return "groww"
    if kite_keys():
        return "kite"
    return None


def _broker_provider(broker: str, fetch_token: bool = True) -> DataProvider | None:
    if broker == "groww":
        from .groww_provider import GrowwProvider, cached_token, groww_token

        tok = groww_token() if fetch_token else cached_token()
        return GrowwProvider(tok) if tok else None
    if broker == "kite":
        from .kite_provider import KiteProvider, token_valid_today

        tok, keys = token_valid_today(), kite_keys()
        return KiteProvider(keys[0], tok) if tok and keys else None
    return None


def _india_provider(source: str) -> DataProvider:
    if source in ("groww", "kite"):
        p = _broker_provider(source)
        if p is None:
            raise RuntimeError(india_login_hint(source) or f"{source} is not configured in .env")
        return p
    if source == "auto":
        broker = india_broker()
        p = _broker_provider(broker) if broker else None
        if p is not None:
            return p
    elif source != "yfinance":
        raise RuntimeError(f"Data source '{source}' is not available for the India market")
    from .yf_provider import YFinanceIndiaProvider

    return YFinanceIndiaProvider()


def india_login_needed() -> bool:
    """A broker is configured but there's no token for today yet (no network call)."""
    broker = india_broker()
    if broker == "groww":
        from .groww_provider import cached_token

        return not cached_token()
    if broker == "kite":
        from .kite_provider import token_valid_today

        return not token_valid_today()
    return False


def india_login_hint(broker: str | None = None) -> str | None:
    broker = broker or india_broker()
    if broker == "groww":
        from .groww_provider import groww_credentials

        c = groww_credentials()
        if not c:
            return "Groww: set GROWW_API_KEY / GROWW_API_SECRET in .env"
        if c["mode"] == "approval":
            return "Groww: approve today's API key on Groww Cloud (API Keys page)"
        return "Groww: TOTP login failed - check GROWW_TOTP_TOKEN / GROWW_TOTP_SECRET"
    if broker == "kite":
        if not kite_keys():
            return "Zerodha: set KITE_API_KEY / KITE_API_SECRET in .env"
        return "Zerodha: log in for today with  ./run.sh --market india auth"
    return None


def india_token_state() -> str:
    """Changes when a new broker token appears (dashboard cache key)."""
    broker = india_broker()
    if broker == "groww":
        from .groww_provider import cached_token

        return f"groww:{cached_token() or ''}"
    if broker == "kite":
        from .kite_provider import token_valid_today

        return f"kite:{token_valid_today() or ''}"
    return "none"
