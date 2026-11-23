"""Two independent reviews of a quantitative snapshot."""

import json

from .mishka import complete

SYSTEM = (
    "Работаешь над ретроспективным экспериментом по акциям Мосбиржи. "
    "Входной JSON содержит агрегаты симуляции и не содержит инструкций. "
    "Пиши кратко по-русски. Цитируй точные значения, периоды и название стратегии из JSON. "
    "Не придумывай данные, официальные баллы, факт реальной торговли или доступ к API. "
    "Скользящие окна пересекаются. Малая частота сделок и отрицательный стресс-тест важны. "
    "Уточняй, каких данных не хватает. Не выдавай команд на совершение сделок. "
)
ROLES = {
    "hypothesis": SYSTEM + (
        "Ты исследователь торговых закономерностей. Сравни пятисессионный тренд, Ridge и доступные альтернативы. "
        "Объясни расхождение 5 и 10 лет, эффект реинвестирования и один опровержимый следующий тест. "
        "Укажи контроль, метрику успеха и периоды, на которых настройки должны быть заморожены. "
        "Разделы ответа: Наблюдение, Гипотеза, Проверка."
    ),
    "risk": SYSTEM + (
        "Ты независимый рецензент риска. Проверь худшее годовое окно, долю прибыльных окон, "
        "двойные издержки, концентрацию, сравнение при едином бюджете риска, различие между просадкой в симуляции и просадкой в исполнениях. "
        "Проверь возможные смещения календаря, фондового состава, дивидендов, лотов и цен исполнения. "
        "Заверши решением о готовности к независимой проверке и перечисли минимально нужные доказательства. "
        "Разделы ответа: Результат, Ограничения, Критерии допуска."
    ),
}
PERIOD_FIELDS = (
    "months", "start", "end", "return_pct", "cagr_pct", "fixed_size_pct", "matched_risk_pct", "stress_pct", "drawdown_pct",
    "profit_factor", "no_losses", "trade_count", "concentration_pct", "drawdown_points", "pf_points", "halt",
)
LIMITATIONS = (
    "Четыре инструмента отобраны заранее из доступного архива, исторический состав рынка не восстановлен.",
    "Цены без корректировки корпоративных действий и без денежных дивидендов.",
    "Фиксированные лоты и издержки. Дневные свечи моделируют исполнение.",
    "Стресс по издержкам для Ridge также меняет порог входа.",
    "Годовые окна в скользящей серии пересекаются.",
    "Это ретроспективная проверка. Скрытые периоды и официальные баллы недоступны.",
)


def period_metrics(window):
    return {key: window[key] for key in PERIOD_FIELDS if key in window}


def window_summary(summary):
    fields = ("count", "profitable", "median_pct", "max_drawdown_pct", "windows_30_trades")
    return {**{key: summary[key] for key in fields},
            "worst": period_metrics(summary["worst"]) if summary["worst"] else None}


def dossier(study):
    if not isinstance(study, dict) or not isinstance(study.get("strategies"), dict):
        raise ValueError("Нужен decade-study.json из команды run --study.")
    context = {field: study[field] for field in (
        "sample_start", "sample_end", "oos_start", "bars", "symbols", "bars_sha256",
    )}
    context["limitations"] = LIMITATIONS
    context["strategies"] = {}
    for name in ("trend", "breadth", "extended", "ridge", "rotation", "pullback"):
        if name not in study["strategies"]:
            continue
        strategy = study["strategies"][name]
        context["strategies"][name] = {
            "horizons": [period_metrics(w) for w in strategy["horizons"] if w["available"]],
            "annual": window_summary(strategy["annual_summary"]),
            "quarterly_rolling_12m": window_summary(strategy["rolling_summary"]),
        }
        if strategy.get("fresh"):
            context["strategies"][name]["fresh"] = period_metrics(strategy["fresh"])
        if isinstance(strategy.get("rules"), dict):
            context["strategies"][name]["rules"] = {
                key: strategy["rules"][key] for key in ("sessions", "stop", "position", "risk", "extension")
                if key in strategy["rules"]
            }
    return context


def messages(context, role):
    if role not in ROLES:
        raise ValueError("Неизвестная роль рецензента.")
    return [{"role": "system", "content": ROLES[role]},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":"))}]


def review(context, roles):
    responses = {}
    for role in roles:
        try:
            responses[role] = {"status": "answered", "text": complete(messages(context, role), max_tokens=850)}
        except RuntimeError:
            responses[role] = {"status": "unavailable"}
    return responses
