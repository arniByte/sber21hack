"""Compare independent calendar periods and alternative position sizing."""

from statistics import median

from .horizons import months_before
from .swing import STRATEGIES, forecasts, replay, shared_calendar


def summarize(windows):
    returns = [w["return_pct"] for w in windows]
    qualified = [w for w in windows if w["trade_count"] >= 30]
    return {"count": len(windows), "profitable": sum(r > 0 for r in returns),
            "worst": min(windows, key=lambda w: w["return_pct"], default=None),
            "median_pct": median(returns) if returns else None,
            "max_drawdown_pct": max((w["drawdown_pct"] for w in windows), default=None),
            "windows_30_trades": len(qualified)}


def decade_study(bars, lots, holdout_after=None):
    days, symbols, book = shared_calendar(bars)
    signals, folds = forecasts(days, symbols, book)
    end = days[-1]
    first_signal = min(signals["ridge"])
    if holdout_after is not None and not first_signal <= holdout_after < end:
        raise ValueError("Граница дополнительного окна должна лежать внутри периода проверки.")

    def window(months, offset, strategy, details=False, after=None):
        stop = months_before(end, offset)
        start = after or months_before(stop, months)
        days_in_window = [day for day in days if start < day <= stop]
        if not days_in_window or days[0] > start or days_in_window[0] < first_signal:
            return None
        selected = signals[strategy]
        params = {"forecast_gate": strategy == "ridge"}
        result = replay(days_in_window, book, selected, lots, **params)
        stats = {"start": days_in_window[0], "end": days_in_window[-1], "months": months,
                 "sessions": len(days_in_window), **result["metrics"]}
        if months and months >= 12:
            stats["cagr_pct"] = 100 * ((1 + stats["return_pct"] / 100) ** (12 / months) - 1)
        if details:
            fixed = replay(days_in_window, book, selected, lots, reinvest=False, **params)
            stressed = replay(days_in_window, book, selected, lots, cost_bps=10, **params)
            stats.update(equity=result["equity"], fixed_size_pct=fixed["metrics"]["return_pct"],
                         stress_pct=stressed["metrics"]["return_pct"])
        return stats, result["trades"]

    report = {"sample_start": days[0], "sample_end": end, "oos_start": first_signal, "symbols": symbols,
              "lots": lots, "bars": len(bars), "folds": folds, "strategies": {}}
    if holdout_after:
        report["holdout_after"] = holdout_after
    trade_log = []
    for key, label in STRATEGIES.items():
        periods = []
        for months in (6, 12, 24, 60, 120):
            result = window(months, 0, key, details=True)
            periods.append({"months": months, "available": bool(result), **(result[0] if result else {})})
            if result and months == 120:
                trade_log.extend({"strategy": key, "period": "decade", **trade} for trade in result[1])
        annual = [result[0] for offset in range(0, 120, 12)
                  if (result := window(12, offset, key))]
        rolling = [result[0] for offset in range(0, 109, 3)
                   if (result := window(12, offset, key))]
        report["strategies"][key] = {"label": label, "horizons": periods, "annual": annual,
                                    "annual_summary": summarize(annual), "rolling": rolling,
                                    "rolling_summary": summarize(rolling)}
        if holdout_after:
            fresh, trades = window(None, 0, key, details=True, after=holdout_after)
            report["strategies"][key]["fresh"] = fresh
            trade_log.extend({"strategy": key, "period": "fresh", **trade} for trade in trades)
    return report, trade_log
