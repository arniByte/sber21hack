"""Stitch regular-session archives against verified overlapping ISS closes."""

from dataclasses import replace
from statistics import median


def extend_with_iss(archive, official):
    historical = {(bar.symbol, bar.day): bar for bar in archive}
    observed = {(bar.symbol, bar.day): bar for bar in official}
    symbols = {bar.symbol for bar in archive}
    if not symbols or symbols != {bar.symbol for bar in official}:
        raise ValueError("Архив и выгрузка ISS должны содержать одинаковый набор тикеров.")
    cutoff = max(bar.day for bar in archive)
    common = historical.keys() & observed.keys()
    checks, factors = {}, {}
    for symbol in sorted(symbols):
        dates = sorted(day for ticker, day in common if ticker == symbol)[-20:]
        if len(dates) < 5 or max(dates) != cutoff:
            raise ValueError(f"По {symbol} нужно минимум пять пересечений, включая последнюю дату архива.")
        pairs = [(historical[symbol, day], observed[symbol, day]) for day in dates]
        close_delta = max(abs(left.close / right.close - 1) for left, right in pairs)
        if close_delta > .001:
            raise ValueError(f"Цены закрытия {symbol} расходятся больше чем на 0,1%.")
        ratios = [left.volume / right.volume for left, right in pairs if left.volume > 0 and right.volume > 0]
        if len(ratios) < 5:
            raise ValueError(f"Недостаточно наблюдений объёма {symbol}.")
        ratio = median(ratios)
        factor = min((.01, .1, 1, 10, 100), key=lambda unit: abs(ratio / unit - 1))
        if abs(ratio / factor - 1) > .1:
            raise ValueError(f"Единицы объёма {symbol} расходятся.")
        factors[symbol] = factor
        checks[symbol] = {"overlap_sessions": len(dates), "volume_scale": factor,
                          "max_close_gap_pct": 100 * close_delta,
                          "max_open_gap_pct": 100 * max(abs(right.open / left.open - 1) for left, right in pairs)}
    fresh = [replace(bar, volume=bar.volume * factors[bar.symbol]) for bar in official if bar.day > cutoff]
    days = {bar.day for bar in fresh}
    if not days or len(fresh) != len(days) * len(symbols):
        raise ValueError("Нужны новые свечи всех инструментов на каждую обновляемую сессию.")
    return [*archive, *fresh], {"cutoff": cutoff, "end": max(days), "new_sessions": len(days), "checks": checks}
