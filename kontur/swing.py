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
POSITION = 0.15
RISK = 0.0045
SOFT_DRAWDOWN = 0.02
HALT_DRAWDOWN = 0.08
TRAIN_SESSIONS = 252
REFIT_SESSIONS = 63
FEATURES = ("return_5", "return_20", "return_60", "distance_sma_60", "volatility_20",
            "range_20", "breadth_60")
STRATEGIES = {"trend": "Тренд · 5 сессий", "ridge": "Ridge · 5 сессий"}


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
    results = {key: {} for key in STRATEGIES}
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
    return results, folds


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: int
    opening: float
    cost: float
    entry_day: date
    entry_index: int


def replay(days, book, signals, lots, *, forecast_gate=False, reinvest=True, cost_bps=5, capital=CAPITAL):
    cost = cost_bps / 10_000
    buy = (1 + cost) ** 2
    sell = (1 - cost) ** 2
    gate = buy / sell - 1 + .001
    cash = equity = peak = capital
    position = None
    halt = None
    curve, trades = [], []
    exposed = 0
    for index, day in enumerate(days):
        if position is None and halt is None:
            budget = equity if reinvest else capital
            scale = .5 if equity <= peak * (1 - SOFT_DRAWDOWN) else 1
            candidates = sorted(signals.get(day, ()), key=lambda signal: (-signal.score, signal.ticker))
            for signal in candidates:
                if forecast_gate and signal.score <= gate:
                    break
                opening = book[day][signal.ticker].open
                unit_cost = opening * buy
                unit_risk = unit_cost - opening * (1 - STOP) * sell
                lot = lots[signal.ticker]
                quantity = floor(min(cash / unit_cost, budget * scale * POSITION / unit_cost,
                                     budget * scale * RISK / unit_risk) / lot) * lot
                if quantity:
                    position = Position(signal.ticker, quantity, opening, unit_cost * quantity, day, index)
                    cash -= position.cost
                    break
        heat = equity = cash
        if position:
            exposed += 1
            bar = book[day][position.ticker]
            stop = position.opening * (1 - STOP)
            timed = index - position.entry_index + 1 >= HOLD or index == len(days) - 1
            reference, reason = exit_price(bar, stop, timed)
            # Once the stop has fired, the position no longer bears later bar lows.
            adverse = bar.open if reason == "gap" else max(bar.low, stop)
            heat += adverse * sell * position.quantity
            if reference is None:
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
