"""Daily bar execution simulator for a long-only, flat-overnight strategy."""

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from math import floor, isfinite

from .contest import drawdown_score, profit_factor_score


@dataclass(frozen=True)
class RiskConfig:
    initial_capital: float = 35_000
    commission_bps: float = 5
    slippage_bps: float = 5
    min_signal_bps: float = 25
    stop_pct: float = 1.0
    take_profit_pct: float = 1.6
    max_position_pct: float = 25.0
    max_open_positions: int = 2
    max_daily_loss_pct: float = 0.6
    max_drawdown_pct: float = 4.0
    lot_sizes: dict[str, int] = field(default_factory=lambda: {
        "SBER": 10, "SBERP": 10, "GAZP": 10, "LKOH": 1,
    })

    def __post_init__(self):
        if not isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("Начальный капитал должен быть положительным.")
        for value in (self.commission_bps, self.slippage_bps, self.min_signal_bps):
            if not isfinite(value) or not 0 <= value < 10_000:
                raise ValueError("Издержки и порог сигнала задаются в базисных пунктах.")
        for value in (self.stop_pct, self.take_profit_pct, self.max_position_pct,
                      self.max_daily_loss_pct, self.max_drawdown_pct):
            if not isfinite(value) or not 0 < value <= 100:
                raise ValueError("Лимиты риска задаются в процентах от 0 до 100.")
        if self.stop_pct == 100:
            raise ValueError("Стоп должен быть меньше 100%.")
        if type(self.max_open_positions) is not int or self.max_open_positions < 1:
            raise ValueError("Лимит позиций должен быть положительным целым числом.")
        if any(type(size) is not int or size < 1 for size in self.lot_sizes.values()):
            raise ValueError("Размер лота должен быть положительным целым числом.")


def _price_out(reference_price, slippage_rate):
    return reference_price * (1 - slippage_rate)


def resolve_exit(bar, stop, target):
    if bar.low <= stop:
        return stop, "stop", None
    if bar.high >= target:
        return target, "take_profit", None
    return bar.close, "close", bar.close_at.isoformat()


def _encode_evidence(item):
    return {
        key: value.isoformat() if isinstance(value, (datetime, date)) else value
        for key, value in asdict(item).items()
    }


def _metrics(config, equity, trades, risk_halt_date):
    outcomes = [trade["net_pnl"] for trade in trades]
    net_pnl = equity[-1]["equity"] - config.initial_capital if equity else 0.0
    gains = sum(value for value in outcomes if value > 0)
    losses = -sum(value for value in outcomes if value < 0)
    profit_factor = gains / losses if losses else None
    no_losses = not losses and gains > 0
    max_drawdown = max((point["drawdown_pct"] for point in equity), default=0.0)
    return {
        "initial_capital": config.initial_capital,
        "net_pnl": net_pnl,
        "return_pct": net_pnl * 100 / config.initial_capital,
        "max_drawdown_pct": max_drawdown,
        "profit_factor": profit_factor,
        "no_losses": no_losses,
        "expectancy": sum(outcomes) / len(outcomes) if outcomes else None,
        "trade_count": len(trades),
        "trade_days": len({trade["trade_day"] for trade in trades}),
        "concentration_pct": max(outcomes) * 100 / net_pnl if net_pnl > 0 and outcomes else None,
        "total_commission": sum(trade["commission"] for trade in trades),
        "total_slippage": sum(trade["slippage"] for trade in trades),
        "risk_halt_date": risk_halt_date,
        "score_components": {
            "drawdown": drawdown_score(max_drawdown) if outcomes else None,
            "profit_factor": profit_factor_score(profit_factor, no_losses) if outcomes else None,
            "real_repeatability": None,
            "relative_return": None,
        },
    }


def simulate(bars, predictions, score_field, config=None):
    """Replay ranked prior-close signals with one round trip per ticker/session.

    Opens and closes assume enough liquidity at the reference plus fixed adverse
    slippage. Intraday fills follow OHLC touches, with stops first on ambiguous
    bars. The equity heat is an adverse simultaneous mark relative to prior
    closing peaks. Daily OHLC cannot determine the true intraday equity path.
    """
    if score_field not in {"event_return", "market_return", "momentum_return"}:
        raise ValueError("Неизвестное поле прогноза.")
    config = config if config is not None else RiskConfig()
    commission_rate = config.commission_bps / 10_000
    slippage_rate = config.slippage_bps / 10_000
    min_score = max(config.min_signal_bps, 2 * (config.commission_bps + config.slippage_bps)) / 10_000

    bar_by_session = {(bar.symbol, bar.day): bar for bar in bars}
    ticker_sessions = defaultdict(list)
    for ticker, day in bar_by_session:
        ticker_sessions[ticker].append(day)
    next_session = {}
    for ticker, days in ticker_sessions.items():
        days.sort()
        next_session.update({(ticker, day): following for day, following in zip(days, days[1:])})

    by_day = defaultdict(list)
    seen = set()
    for prediction in predictions:
        key = (prediction.ticker, prediction.trade_day)
        bar = bar_by_session.get(key)
        if bar is None or next_session.get((prediction.ticker, prediction.signal_day)) != prediction.trade_day:
            raise ValueError(f"Нет следующей сессии для прогноза {prediction.ticker} {prediction.signal_day}.")
        if prediction.signal_at.tzinfo is None or prediction.signal_at >= bar.open_at:
            raise ValueError(f"Прогноз отсутствовал до открытия {prediction.ticker} {bar.day}.")
        signal_bar = bar_by_session[(prediction.ticker, prediction.signal_day)]
        if prediction.signal_at < signal_bar.close_at:
            raise ValueError(f"Дневная свеча ещё не закрылась для {prediction.ticker} {prediction.signal_day}.")
        if key in seen:
            raise ValueError(f"Дублированный прогноз {prediction.ticker} {prediction.trade_day}.")
        seen.add(key)
        score = getattr(prediction, score_field)
        if not isfinite(score):
            raise ValueError(f"Некорректный прогноз {prediction.ticker} {prediction.trade_day}.")
        by_day[prediction.trade_day].append((score, prediction, bar))

    starting_cash = config.initial_capital
    peak = starting_cash
    equity = []
    trades = []
    risk_halt_date = None
    for day, candidates in sorted(by_day.items()):
        unallocated_cash = starting_cash
        daily_risk = starting_cash * config.max_daily_loss_pct / 100
        max_position_cash = starting_cash * config.max_position_pct / 100
        closing_proceeds = 0.0
        conservative_proceeds = 0.0
        opened = 0

        for score, prediction, bar in sorted(candidates, key=lambda item: (-item[0], item[1].ticker)):
            if risk_halt_date or opened >= config.max_open_positions or score <= min_score:
                break
            ticker = prediction.ticker
            lot_size = config.lot_sizes.get(ticker, 1)
            entry_price = bar.open * (1 + slippage_rate)
            stop = bar.open * (1 - config.stop_pct / 100)
            target = bar.open * (1 + config.take_profit_pct / 100)
            unit_cost = entry_price * (1 + commission_rate)
            stop_proceeds = _price_out(stop, slippage_rate) * (1 - commission_rate)
            unit_risk = unit_cost - stop_proceeds
            lots = floor(min(unallocated_cash / unit_cost, max_position_cash / unit_cost,
                             daily_risk / unit_risk) / lot_size)
            if lots <= 0:
                continue
            quantity = lots * lot_size
            risk_reserved = quantity * unit_risk
            entry_fee = entry_price * quantity * commission_rate
            entry_cost = entry_price * quantity + entry_fee
            unallocated_cash -= entry_cost
            daily_risk -= risk_reserved
            opened += 1

            reference_exit, reason, exit_at = resolve_exit(bar, stop, target)
            exit_price = _price_out(reference_exit, slippage_rate)
            exit_fee = exit_price * quantity * commission_rate
            proceeds = exit_price * quantity - exit_fee
            heat_reference = max(bar.low, stop)
            conservative_proceeds += _price_out(heat_reference, slippage_rate) * quantity * (1 - commission_rate)
            closing_proceeds += proceeds
            trades.append({
                "id": f"{ticker}-{day.isoformat()}",
                "ticker": ticker,
                "signal_day": prediction.signal_day.isoformat(),
                "trade_day": day.isoformat(),
                "signal_at": prediction.signal_at.isoformat(),
                "entry_at": bar.open_at.isoformat(),
                "exit_at": exit_at,
                "entry_reference_price": bar.open,
                "exit_reference_price": reference_exit,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_price": stop,
                "target_price": target,
                "quantity": quantity,
                "lots": lots,
                "commission": entry_fee + exit_fee,
                "slippage": ((entry_price - bar.open) + (reference_exit - exit_price)) * quantity,
                "net_pnl": proceeds - entry_cost,
                "reason": reason,
                "prediction": score,
                "evidence": [_encode_evidence(item) for item in prediction.evidence],
                "fold": prediction.fold,
                "entry_fee": entry_fee,
                "exit_fee": exit_fee,
            })

        closing_equity = unallocated_cash + closing_proceeds
        conservative_equity = min(closing_equity, unallocated_cash + conservative_proceeds)
        drawdown = max(0.0, (peak - conservative_equity) * 100 / peak)
        if risk_halt_date is None and drawdown >= config.max_drawdown_pct:
            risk_halt_date = day.isoformat()
        equity.append({
            "date": day.isoformat(),
            "equity": closing_equity,
            "drawdown_pct": drawdown,
            "conservative_equity": conservative_equity,
        })
        peak = max(peak, closing_equity)
        starting_cash = closing_equity

    return {"metrics": _metrics(config, equity, trades, risk_halt_date), "equity": equity,
            "trades": trades, "risk": asdict(config)}
