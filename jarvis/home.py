"""Умный дом через Home Assistant (REST API, долгоживущий токен).

В config.yaml:
    ha_url: "http://homeassistant.local:8123"
    ha_token: "eyJ..."   # Профиль -> Безопасность -> Долгосрочные токены доступа
Яндекс Алису, Tuya, Xiaomi и прочее проще всего завести в Home Assistant интеграциями — тогда
Джарвис рулит всем через одно место.
"""

from __future__ import annotations

import difflib
import re
import time

import requests

DOMAINS = ("light", "switch", "fan", "cover", "climate", "media_player", "scene", "script", "input_boolean",
           "vacuum", "lock", "humidifier")
ACTIONS = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}


class HomeError(Exception):
    pass


class Home:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        if re.match(r"https?://(localhost|127\.|192\.168\.|10\.|homeassistant\.local)", self.url):
            self.session.trust_env = False  # локальная сеть — мимо прокси/VPN
        self._cache: tuple[float, list[dict]] | None = None

    def _req(self, method: str, path: str, **kw):
        try:
            r = self.session.request(method, self.url + path, timeout=8, **kw)
        except requests.RequestException as e:
            raise HomeError(f"Home Assistant недоступен: {e}") from e
        if r.status_code == 401:
            raise HomeError("Home Assistant не пустил: проверь ha_token")
        if r.status_code >= 400:
            raise HomeError(f"Home Assistant ответил {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    def devices(self) -> list[dict]:
        out = []
        for s in self._req("GET", "/api/states") or []:
            domain = s["entity_id"].split(".")[0]
            if domain in DOMAINS:
                out.append({"id": s["entity_id"], "name": s.get("attributes", {}).get("friendly_name") or s["entity_id"],
                            "state": s.get("state"), "domain": domain})
        return out

    def find(self, name: str) -> dict | None:
        if self._cache and time.time() - self._cache[0] < 60:
            devices = self._cache[1]
        else:
            devices = self.devices()
            self._cache = (time.time(), devices)
        q = name.lower().replace("ё", "е").strip()
        for d in devices:
            if q in (d["id"].lower(), d["name"].lower()):
                return d
        stems = [w[:-1] if len(w) > 4 else w for w in q.split()]  # «люстру» -> «люстр»
        best, score = None, 0.0
        for d in devices:
            n = d["name"].lower().replace("ё", "е")
            s = 0.9 if all(w in n for w in stems) else difflib.SequenceMatcher(None, q, n).ratio()
            if s > score:
                best, score = d, s
        return best if score >= 0.6 else None

    def act(self, action: str, name: str) -> str:
        device = self.find(name)
        if not device:
            raise HomeError(f"Не нашёл устройство «{name}»")
        domain = device["domain"]
        service = "turn_on" if domain in ("scene", "script") else ACTIONS.get(action, action)
        if domain == "cover":
            service = {"turn_on": "open_cover", "turn_off": "close_cover", "toggle": "toggle"}[service]
        if domain == "lock":
            service = {"turn_on": "unlock", "turn_off": "lock", "toggle": "lock"}[service]
        self._req("POST", f"/api/services/{domain}/{service}", json={"entity_id": device["id"]})
        word = {"turn_on": "включил", "turn_off": "выключил", "toggle": "переключил", "open_cover": "открыл",
                "close_cover": "закрыл", "lock": "закрыл", "unlock": "открыл"}.get(service, "сделал")
        return f"{device['name']}: {word}"
