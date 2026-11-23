"""Frozen multi-session experiment on a shared market calendar."""

from dataclasses import dataclass
from datetime import date
from math import floor

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .contest import CAPITAL, drawdown_score, profit_factor_score

HOLD = 5
STOP = 0.03
SOFT_DRAWDOWN = 0.02
HALT_DRAWDOWN = 0.08
TRAIN_SESSIONS = 252
REFIT_SESSIONS = 63
FEATURES = ("return_5", "return_20", "return_60", "distance_sma_60", "volatility_20",
            "range_20", "breadth_60")
STRATEGIES = {
    "trend": "Тренд · 5 сессий",
    "ridge": "Ridge · 5 сессий",
    "rotation": "Недельная ротация",
    "pullback": "Покупка отката",
}


@dataclass(frozen=True)
class Rules:
    sessions: int | None = HOLD
    stop: float = STOP
    position: float = .15
    risk: float = .0045


PROFILES = {
    "trend": Rules(),
    "ridge": Rules(),
    "rotation": Rules(None, .05, .30, .012),
    "pullback": Rules(7, .04, .30, .012),
}


@dataclass(frozen=True)
class Signal:
    ticker: str
    score: float


def shared_calendar(bars):
    book = {}
    for bar in bars:
        book.setdefault(bar.day, {})[bar.symbol] = bar
    days = sorted(book)
    universe = sorted({bar.symbol for bar in bars})
    if not days or any(sorted(book[day]) != universe for day in days):
        raise ValueError("Для исследования нужны свечи всех выбранных бумаг на каждой наблюдаемой сессии.")
    return days, universe, book


def exit_price(bar, stop, timed):
    if bar.open <= stop:
        return bar.open, "gap"
    if bar.low <= stop:
        return stop, "stop"
    return (bar.close, "close") if timed else (None, None)


def rule_signals(days, symbols, prices, first):
    """Candidates known at the last close, evaluated on the next session."""
    rotation, pullback = {}, {}
    for index in range(max(120, first), len(days) - 1):
        signal_day, trade_day = days[index:index+2]
        if (trade_day - signal_day).days > 7:
            continue
        level = prices[index]
        base = level > prices[index-119:index+1].mean(axis=0)
        recent = prices[index-20:index+1]
        vol = np.maximum((recent[1:] / recent[:-1] - 1).std(axis=0), .001)
        ret_3 = level / prices[index-3] - 1
        ret_20 = level / recent[0] - 1
        pullback[trade_day] = [Signal(symbol, float(-ret_3[k] / vol[k]))
                               for k, symbol in enumerate(symbols)
                               if base[k] and ret_20[k] > 0 and ret_3[k] < -vol[k]]
        if trade_day.isocalendar()[:2] == signal_day.isocalendar()[:2]:
            continue
        ret_60 = level / prices[index-60] - 1
        ret_120 = level / prices[index-120] - 1
        rotation[trade_day] = [Signal(symbol, float((ret_60[k] + ret_120[k]) / (2 * vol[k])))
                               for k, symbol in enumerate(symbols) if base[k] and ret_60[k] > 0]
    return {"rotation": rotation, "pullback": pullback}



def forecasts(days, symbols, book):
    prices = np.array([[book[day][symbol].close for symbol in symbols] for day in days])
    daily_bars = [[book[day][symbol] for symbol in symbols] for day in days]
    ranges = np.array([[(bar.high - bar.low) / bar.open for bar in session] for session in daily_bars])
    x, y = [], []
    for i in range(60, len(days) - 1):
        level = prices[i]
        trend = level / prices[i-59:i+1].mean(axis=0) - 1
        period = prices[i-20:i+1]
        vol = np.maximum((period[1:] / period[:-1] - 1).std(axis=0), .001)
        x.append(np.column_stack([
            level / prices[i-5] - 1, level / prices[i-20] - 1,
            level / prices[i-60] - 1, trend, vol, ranges[i-19:i+1].mean(axis=0),
            np.full(len(symbols), (trend > 0).mean())
        ]))
        if i + HOLD >= len(days):
            continue
        values = []
        for symbol in symbols:
            opening = book[days[i+1]][symbol].open
            for j in range(i + 1, i + HOLD + 1):
                price, _ = exit_price(book[days[j]][symbol], opening * (1 - STOP), j == i + HOLD)
                if price is not None:
                    values.append(price / opening - 1)
                    break
        y.append(values)
    universe_size = len(symbols)
    if len(y) < TRAIN_SESSIONS + 1:
        raise ValueError("Недостаточно истории для обучения и отложенного прогноза.")
    fit_features = np.array(x)
    labels = np.array(y)
    results = {"trend": {}, "ridge": {}}
    folds = []
    eligible = np.array([(right-left).days <= 7 for left, right in zip(days[60:-1], days[61:])])
    with threadpool_limits(limits=1):
        for start in range(TRAIN_SESSIONS + HOLD, len(x), REFIT_SESSIONS):
            # The last retained label resolves strictly before this fold's first signal close.
            train_end = start - HOLD
            end = min(start + REFIT_SESSIONS, len(x))
            mask = eligible[:train_end]
            model = make_pipeline(StandardScaler(), Ridge(alpha=18))
            model.fit(fit_features[:train_end][mask].reshape(-1, len(FEATURES)),
                      labels[:train_end][mask].reshape(-1))
            values = model.predict(fit_features[start:end].reshape(-1, len(FEATURES)))
            values = values.reshape(-1, universe_size)
            folds.append({"train_label_end": days[60 + train_end - 1 + HOLD],
                          "first_signal": days[60 + start], "last_signal": days[60 + end - 1],
                          "train_examples": int(mask.sum()) * universe_size})
            for row in range(start, end):
                day = days[61 + row]
                candidates = [k for k in range(universe_size)
                              if eligible[row] and x[row][k, 1] > 0 and x[row][k, 3] > 0]
                results["trend"][day] = [Signal(symbols[k], float(x[row][k, 1] / x[row][k, 4]))
                                         for k in candidates]
                results["ridge"][day] = [Signal(symbols[k], float(values[row - start][k]))
                                         for k in candidates]
    results.update(rule_signals(days, symbols, prices, 60 + TRAIN_SESSIONS + HOLD))
    return results, folds


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: int
    opening: float
    cost: float
    entry_day: date
    entry_index: int


def replay(days, book, signals, lots, *, forecast_gate=False, reinvest=True, cost_bps=5,
           capital=CAPITAL, rules=PROFILES["trend"]):
    cost = cost_bps / 10_000
    buy = (1 + cost) ** 2
    sell = (1 - cost) ** 2
    gate = buy / sell - 1 + .001
    cash = equity = peak = capital
    position = None
    trailing_close = 0.0
    halt = None
    curve, trades = [], []
    exposed = 0
    for index, day in enumerate(days):
        candidates = sorted(signals.get(day, ()), key=lambda signal: (-signal.score, signal.ticker))
        if position is None and halt is None:
            budget = equity if reinvest else capital
            scale = .5 if equity <= peak * (1 - SOFT_DRAWDOWN) else 1
            for signal in candidates:
                if forecast_gate and signal.score <= gate:
                    break
                opening = book[day][signal.ticker].open
                unit_cost = opening * buy
                unit_risk = unit_cost - opening * (1 - rules.stop) * sell
                lot = lots[signal.ticker]
                quantity = floor(min(cash / unit_cost, budget * scale * rules.position / unit_cost,
                                     budget * scale * rules.risk / unit_risk) / lot) * lot
                if quantity:
                    position = Position(signal.ticker, quantity, opening, unit_cost * quantity, day, index)
                    trailing_close = opening
                    cash -= position.cost
                    break
        heat = equity = cash
        if position:
            exposed += 1
            bar = book[day][position.ticker]
            rotates = rules.sessions is None
            stop_base = trailing_close if rotates else position.opening
            stop = stop_base * (1 - rules.stop)
            limit = not rotates and index - position.entry_index + 1 >= rules.sessions
            dropped = (rotates and day in signals and position.entry_day != day
                       and position.ticker not in {s.ticker for s in candidates[:2]})
            if dropped and bar.open > stop:
                reference, reason = bar.open, "rotation"
            else:
                reference, reason = exit_price(bar, stop, limit or index == len(days) - 1)
            # Stops and planned opening exits remove exposure to later lows.
            adverse = bar.open if reason in {"gap", "rotation"} else max(bar.low, stop)
            heat += adverse * sell * position.quantity
            if reference is None:
                trailing_close = max(trailing_close, bar.close)
                equity += bar.close * sell * position.quantity
            else:
                proceeds = reference * sell * position.quantity
                trades.append({"symbol": position.ticker, "entry_day": position.entry_day, "exit_day": day,
                               "quantity": position.quantity, "net_pnl": proceeds - position.cost,
                               "entry_price": position.opening * (1 + cost),
                               "exit_price": reference * (1 - cost), "reason": reason})
                cash += proceeds
                equity = cash
                position = None
        drawdown = max(0.0, (peak - min(heat, equity)) / peak)
        if halt is None and drawdown >= HALT_DRAWDOWN:
            halt = day
        peak = max(peak, equity)
        curve.append([day, equity, drawdown * 100])
    outcomes = [trade["net_pnl"] for trade in trades]
    gross_up = sum(value for value in outcomes if value > 0)
    gross_down = -sum(value for value in outcomes if value < 0)
    factor = gross_up / gross_down if gross_down else None
    drawdown = max((point[2] for point in curve), default=0)
    profit = equity - capital
    return {"equity": curve, "trades": trades, "metrics": {
        "net_pnl": profit, "return_pct": 100 * profit / capital, "drawdown_pct": drawdown,
        "profit_factor": factor, "no_losses": gross_up > 0 and not gross_down,
        "trade_count": len(trades), "exposed_days": exposed,
        "concentration_pct": 100 * max(outcomes) / profit if profit > 0 and outcomes else None,
        "halt": halt, "drawdown_points": drawdown_score(drawdown) if trades else None,
        "pf_points": profit_factor_score(factor, gross_up > 0 and not gross_down) if trades else None,
    }}
