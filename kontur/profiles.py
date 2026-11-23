"""Versioned, portable strategy settings. Reports and credentials are never part of a preset."""

from dataclasses import asdict
import json
from pathlib import Path
import re

from .backtest import RiskConfig

STRATEGIES = {
    "hybrid": ("События + рынок", "event_return"),
    "market": ("Только рынок", "market_return"),
    "momentum": ("Импульс цены", "momentum_return"),
}
RISK_KEYS = set(asdict(RiskConfig())) - {"lot_sizes", "initial_capital"}
TOURNAMENT_RISK = {"max_position_pct": 15, "max_daily_loss_pct": 0.3, "max_drawdown_pct": 2}
PROFILES = {
    "baseline": ("Базовый", "hybrid", "close", {}),
    "contest": ("Турнирный", "market", "close", TOURNAMENT_RISK),
    "payoff": ("По правилам выхода", "market", "exit", TOURNAMENT_RISK),
}


def preset(profile, agent, risk, objective="close"):
    settings = asdict(risk)
    return {"version": 1, "profile": profile, "agent": agent, "objective": objective,
            "lots": dict(risk.lot_sizes),
            "risk": {key: settings[key] for key in sorted(RISK_KEYS)}}


def configuration(name, path, lots, overrides=None):
    lots = dict(lots)
    label, agent, objective, changes = PROFILES[name]
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"version", "profile", "agent", "risk", "lots", "objective"}
                or type(data["version"]) is not int or data["version"] != 1):
            raise ValueError("Нужен пресет Контур версии 1.")
        objective = data["objective"]
        if objective not in ("exit", "close"):
            raise ValueError("Неизвестная цель модели.")
        sizes = data["lots"]
        if not isinstance(sizes, dict) or any(not re.fullmatch(r"[A-Z0-9_.-]{1,24}", key)
                or type(size) is not int or size < 1 for key, size in sizes.items()):
            raise ValueError("Некорректные лоты в пресете.")
        lots.update(sizes)
        label, agent, changes = data["profile"], data["agent"], data["risk"]
        if not isinstance(label, str) or not label.strip() or len(label) > 60:
            raise ValueError("Название профиля должно содержать от 1 до 60 символов.")
        if not isinstance(agent, str) or agent not in STRATEGIES:
            raise ValueError("Неизвестная стратегия в пресете.")
        if (not isinstance(changes, dict) or set(changes) != RISK_KEYS
                or any(type(value) not in (float, int) for value in changes.values())):
            raise ValueError("Неверные параметры риска в пресете.")
    lots.update(overrides or {})
    risk = RiskConfig(lot_sizes=lots, **changes)
    return preset(label, agent, risk, objective), risk
