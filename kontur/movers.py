"""Price movers between the two most recent observed market sessions."""

COMPANIES = {
    "AFKS": "АФК Система", "AFLT": "Аэрофлот", "ALRS": "АЛРОСА", "CHMF": "Северсталь",
    "GAZP": "Газпром", "GMKN": "Норникель", "LKOH": "Лукойл", "MGNT": "Магнит",
    "MOEX": "Московская биржа", "MTLR": "Мечел", "MTLRP": "Мечел · ап", "NLMK": "НЛМК",
    "NVTK": "НОВАТЭК", "OZON": "Ozon", "PIKK": "ПИК", "PLZL": "Полюс", "RENI": "Ренессанс",
    "ROSN": "Роснефть", "RUAL": "РУСАЛ", "SBER": "Сбербанк", "SBERP": "Сбербанк · ап",
    "SMLT": "Самолёт", "SVCB": "Совкомбанк", "T": "Т-Технологии", "TATN": "Татнефть",
    "TRNFP": "Транснефть · ап", "VKCO": "VK", "VTBR": "ВТБ", "X5": "X5", "YDEX": "Яндекс",
}


def movers(bars):
    days = sorted({bar.day for bar in bars})
    if len(days) < 2:
        return None
    prev, last = days[-2:]
    by_day = {day: {b.symbol: b.close for b in bars if b.day == day} for day in (prev, last)}
    a, b = by_day[prev], by_day[last]
    ranked = sorted(({"symbol": key, "name": COMPANIES.get(key, key), "close": b[key],
                      "change_pct": (b[key] / a[key] - 1) * 100} for key in sorted(a.keys() & b.keys())),
                    key=lambda item: item["change_pct"])
    return {"as_of": last, "previous": prev, "count": len(ranked),
            "gainers": [item for item in reversed(ranked) if item["change_pct"] > 0][:5],
            "losers": [item for item in ranked if item["change_pct"] < 0][:5]}
