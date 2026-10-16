import pytest

import jarvis.quick as Q
from jarvis.tools import ToolError


@pytest.fixture
def run():
    calls = []

    def fn(tool, args):
        calls.append((tool, args))
        if tool == "open_app" and args["name"] == "абракадабра":
            raise ToolError("не найдена")
        if tool == "set_timer":
            return f"Напоминание через {args['minutes']} мин: {args['text']}"
        if tool == "power":
            return f"power {args['action']} {args['delay_minutes']}"
        return "ok"

    fn.calls = calls
    return fn


@pytest.mark.parametrize("text,tool,action", [
    ("громче", "media", "volume_up"), ("Сделай потише.", "media", "volume_down"), ("выключи звук", "media", "mute"),
    ("поставь на паузу", "media", "play_pause"), ("следующий трек", "media", "next"),
    ("предыдущая песня", "media", "previous"), ("сверни все окна", "press_keys", None),
    ("заблокируй компьютер", "power", "lock"), ("сделай скрин", "save_screenshot", None),
    ("отмени все напоминания", "cancel_timers", None),
])
def test_media_and_simple(run, text, tool, action):
    assert Q.quick_command(text, run)
    assert run.calls[0][0] == tool
    if action:
        assert run.calls[0][1]["action"] == action


@pytest.mark.parametrize("text,expected", [
    ("напомни через пятнадцать минут покормить персонажа", (15, "покормить персонажа")),
    ("напомни через полчаса проверить садок", (30, "проверить садок")),
    ("напомни мне через 2 часа сменить точку", (120, "сменить точку")),
    ("напомни через минуту", (1, "время вышло")),
    ("поставь таймер на десять минут", (10, "время вышло")),
    ("напомни через двадцать пять минут что надо поесть", (25, "надо поесть")),
    ("напомни через полтора часа выйти", (90, "выйти")),
    ("напомни через час про наживку", (60, "наживку")),
    ("напомни завтра купить хлеб", None),
    ("напомни купить хлеб через десять минут", None),
])
def test_parse_timer(text, expected):
    assert Q.parse_timer(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("выключи комп", ("shutdown", 0)), ("выключи компьютер через полчаса", ("shutdown", 30)),
    ("вырубай пк через двадцать минут", ("shutdown", 20)), ("перезагрузи компьютер", ("restart", 0)),
    ("перезагрузи систему через 5 минут", ("restart", 5)), ("отправь комп в сон", ("sleep", 0)),
    ("усыпи компьютер через час", ("sleep", 60)), ("отмени выключение", ("cancel", 0)),
    ("не выключай компьютер", ("cancel", 0)), ("заверши работу компьютера", ("shutdown", 0)),
    ("выключи звук", None), ("выключись", None), ("выключи свет", None),
])
def test_parse_power(text, expected):
    assert Q.parse_power(text) == expected


def test_power_declined(run):
    def fn(tool, args):
        raise ToolError("Пользователь не подтвердил действие.")
    assert Q.quick_command("выключи комп", fn) == "Ок, не трогаю."


@pytest.mark.parametrize("words,n", [("двадцать пять", 25), ("2", 2), ("", 1), ("полторы", 1.5), ("сто", 100),
                                     ("2,5", 2.5), ("много", None)])
def test_parse_amount(words, n):
    assert Q.parse_amount(words) == n


@pytest.mark.parametrize("text,level", [("громкость 30", 30), ("поставь громкость на пятьдесят процентов", 50),
                                         ("громкость ноль", 0), ("громкость на максимум", 100),
                                         ("громкость на половину", 50)])
def test_volume(run, text, level):
    assert Q.quick_command(text, run) == f"Громкость {level} процентов."
    ups = [a["times"] for t, a in run.calls if a.get("action") == "volume_up"]
    assert ups == ([round(level / 2)] if level else [])


def test_volume_out_of_range_goes_to_llm(run):
    assert Q.quick_command("громкость 300", run) is None


def test_time_date():
    assert Q.quick_command("сколько времени", lambda *a: "").startswith("Сейчас ")
    assert Q.quick_command("какое сегодня число", lambda *a: "").startswith("Сегодня ")


def test_weather(monkeypatch):
    seen = []

    class R:
        def __init__(self, ok):
            self.ok = ok

        def json(self):
            if not self.ok:
                raise ValueError("not json")
            return {"current_condition": [{"temp_C": "5", "FeelsLikeC": "-2", "windspeedKmph": "18",
                                           "lang_ru": [{"value": "Облачно"}]}]}

    def get(url, params=None, timeout=None):
        seen.append(url)
        return R(url.endswith("москва"))

    import requests
    monkeypatch.setattr(requests, "get", get)
    assert Q.quick_command("какая погода в москве", lambda *a: "") == \
        "Москва: облачно, плюс 5, ощущается как минус 2, ветер 5 метров в секунду."
    assert seen[0].endswith("москве")  # сначала как сказали, потом в именительном


def test_weather_offline_falls_to_llm(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError()
    monkeypatch.setattr(requests, "get", boom)
    assert Q.quick_command("какая погода", lambda *a: "") is None


@pytest.mark.parametrize("text,target", [("открой телеграм", "телеграм"), ("Запусти телегу, пожалуйста", "телегу"),
                                          ("открой мне стим", "стим")])
def test_open(run, text, target):
    assert Q.quick_command(text, run) == "Открываю."
    assert run.calls == [("open_app", {"name": target})]


def test_open_domain(run):
    Q.quick_command("открой youtube.com", run)
    assert run.calls == [("open_url", {"url": "youtube.com"})]


@pytest.mark.parametrize("text", ["открой папку загрузки", "открой телеграм и напиши привет",
                                  "открой абракадабра", "какая-то болтовня", ""])
def test_goes_to_llm(run, text):
    assert Q.quick_command(text, run) is None
