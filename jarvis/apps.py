"""Поиск и запуск программ на Windows по человеческому названию.

Пользователь говорит «открой яндекс», а программа называется browser.exe и лежит
в AppData. Поэтому ищем так же, как поиск в меню «Пуск»:
  1. пути и протоколы (C:\\..., steam://, ms-settings:);
  2. программы из PATH и реестра App Paths (chrome, winword...);
  3. ярлыки из меню «Пуск» и с рабочего стола — по похожести названия,
     с транслитерацией (яндекс ↔ yandex);
  4. приложения из Microsoft Store (Get-StartApps).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

MATCH_THRESHOLD = 0.62

_RU_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_SKIP_WORDS = ("uninstall", "удалить", "удаление", "readme", "help", "справка", "website", "сайт")


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"[^\w]+", " ", text).strip()


def _translit(text: str) -> str:
    out = "".join(_RU_LAT.get(ch, ch) for ch in text)
    return out.replace("ks", "x")  # яндекс -> yandex


# Как программы называют вслух -> как они называются на самом деле.
SPOKEN_NAMES = {
    "яндекс": "яндекс браузер", "yandex": "яндекс браузер", "стим": "steam", "телега": "telegram", "телеграм": "telegram", "дискорд": "discord", "дс": "discord",
    "хром": "chrome", "гугл хром": "google chrome", "эдж": "edge", "ворд": "word", "эксель": "excel",
    "повер поинт": "powerpoint", "павер поинт": "powerpoint", "фотошоп": "photoshop",
    "вс код": "visual studio code", "вскод": "visual studio code", "вижуал студио": "visual studio",
    "обс": "obs studio", "зум": "zoom", "спотифай": "spotify", "ютуб": "youtube", "вк": "vk",
    "вконтакте": "vk", "ватсап": "whatsapp", "вотсап": "whatsapp", "эпик": "epic games",
    "эпик геймс": "epic games launcher", "блендер": "blender", "майнкрафт": "minecraft",
    "роблокс": "roblox", "роблокс студио": "roblox studio", "торрент": "utorrent",
    "лм студио": "lm studio", "кап кат": "capcut", "капкат": "capcut", "винрар": "winrar",
    "ноутпад": "notepad++", "аудасити": "audacity", "тим спик": "teamspeak", "кс": "counter-strike",
}


def _stem(text: str) -> str:
    """Грубо отрезает русские окончания: «телегу» -> «телег», «музыку» -> «музык»."""
    return " ".join(re.sub(r"[аяуюеиыйоь]+$", "", w) if len(w) > 4 and re.search(r"[а-я]", w) else w
                    for w in text.split())


_SPOKEN_STEMS = {_stem(k): v for k, v in SPOKEN_NAMES.items()}


def _variants(text: str) -> set[str]:
    n = _norm(text)
    stem = _stem(n)
    out = {n, _translit(n), stem, _translit(stem)}
    # Разговорное название — только если сказано целиком: «телегу» -> Telegram, но «яндекс музыку»
    # не должно превращаться в «яндекс браузер музыку».
    real = _SPOKEN_STEMS.get(stem)
    if real:
        out.add(real)
    return out


def score(query: str, candidate: str) -> float:
    best = 0.0
    for q in _variants(query):
        for c in _variants(candidate):
            if not q or not c:
                continue
            if q == c:
                return 1.0
            if c.startswith(q) or all(word in c.split() for word in q.split()):
                s = 0.9
            elif q in c:
                s = 0.8
            else:
                s = difflib.SequenceMatcher(None, q, c).ratio() * 0.85
            best = max(best, s)
    return best


def _shortcut_dirs() -> list[Path]:
    env = os.environ.get
    dirs = [
        Path(env("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(env("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(env("USERPROFILE", "")) / "Desktop",
        Path(env("PUBLIC", "C:/Users/Public")) / "Desktop",
        Path(env("USERPROFILE", "")) / "OneDrive/Desktop",
        Path(env("USERPROFILE", "")) / "OneDrive/Рабочий стол",
    ]
    return [d for d in dirs if str(d) not in ("", ".") and d.is_dir()]


@lru_cache(maxsize=1)
def _shortcuts() -> list[tuple[str, Path]]:
    found = []
    for folder in _shortcut_dirs():
        for path in folder.rglob("*"):
            if path.suffix.lower() in (".lnk", ".url", ".exe", ".appref-ms"):
                name = path.stem
                if not any(w in name.lower() for w in _SKIP_WORDS):
                    found.append((name, path))
    return found


@lru_cache(maxsize=1)
def _store_apps() -> list[tuple[str, str]]:
    """Все приложения из «Пуска», включая Store: [(название, AppID)]."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        ).stdout
        data = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    if isinstance(data, dict):
        data = [data]
    return [(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]


def _app_paths(name: str) -> str | None:
    """Программы, зарегистрированные в реестре App Paths (их находит Win+R)."""
    import winreg

    exe = name if name.lower().endswith(".exe") else name + ".exe"
    key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                value = winreg.QueryValue(k, None)
                if value:
                    return value.strip('"')
        except OSError:
            continue
    return None


def _best(query: str, items: list[tuple[str, object]]) -> tuple[float, str, object] | None:
    scored = [(score(query, name), name, target) for name, target in items]
    if not scored:
        return None
    # При равенстве берём название покороче: «Яндекс» -> «Яндекс Браузер», а не «Яндекс Браузер — обновление».
    return max(scored, key=lambda t: (t[0], -len(t[1])))


def suggestions(query: str, limit: int = 5) -> list[str]:
    names = [n for n, _ in _shortcuts()] + [n for n, _ in _store_apps()]
    ranked = sorted(set(names), key=lambda n: score(query, n), reverse=True)
    return ranked[:limit]


def launch_windows(name: str) -> str | None:
    """Запускает программу. Возвращает, что запущено, или None, если не нашлось."""
    target = os.path.expandvars(name.strip().strip('"'))

    # Ссылки, протоколы и существующие пути — открываем как есть.
    from .tools import APP_SCHEMES, WEB_SCHEMES, scheme_of

    scheme = scheme_of(target)
    if scheme and scheme not in WEB_SCHEMES + APP_SCHEMES:
        return None  # ms-msdt:, search-ms: и прочее опасное не открываем вовсе
    if scheme or Path(target).exists():
        os.startfile(target)  # type: ignore[attr-defined]
        return target

    # Команды из PATH и App Paths: notepad, calc, chrome, winword...
    simple = target if " " not in target else None
    if simple:
        exe = shutil.which(simple) or _app_paths(simple)
        if exe:
            os.startfile(exe)  # type: ignore[attr-defined]
            return exe

    best = _best(target, _shortcuts())  # type: ignore[arg-type]
    if best and best[0] >= MATCH_THRESHOLD:
        os.startfile(str(best[2]))  # type: ignore[attr-defined]
        return best[1]

    best = _best(target, _store_apps())  # type: ignore[arg-type]
    if best and best[0] >= MATCH_THRESHOLD:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{best[2]}"])
        return best[1]
    return None
