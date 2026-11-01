"""Trading costs per round trip: broker brokerage + statutory charges.

India (NSE, intraday equity) statutory rates are the same at every broker:
STT 0.025% on the sell, stamp duty 0.003% on the buy, NSE transaction
charge 0.00297% and SEBI + IPFT 0.0001% each on both sides, GST 18% on
brokerage + exchange + SEBI + IPFT. Brokerage per executed order is
min(pct x order value, cap), floored at `min`. Rates come from the brokers'
published pricing (Groww's own page; others from comparison sites - verify).

US: Alpaca/Schwab stock trades are commission-free; regulatory fees are a
fraction of a cent per share, so costs default to zero (slippage is
modelled separately in the backtest).
"""
from __future__ import annotations

from dataclasses import dataclass

# brokerage per executed order: pct of order value, capped, with a floor
BROKERS = {
    "india": {
        "groww":   {"label": "Groww",   "pct": 0.001,  "cap": 20.0, "min": 5.0},
        "zerodha": {"label": "Zerodha", "pct": 0.0003, "cap": 20.0, "min": 0.0},
        "dhan":    {"label": "Dhan",    "pct": 0.0003, "cap": 20.0, "min": 0.0},
        "fyers":   {"label": "Fyers",   "pct": 0.0003, "cap": 20.0, "min": 0.0},
        "angel":   {"label": "Angel One", "pct": 0.001, "cap": 20.0, "min": 5.0},
        "shoonya": {"label": "Shoonya", "pct": 0.0003, "cap": 5.0,  "min": 0.0},
        "mstock":  {"label": "m.Stock", "pct": None,   "cap": 10.0, "min": 10.0},
    },
    "us": {
        "alpaca":  {"label": "Alpaca (commission-free)", "pct": 0.0, "cap": 0.0, "min": 0.0},
        "schwab":  {"label": "Schwab (commission-free)", "pct": 0.0, "cap": 0.0, "min": 0.0},
    },
}
DEFAULT_BROKER = {"india": "groww", "us": "alpaca"}

INDIA = {"stt_sell": 0.00025, "stamp_buy": 0.00003, "exch": 0.0000297, "sebi": 0.000001, "ipft": 0.000001,
         "gst": 0.18}


@dataclass
class Cost:
    value: float          # order value per side
    brokerage: float      # both orders
    taxes: float          # statutory + GST, both sides
    buy_side: float       # everything paid on the buy
    sell_side: float      # everything paid on the sell

    @property
    def total(self) -> float:
        return self.brokerage + self.taxes

    @property
    def pct(self) -> float:
        return self.total / self.value * 100 if self.value else 0.0


def broker_label(market_key: str, broker: str) -> str:
    return BROKERS.get(market_key, {}).get(broker, {}).get("label", broker)


def _brokerage(b: dict, value: float) -> float:
    if b["pct"] is None:
        return b["cap"]
    return max(min(b["pct"] * value, b["cap"]), b["min"]) if value > 0 else 0.0


def round_trip(market_key: str, broker: str | None, value: float) -> Cost:
    """Cost of buying `value` worth and selling it back the same day."""
    broker = broker or DEFAULT_BROKER.get(market_key, "")
    b = BROKERS.get(market_key, {}).get(broker)
    if market_key != "india" or b is None:
        return Cost(value, 0.0, 0.0, 0.0, 0.0)
    r = INDIA
    per_order = _brokerage(b, value)
    side_fees = value * (r["exch"] + r["sebi"] + r["ipft"])          # each side
    buy = per_order + side_fees + value * r["stamp_buy"] + r["gst"] * (per_order + side_fees)
    sell = per_order + side_fees + value * r["stt_sell"] + r["gst"] * (per_order + side_fees)
    brokerage = 2 * per_order
    return Cost(value, brokerage, buy + sell - brokerage, buy, sell)


def compare(market_key: str, value: float) -> list[tuple[str, str, Cost]]:
    return [(k, v["label"], round_trip(market_key, k, value)) for k, v in BROKERS.get(market_key, {}).items()]
