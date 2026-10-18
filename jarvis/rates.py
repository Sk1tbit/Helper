"""Курсы валют ЦБ РФ (cbr-xml-daily.ru), погода (wttr.in) и состояние ПК — с кешем, для голоса и виджетов."""

from __future__ import annotations

import time

import requests

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
NAMES = {"USD": "доллар", "EUR": "евро", "CNY": "юань", "KZT": "тенге", "GBP": "фунт", "TRY": "лира", "BYN": "белорусский рубль"}
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    if value is not None:
        _cache[key] = (time.time(), value)
    return value


def rates() -> dict[str, dict] | None:
    """{"USD": {"value": 81.42, "change": 0.31, "nominal": 1}, ...} или None, если нет связи."""
    def load():
        try:
            data = requests.get(CBR_URL, timeout=8).json()["Valute"]
        except (requests.RequestException, ValueError, KeyError):
            return None
        out = {}
        for code in NAMES:
            v = data.get(code)
            if v:
                nominal = v.get("Nominal", 1) or 1
                out[code] = {"value": round(v["Value"] / nominal, 4), "change": round((v["Value"] - v["Previous"]) / nominal, 4),
                             "nominal": 1}
        return out
    return _cached("rates", 600, load)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def say_rate(code: str | None = None) -> str | None:
    r = rates()
    if not r:
        return None
    codes = [code] if code else ["USD", "EUR", "CNY"]
    parts = []
    for c in codes:
        if c in r:
            v, ch = r[c]["value"], r[c]["change"]
            trend = "растёт" if ch > 0.005 else ("падает" if ch < -0.005 else "стоит")
            rub, kop = int(v), round((v - int(v)) * 100)
            parts.append(f"{NAMES[c]} {rub} {plural(rub, 'рубль', 'рубля', 'рублей')} "
                         f"{kop} {plural(kop, 'копейка', 'копейки', 'копеек')}, {trend}")
    return ("Курс ЦБ: " + "; ".join(parts) + ".") if parts else None


def weather_data(city: str) -> dict | None:
    """{"city", "temp", "feels", "desc", "wind"} или None. Город можно в любом падеже."""
    city = city.strip()
    words = city.split()
    last = words[-1] if words else city
    variants = [city, "-".join(words)]
    if last.endswith("е") and len(last) > 3:  # «в москве» -> «москва», «в санкт петербурге» -> «санкт-петербург»
        for ending in (last[:-1] + "а", last[:-1]):
            variants += [" ".join(words[:-1] + [ending]), "-".join(words[:-1] + [ending])]

    def load():
        for name in dict.fromkeys(v for v in variants if v):
            try:
                data = requests.get(f"https://wttr.in/{name}", params={"format": "j1", "lang": "ru"}, timeout=8).json()
                cur = data["current_condition"][0]
            except (requests.RequestException, ValueError, KeyError, IndexError):
                continue
            desc = (cur.get("lang_ru") or cur.get("weatherDesc") or [{"value": ""}])[0]["value"].strip().lower()
            return {"city": name[:1].upper() + name[1:], "temp": int(cur["temp_C"]), "feels": int(cur["FeelsLikeC"]),
                    "desc": desc, "wind": round(int(cur.get("windspeedKmph", 0)) / 3.6)}
        return None
    return _cached(f"weather:{city.lower()}", 900, load)


def system_data() -> dict:
    import psutil

    mem = psutil.virtual_memory()
    out = {"cpu": psutil.cpu_percent(interval=None), "ram": mem.percent,
           "ram_used": round(mem.used / 2**30, 1), "ram_total": round(mem.total / 2**30, 1)}
    try:
        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        battery = None
    if battery:
        out["battery"] = round(battery.percent)
    return out
