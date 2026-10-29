from __future__ import annotations

from ..config import alpaca_keys, load_config, schwab_keys
from .base import DataProvider


def get_provider(cfg: dict | None = None, source: str | None = None) -> DataProvider:
    """auto = Alpaca if its keys are in .env, else Schwab, else free Yahoo."""
    cfg = cfg or load_config()
    source = source or cfg.get("data_source", "auto")
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
    from .yf_provider import YFinanceProvider

    return YFinanceProvider()
