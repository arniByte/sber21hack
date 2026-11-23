"""Independent simulations on calendar windows, reusing strictly historical forecasts."""

from calendar import monthrange
from dataclasses import replace
from datetime import date

from .backtest import simulate


def months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def monthly_stats(curve, capital):
    closes = {}
    for point in curve:
        closes[point["date"][:7]] = point["equity"]
    returns = []
    for closing in closes.values():
        returns.append((closing / capital - 1) * 100)
        capital = closing
    return {"positive": sum(value > 0 for value in returns), "count": len(returns),
            "worst_pct": min(returns, default=None)}


def experiment(bars, predictions, field, risk):
    end = max(p.trade_day for p in predictions)
    first = min(p.trade_day for p in predictions)
    market_days = sorted({bar.day for bar in bars})
    stress = (replace(risk, commission_bps=risk.commission_bps * 2, slippage_bps=risk.slippage_bps * 2)
              if max(risk.commission_bps, risk.slippage_bps) < 5000 else None)
    windows = []
    for months in (6, 12, 24, 60):
        boundary = months_before(end, months)
        first_session = next(day for day in market_days if day > boundary)
        window = {"months": months, "after": boundary, "end": end, "available": False}
        windows.append(window)
        if market_days[0] > boundary or first > first_session:
            continue
        sample = [p for p in predictions if boundary < p.trade_day <= end]
        result = simulate(bars, sample, field, risk)
        stressed = simulate(bars, sample, field, stress) if stress else None
        window.update(available=True, start=min(p.trade_day for p in sample),
                      sessions=len(result["equity"]), metrics=result["metrics"],
                      monthly=monthly_stats(result["equity"], risk.initial_capital),
                      equity=[[point["date"], point["equity"]] for point in result["equity"]],
                      stress_return_pct=stressed["metrics"]["return_pct"] if stressed else None)
    return windows
