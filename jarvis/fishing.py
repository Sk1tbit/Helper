"""Поиск точек и наживок для «Русской Рыбалки 4».

Открытой базы точек нет — всё живёт в Telegram-каналах, группах ВК, роликах и на форумах.
Поэтому: ищем посты в интернете и в указанных Telegram-каналах (у публичных каналов есть
веб-версия с поиском t.me/s/<канал>?q=...), заходим на найденные страницы, вытаскиваем
координаты вида «56:42» и фразы рядом с ними, ранжируем и отдаём лучшее.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import requests

# Водоёмы РР4 и начала слов, по которым их узнаём в речи («на вьюнке», «на старом остроге»).
# Порядок важен: архипелаг раньше Ладоги.
WATERS: list[tuple[str, tuple[str, ...]]] = [
    ("Ладожский архипелаг", ("архипелаг",)),
    ("Ладожское озеро", ("ладожск", "ладог")),
    ("Озеро Комариное", ("комарин",)),
    ("Река Вьюнок", ("вьюн",)),
    ("Озеро Старый Острог", ("острог",)),
    ("Река Белая", ("белая", "белой", "белую")),
    ("Озеро Куори", ("куори",)),
    ("Озеро Медвежье", ("медвеж",)),
    ("Река Волхов", ("волхов",)),
    ("Река Северский Донец", ("донец", "донц")),
    ("Река Сура", ("сура", "суре", "суру", "суры")),
    ("Озеро Янтарное", ("янтарн", "янтарк")),
    ("Река Ахтуба", ("ахтуб",)),
    ("Озеро Медное", ("медн",)),
    ("Река Нижняя Тунгуска", ("тунгуск",)),
    ("Река Яма", ("яма", "яме", "яму", "ямы")),
    ("Норвежское море", ("норвеж",)),
]
# Слова, которые тоже относятся к названию водоёма и не являются целью поиска.
WATER_EXTRA = ("стар", "северск", "нижн", "ладожск")
FILLER = {
    "найди", "найти", "поищи", "ищи", "покажи", "где", "ловить", "поймать", "ловится", "клюет", "клюёт",
    "точку", "точка", "точки", "пост", "посты", "на", "по", "в", "река", "реке", "реку", "озеро", "озере",
    "рр4", "рр", "русская", "рыбалка", "рыбалке", "мне", "пожалуйста", "и", "для", "как", "что", "чем", "море",
}
COORD_RE = re.compile(r"(?<![\d:./])(\d{1,3})\s?:\s?(\d{1,3})(?![\d:./])")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
PREFERRED = ("t.me", "vk.com", "rf4game.ru", "rf4game.com", "rf4.info", "forum", "rutube.ru", "youtube.com")


def _stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    return re.sub(r"[аяуюеиыйоь]+$", "", word) if len(word) > 4 else word


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))).strip()


def parse_request(text: str) -> tuple[str, str] | None:
    """«река вьюнок перловица» -> ("перловица", "Река Вьюнок"). None — если водоём не назван."""
    words = _norm(text).split()
    for water, prefixes in WATERS:
        if any(w.startswith(p) for w in words for p in prefixes):
            break
    else:
        return None
    rest = [w for w in words
            if not any(w.startswith(p) for p in prefixes + WATER_EXTRA) and w not in FILLER]
    if not rest or len(rest) > 4:
        return None
    return " ".join(rest), water


@dataclass
class Hit:
    title: str
    url: str
    text: str
    score: float = 0.0
    coords: list[str] = field(default_factory=list)


def _clean_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _snippets(text: str, words: list[str], limit: int = 3) -> list[str]:
    """Фразы, где упомянута цель и/или координаты."""
    out = []
    for line in re.split(r"[\n.!?]+", text):
        line = re.sub(r"\s+", " ", line).strip()
        low = line.lower().replace("ё", "е")
        if 10 < len(line) < 300 and (any(w in low for w in words) or COORD_RE.search(line)):
            if any(w in low for w in words) or len(out) < 1:
                out.append(line)
        if len(out) >= limit:
            break
    return out


def _coords(text: str) -> list[str]:
    found = []
    for x, y in COORD_RE.findall(text):
        c = f"{int(x)}:{int(y)}"
        # Отсекаем время вроде 12:30 и даты — у точек РР4 обе координаты обычно 1–200.
        if int(y) < 200 and int(x) < 200 and c not in found:
            found.append(c)
    return found[:6]


class FishingSearch:
    def __init__(self, tg_channels: list[str] | None = None, timeout: float = 8):
        self.channels = [c.strip().lstrip("@").replace("https://t.me/", "") for c in (tg_channels or []) if c]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(UA)
        self.errors: list[str] = []  # почему не ответили источники — чтобы не врать «ничего нет»

    # --- источники -----------------------------------------------------------

    def _telegram(self, target: str, water: str) -> list[Hit]:
        hits = []
        for ch in self.channels:
            try:
                page = self.session.get(f"https://t.me/s/{ch}?q={quote_plus(target)}", timeout=self.timeout).text
            except requests.RequestException as e:
                self.errors.append(f"Telegram @{ch}: {type(e).__name__}")
                continue
            for post, body in re.findall(
                r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.S
            ):
                text = _clean_html(body)
                hits.append(Hit(title=f"Telegram @{post.split('/')[0]}", url=f"https://t.me/{post}", text=text))
        return hits

    def _web(self, target: str, water: str) -> list[Hit]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        short_water = water.split()[-1]
        queries = [
            f"РР4 {short_water} {target} точка",
            f"Русская рыбалка 4 {water} {target} где ловить наживка",
            f"site:t.me РР4 {short_water} {target}",
        ]
        hits, seen = [], set()
        ddgs = DDGS()
        for q in queries:
            try:
                results = ddgs.text(q, region="ru-ru", max_results=8) or []
            except Exception as e:
                self.errors.append(f"поиск: {type(e).__name__}")
                continue
            for r in results:
                url = r.get("href") or r.get("url") or ""
                if url and url not in seen:
                    seen.add(url)
                    hits.append(Hit(title=r.get("title", ""), url=url, text=r.get("body", "")))
        return hits

    def _fetch(self, hit: Hit) -> None:
        """Дочитывает страницу: в выдаче поисковика обычно нет координат."""
        try:
            r = self.session.get(hit.url, timeout=self.timeout)
            if "text/html" not in r.headers.get("content-type", "text/html"):
                return
            raw = r.text[:400_000]
        except requests.RequestException:
            return
        meta = " ".join(re.findall(r'<meta[^>]+(?:og:description|name="description")[^>]+content="([^"]*)"', raw))
        hit.text = f"{hit.text}\n{html.unescape(meta)}\n{_clean_html(raw)}"

    # --- главное -------------------------------------------------------------

    def search(self, target: str, water: str, fetch_pages: int = 4) -> list[Hit]:
        t_words = [_stem(w) for w in _norm(target).split()]
        w_words = [_stem(w) for w in _norm(water).split() if w not in ("река", "озеро", "море")]
        hits = self._telegram(target, water) + self._web(target, water)

        def score(h: Hit) -> float:
            low = _norm(f"{h.title} {h.text}")
            s = 3.0 * all(w in low for w in t_words) + 2.0 * any(w in low for w in w_words)
            s += 1.5 * bool(h.coords) + (1.0 if any(p in h.url for p in PREFERRED) else 0)
            s += 1.0 * ("рр4" in low or "русская рыбалка" in low or "rf4" in low)
            return s

        for h in hits:
            h.coords = _coords(h.text)
            h.score = score(h)
        hits.sort(key=lambda h: h.score, reverse=True)
        for h in hits[:fetch_pages]:
            if not h.coords:
                self._fetch(h)
                h.coords = _coords(h.text)
                h.score = score(h)
        hits.sort(key=lambda h: h.score, reverse=True)
        # Источник обязан упоминать то, что ищем, — иначе это мусор из выдачи.
        return [h for h in hits if h.score >= 3 and all(w in _norm(f"{h.title} {h.text}") for w in t_words)][:6]


def tg_app_link(url: str) -> str:
    """https://t.me/channel/123 -> tg://resolve?domain=channel&post=123 (откроется в приложении)."""
    m = re.match(r"https?://t\.me/(?:s/)?([\w_]+)/(\d+)", url)
    return f"tg://resolve?domain={m.group(1)}&post={m.group(2)}" if m else url


def describe(target: str, water: str, hits: list[Hit]) -> str:
    """Текст для нейросети/озвучки: лучшие источники, координаты и фразы про наживку."""
    if not hits:
        return f"По запросу «{target}» на «{water}» ничего толкового не нашлось."
    t_words = [_stem(w) for w in _norm(target).split()]
    lines = [f"Запрос: {target}, {water}. Найдено источников: {len(hits)}."]
    for i, h in enumerate(hits[:4], 1):
        lines.append(f"{i}. {h.title or h.url} — {h.url}")
        if h.coords:
            lines.append(f"   Координаты: {', '.join(h.coords)}")
        for sn in _snippets(h.text, t_words):
            lines.append(f"   «{sn}»")
    return "\n".join(lines)
