import pytest

import jarvis.fishing as F


@pytest.mark.parametrize("text,expected", [
    ("река вьюнок перловица", ("перловица", "Река Вьюнок")),
    ("Найди точку на карпа на Старом Остроге", ("карпа", "Озеро Старый Острог")),
    ("где ловить леща на ахтубе", ("леща", "Река Ахтуба")),
    ("перловица на вьюнке пожалуйста", ("перловица", "Река Вьюнок")),
    ("ладожский архипелаг кумжа", ("кумжа", "Ладожский архипелаг")),
    ("на ладоге судак", ("судак", "Ладожское озеро")),
    ("северский донец сазан", ("сазан", "Река Северский Донец")),
    ("нижняя тунгуска таймень", ("таймень", "Река Нижняя Тунгуска")),
    ("норвежка треска", ("треска", "Норвежское море")),
    ("вьюнок", None), ("открой телеграм", None), ("белая машина", None), ("там яма", None),
    ("медленно сделай", None), ("сура", None),
])
def test_parse_request(text, expected):
    assert F.parse_request(text) == expected


def test_coords():
    assert F._coords("Точка 56:42 и 112:9, стрим в 19:30, дата 12/05, 1:2:3, 300:10") == ["56:42", "112:9", "19:30"]


def make_search(tg=(), web=()):
    fs = F.FishingSearch(["chan"])
    fs._telegram = lambda t, w: list(tg)
    fs._discord = lambda t, w: []
    fs._web = lambda t, w: list(web)
    fs._fetch = lambda h: None
    return fs


def test_ranking_and_filtering():
    fs = make_search(
        tg=[F.Hit("Telegram @x", "https://t.me/x/1", "Вьюнок. Перловица 63:71 на мормышку")],
        web=[F.Hit("Вьюнок перловица", "https://vk.com/wall-1_2", "Перловица на Вьюнке. Точка 47:58"),
             F.Hit("РР4 карп Медное", "https://rutube.ru/x", "карп медное 12:44 рр4"),
             F.Hit("Погода", "https://weather.com", "19:30 дождь")])
    hits = fs.search("перловица", "Река Вьюнок")
    assert [h.url for h in hits] == ["https://t.me/x/1", "https://vk.com/wall-1_2"]
    text = F.describe("перловица", "Река Вьюнок", hits)
    assert "63:71" in text and "47:58" in text


def test_fetch_used_when_snippet_has_no_coords():
    fs = make_search(web=[F.Hit("Перловица Вьюнок РР4", "https://forum/1", "перловица на вьюнке")])

    def fetch(h):
        h.text += "\nПерловица — точка 81:62, наживка мотыль"
    fs._fetch = fetch
    hits = fs.search("перловица", "Река Вьюнок")
    assert hits and hits[0].coords == ["81:62"]


def test_telegram_html_parsing(monkeypatch):
    html = ('<div class="tgme_widget_message" data-post="rr4/77"><div class="tgme_widget_message_text js-message_text">'
            'Вьюнок<br/>Перловица 63:71 &amp; мормышка</div></div>')

    class R:
        text = html
    fs = F.FishingSearch(["rr4"])
    monkeypatch.setattr(fs.session, "get", lambda *a, **k: R())
    hits = fs._telegram("перловица", "Река Вьюнок")
    assert hits[0].url == "https://t.me/rr4/77" and "63:71 & мормышка" in hits[0].text


def test_errors_are_recorded(monkeypatch):
    import requests
    fs = F.FishingSearch(["rr4"])

    def boom(*a, **k):
        raise requests.ConnectionError()
    monkeypatch.setattr(fs.session, "get", boom)
    assert fs._telegram("x", "y") == [] and fs.errors


def test_discord_reads_content_and_embeds(monkeypatch):
    class R:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

    def get(url, headers=None, params=None, timeout=None):
        assert headers["Authorization"] == "Bot TKN"
        if url.endswith("/channels/987"):
            return R(200, {"guild_id": "5", "name": "точки"})
        if url.endswith("/channels/111"):
            return R(403, {})
        if url.endswith("/987/messages"):
            if params.get("before"):
                return R(200, [])
            return R(200, [{"id": "1", "content": "Вьюнок перловица 63:71"}, {"id": "2", "content": "карп"},
                           {"id": "3", "content": "", "embeds": [{"title": "Перловица", "description": "58:40"}]}])
        return R(404, {})

    fs = F.FishingSearch([], discord_token="TKN", discord_channels=["https://discord.com/channels/5/987", "111"])
    monkeypatch.setattr(fs.session, "get", get)
    hits = fs._discord("перловица", "Река Вьюнок")
    assert [h.url for h in hits] == ["https://discord.com/channels/5/987/1", "https://discord.com/channels/5/987/3"]
    assert any("111" in e for e in fs.errors)


def test_links():
    assert F.tg_app_link("https://t.me/rr4/123") == "tg://resolve?domain=rr4&post=123"
    assert F.tg_web_link("https://t.me/rr4/123").startswith("https://web.telegram.org/a/#?tgaddr=tg%3A%2F%2Fresolve")
    assert F.tg_web_link("https://example.com") == "https://example.com"
