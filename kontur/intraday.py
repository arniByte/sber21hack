"""Flat-by-18:00 checkpoint replay on pinned MOEX hourly archive candles."""

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from math import floor, isfinite
from urllib.request import urlopen

from .contest import CAPITAL, contest_scores
from .data import MSK
from .horizons import monthly_stats
from .study import trade_attribution

ARCHIVE = "https://raw.githubusercontent.com/vitalio1895/moex-candles/fd2589ae2229c0ebab93717cae32382e80d4100d/data/1h_hourly/"
PINNED = {
    "GAZP": "f24b3348f4797f6b6c8b1484b9e06e69c8082f71b9176b0a8ef3571579dccae6",
    "LKOH": "7adf91671f17655952c5199bdf66800cbdd3e6e1912fb283ec6d6815c154b770",
    "SBER": "e7c985154e2dd2ae9c77026caf7e0092d6dbf38ca25f71da3cb9425ca19f4bba",
    "TATN": "a1bd41ceaef4ba20c69c02688edc9c2c2fb055e9644973ed0c4e83291e70b682",
}
LOTS = {"GAZP": 10, "LKOH": 1, "SBER": 1, "TATN": 1}
LAST_ENTRY = 16
FLAT_AT = 18
DAILY_RISK = .003
NOTIONAL_CAP = 1.0
HOLDOUT_START = date(2026, 7, 1)


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self):
        if (any(not isfinite(value) or value <= 0 for value in (self.open, self.high, self.low, self.close))
                or self.low > min(self.open, self.close) or self.high < max(self.open, self.close)):
            raise ValueError("Некорректные цены часовой свечи.")


@dataclass(frozen=True)
class Setup:
    side: int
    stop: float
    target: float
    loss: float
    ratio: float


def sessions(root, download=False):
    data = defaultdict(lambda: defaultdict(dict))
    root.mkdir(parents=True, exist_ok=True)
    for symbol, expected in PINNED.items():
        path = root / (symbol + ".csv")
        if download and not path.exists():
            with urlopen(ARCHIVE + path.name, timeout=25) as response:
                content = response.read(5_000_000)
            if sha256(content).hexdigest() != expected:
                raise ValueError(f"Контрольная сумма не совпала: {symbol}.")
            path.write_bytes(content)
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Контрольная сумма не совпала: {symbol}.")
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                stamp = datetime.fromisoformat(row["datetime_msk"]).astimezone(MSK)
                if row["session"] != "REGULAR" or stamp.weekday() >= 5 or not 10 <= stamp.hour <= FLAT_AT:
                    continue
                if stamp.minute or stamp.second or stamp.microsecond:
                    raise ValueError("Архив должен содержать часовые свечи.")
                history = data[stamp.date()][symbol]
                if stamp.hour in history:
                    raise ValueError(f"Повтор свечи: {symbol}, {stamp.isoformat()}.")
                history[stamp.hour] = Candle(*(float(row[key]) for key in ("open", "high", "low", "close")))
    expected_hours = set(range(10, FLAT_AT + 1))
    complete = {day: values for day, values in sorted(data.items())
                if all(values[symbol].keys() == expected_hours for symbol in PINNED)}
    return complete, len(data) - len(complete)


def fills(side, entry, exit, cost):
    entry_fill = entry * (1 + side * cost)
    exit_fill = exit * (1 - side * cost)
    fee = cost * (entry_fill + exit_fill)
    return entry_fill, exit_fill, side * (exit_fill - entry_fill) - fee, fee


def reversal(history, hour, cost):
    band, previous, opening = history[10], history[hour-1], history[hour].open
    if not band.low < previous.close < band.high:
        return None
    down = previous.low < band.low
    up = previous.high > band.high
    if down == up:
        return None
    side = 1 if down else -1
    middle = (band.high + band.low) / 2
    if (side == 1 and not band.low <= opening <= middle or
            side == -1 and not middle <= opening <= band.high):
        return None
    return bracket(side, previous, opening, cost, band.high if side == 1 else band.low)


def retest(history, hour, cost):
    if hour < 13:
        return None
    band, breakout, test, opening = history[10], history[hour-2], history[hour-1], history[hour].open
    width = band.high - band.low
    if (breakout.close > band.high and test.low <= band.high < test.close
            and band.high <= opening <= band.high + width / 4):
        return bracket(1, test, opening, cost)
    if (breakout.close < band.low and test.high >= band.low > test.close
            and band.low - width / 4 <= opening <= band.low):
        return bracket(-1, test, opening, cost)
    return None


def bracket(side, evidence, opening, cost, boundary=None):
    stop = evidence.low * .9997 if side == 1 else evidence.high * 1.0003
    distance = side * (opening - stop)
    if not .0015 <= distance / opening <= .0125:
        return None
    spread = 2 * distance
    if boundary is not None:
        spread = min(spread, side * (boundary - opening))
    target = opening + side * spread
    loss = -fills(side, opening, stop, cost)[2]
    profit = fills(side, opening, target, cost)[2]
    return Setup(side, stop, target, loss, profit / loss) if profit >= loss * 1.25 else None


def exit_at(bar, setup, cutoff=False):
    side = setup.side
    if cutoff:
        return bar.open, "cutoff", bar.open
    if side * (bar.open - setup.stop) <= 0:
        return bar.open, "gap", bar.open
    if side * (bar.open - setup.target) >= 0:
        return setup.target, "target", setup.target
    adverse = bar.low if side == 1 else bar.high
    favorable = bar.high if side == 1 else bar.low
    if side * (adverse - setup.stop) <= 0:
        return setup.stop, "stop", setup.stop
    if side * (favorable - setup.target) >= 0:
        return setup.target, "target", adverse
    return None, None, adverse


def simulate(days, *, entry=reversal, cost_bps=5):
    cost = cost_bps / 10_000
    cash = peak = CAPITAL
    equity, trades = [], []
    skipped_lot = 0
    for day, symbols in sorted(days.items()):
        daily_dd = 0.0
        trade = None
        for hour in range(12, LAST_ENTRY + 1):
            candidates = []
            for symbol, history in symbols.items():
                opening = history[hour].open
                setup = entry(history, hour, cost)
                if setup:
                    lot = LOTS[symbol]
                    price = fills(setup.side, opening, opening, cost)[0]
                    qty = floor(min(cash * DAILY_RISK / setup.loss, cash * NOTIONAL_CAP / price) / lot) * lot
                    if qty:
                        candidates.append((setup.ratio, symbol, setup, qty))
                    else:
                        skipped_lot += 1
            if not candidates:
                continue
            _, symbol, setup, qty = min(candidates, key=lambda item: (-item[0], item[1]))
            history = symbols[symbol]
            opening = history[hour].open
            for end in range(hour, FLAT_AT + 1):
                candle = history[end]
                reference, reason, adverse = exit_at(candle, setup, end == FLAT_AT)
                heat = cash + qty * fills(setup.side, opening, adverse, cost)[2]
                daily_dd = max(daily_dd, 100 * (peak - heat) / peak)
                if reference is None:
                    peak = max(peak, cash + qty * fills(setup.side, opening, candle.close, cost)[2])
                    continue
                entry_price, exit_price, unit_pnl, fee = fills(setup.side, opening, reference, cost)
                trade = {
                    "symbol": symbol, "side": "LONG" if setup.side == 1 else "SHORT",
                    "trade_day": day.isoformat(), "entry_at": f"{day}T{hour:02d}:00:00+03:00",
                    "exit_at": f"{day}T{end:02d}:00:00+03:00" if reason in ("gap", "cutoff") else None,
                    "exit_hour_msk": end, "reason": reason, "quantity": qty,
                    "entry_price": entry_price, "exit_price": exit_price, "stop": setup.stop, "target": setup.target,
                    "gross_pnl": qty * setup.side * (reference - opening), "commission": qty * fee,
                    "slippage": qty * cost * (opening + reference),
                    "net_pnl": qty * unit_pnl, "pnl": qty * unit_pnl,
                    "planned_risk": qty * setup.loss, "notional_multiple": qty * entry_price / cash,
                }
                cash += trade["net_pnl"]
                peak = max(peak, cash)
                break
            break
        if trade:
            trades.append(trade)
        equity.append({"date": day.isoformat(), "equity": cash, "drawdown_pct": max(daily_dd, 100 * (peak-cash) / peak)})
    net = cash - CAPITAL
    gains = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    loss = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    metrics = {"initial_capital": CAPITAL, "net_pnl": net, "return_pct": net / CAPITAL * 100,
               "trade_count": len(trades), "trade_days": len(trades), "max_drawdown_pct": max(
                   (p["drawdown_pct"] for p in equity), default=0), "profit_factor": gains / loss if loss else None,
               "no_losses": bool(gains and not loss), "expectancy": net / len(trades) if trades else None,
               "concentration_pct": 100 * max(t["pnl"] for t in trades) / net if net > 0 and trades else None}
    return {"equity": equity, "metrics": metrics, "trades": trades, "skipped_too_expensive": skipped_lot}


def study(days):
    late = {d: v for d, v in days.items() if d >= HOLDOUT_START}
    periods = {
        "checkpoint": ("Возврат · весь архив", days, reversal),
        "checkpoint_holdout": ("Возврат · позднее окно", late, reversal),
        "checkpoint_retest": ("Ретест · весь архив", days, retest),
        "checkpoint_retest_holdout": ("Ретест · позднее окно", late, retest),
    }
    results = {}
    for key, (label, subset, entry) in periods.items():
        if not subset:
            raise ValueError("Архив не покрывает проверочные периоды.")
        result = simulate(subset, entry=entry)
        stressed = simulate(subset, entry=entry, cost_bps=10)
        result.update(start=min(subset), end=max(subset), label=label, sessions=len(subset),
                      stress_pct=stressed["metrics"]["return_pct"],
                      stress_trades=stressed["metrics"]["trade_count"],
                      monthly=monthly_stats(result["equity"], CAPITAL),
                      contest=contest_scores(result["metrics"]),
                      attribution=trade_attribution(result["trades"]))
        result["by_side"] = {
            side: {"count": len(rows), "net": sum(t["net_pnl"] for t in rows)}
            for side in ("LONG", "SHORT") if (rows := [t for t in result["trades"] if t["side"] == side])
        }
        results[key] = result
    return results
