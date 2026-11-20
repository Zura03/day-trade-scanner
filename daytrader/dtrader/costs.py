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

India futures (NSE): STT 0.05% on the sell (raised from 0.02% by Budget 2026,
from 1 April 2026), stamp duty 0.002% on the buy, NSE futures transaction
charge 0.00173%, SEBI 0.0001%, GST 18% on brokerage + exchange + SEBI; the
same per-order brokerage as stocks.

US futures: a commission per contract per side (Schwab $2.25, from a broker
comparison page - check Schwab's pricing) plus exchange + NFA fees per
contract per side (approximate CME non-member rates - check CME's fee
schedule). Costs are per contract, so they need the contract count.
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
    "us_fut": {
        "schwab":  {"label": "Schwab ($2.25 per contract)", "per_contract": 2.25},
    },
}
BROKERS["india_fut"] = BROKERS["india"]  # same per-order brokerage as stocks
DEFAULT_BROKER = {"india": "groww", "us": "alpaca", "india_fut": "fyers", "us_fut": "schwab"}

INDIA = {"stt_sell": 0.00025, "stamp_buy": 0.00003, "exch": 0.0000297, "sebi": 0.000001, "ipft": 0.000001,
         "gst": 0.18}
INDIA_FUT = {"stt_sell": 0.0005, "stamp_buy": 0.00002, "exch": 0.0000173, "sebi": 0.000001, "ipft": 0.000001,
             "gst": 0.18}
# exchange + NFA fees per contract per side (approximate)
US_FUT_FEES = {"ES": 1.40, "NQ": 1.40, "RTY": 1.40, "YM": 1.40, "MES": 0.37, "MNQ": 0.37}


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


def round_trip(market_key: str, broker: str | None, value: float, contracts: int = 0,
               symbol: str | None = None) -> Cost:
    """Cost of buying `value` worth and selling it back the same day. US futures
    are charged per contract: pass the number of contracts and the root."""
    broker = broker or DEFAULT_BROKER.get(market_key, "")
    if market_key == "us_fut":
        b = BROKERS["us_fut"].get(broker) or BROKERS["us_fut"]["schwab"]
        side = contracts * (b["per_contract"] + US_FUT_FEES.get(symbol or "", 1.40))
        return Cost(value, 2 * contracts * b["per_contract"], 2 * side - 2 * contracts * b["per_contract"], side, side)
    b = BROKERS.get(market_key, {}).get(broker)
    if market_key not in ("india", "india_fut") or b is None:
        return Cost(value, 0.0, 0.0, 0.0, 0.0)
    r = INDIA_FUT if market_key == "india_fut" else INDIA
    per_order = _brokerage(b, value)
    side_fees = value * (r["exch"] + r["sebi"] + r["ipft"])          # each side
    buy = per_order + side_fees + value * r["stamp_buy"] + r["gst"] * (per_order + side_fees)
    sell = per_order + side_fees + value * r["stt_sell"] + r["gst"] * (per_order + side_fees)
    brokerage = 2 * per_order
    return Cost(value, brokerage, buy + sell - brokerage, buy, sell)


def compare(market_key: str, value: float) -> list[tuple[str, str, Cost]]:
    return [(k, v["label"], round_trip(market_key, k, value)) for k, v in BROKERS.get(market_key, {}).items()]
