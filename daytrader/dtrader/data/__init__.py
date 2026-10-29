from __future__ import annotations

from ..config import alpaca_keys, kite_keys, load_config, schwab_keys
from .base import DataProvider


def get_provider(cfg: dict | None = None, source: str | None = None) -> DataProvider:
    """US:    auto = Alpaca if its keys are in .env, else Schwab, else free Yahoo.
    India: auto = Zerodha Kite if keys are set and you logged in today, else free Yahoo (NSE)."""
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


def _india_provider(source: str) -> DataProvider:
    if source in ("kite", "auto"):
        from .kite_provider import token_valid_today

        keys, token = kite_keys(), token_valid_today()
        if keys and token:
            from .kite_provider import KiteProvider

            return KiteProvider(keys[0], token)
        if source == "kite":
            raise RuntimeError("Zerodha: " + ("set KITE_API_KEY / KITE_API_SECRET in .env" if not keys
                                              else "log in for today with  ./run.sh --market india auth"))
    elif source != "yfinance":
        raise RuntimeError(f"Data source '{source}' is not available for the India market")
    from .yf_provider import YFinanceIndiaProvider

    return YFinanceIndiaProvider()


def kite_login_needed() -> bool:
    """Kite keys are configured but today's login hasn't happened yet."""
    from .kite_provider import token_valid_today

    return bool(kite_keys()) and not token_valid_today()
