import os
import time
from pathlib import Path

import pytest

import jarvis.apps as A
import jarvis.tools as T


@pytest.mark.parametrize("query,best", [
    ("телегу", "Telegram"), ("яндекс", "Яндекс Браузер"), ("яндекс музыку", "Яндекс Музыка"), ("стим", "Steam"),
    ("дискорде", "Discord"), ("хром", "Google Chrome"), ("вс код", "Visual Studio Code"), ("фотошоп", "Adobe Photoshop 2024"),
    ("эдж", "Microsoft Edge"), ("ворд", "Word"), ("обс", "OBS Studio"), ("калькулятор", "Калькулятор"),
])
def test_app_matching(query, best):
    names = ["Яндекс Браузер", "Telegram", "Discord", "Steam", "Google Chrome", "Visual Studio Code", "Яндекс Музыка",
             "Microsoft Edge", "Блокнот", "Adobe Photoshop 2024", "Word", "OBS Studio", "Калькулятор"]
    score, name, _ = A._best(query, [(n, n) for n in names])
    assert name == best and score >= A.MATCH_THRESHOLD


@pytest.mark.parametrize("query", ["абракадабра", "папку загрузки"])
def test_app_no_false_match(query):
    score, _, _ = A._best(query, [(n, n) for n in ["Telegram", "Steam", "Яндекс Браузер"]])
    assert score < A.MATCH_THRESHOLD


@pytest.fixture
def windows(monkeypatch):
    started = []
    monkeypatch.setattr(T, "SYSTEM", "Windows")
    monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)
    monkeypatch.setattr(A, "_app_paths", lambda n: None)
    monkeypatch.setattr(A, "_shortcuts", lambda: [("Яндекс Браузер", Path("C:/sm/Яндекс Браузер.lnk")),
                                                  ("Telegram", Path("C:/d/Telegram.lnk"))])
    monkeypatch.setattr(A, "_store_apps", lambda: [("Калькулятор", "Microsoft.WindowsCalculator!App")])
    popen = []
    monkeypatch.setattr(A.subprocess, "Popen", lambda a, **k: popen.append(a))
    opened = []
    monkeypatch.setattr(T.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setenv("LOCALAPPDATA", "/nonexistent")
    return started, popen, opened


def test_open_app_windows(windows):
    started, popen, opened = windows
    tb = T.ToolBox(lambda q: True)
    assert "Яндекс Браузер" in tb.run("open_app", {"name": "Яндекс"})
    assert "Telegram" in tb.run("open_app", {"name": "телега"})
    assert "Калькулятор" in tb.run("open_app", {"name": "калькулятор"}) or started  # calc найдётся через PATH или Store
    assert "music.yandex.ru" in tb.run("open_app", {"name": "яндекс музыку"})  # программы нет — сайт
    assert opened == ["https://music.yandex.ru"]
    with pytest.raises(T.ToolError) as e:
        tb.run("open_app", {"name": "фотошоп"})
    assert "не найдена" in str(e.value)


def test_web_app_word_forms():
    assert T._web_app("Яндекс Музыку") == "https://music.yandex.ru"
    assert T._web_app("ютуб") == "https://www.youtube.com"
    assert T._web_app("яндекс") is None and T._web_app("блокнот") is None


def test_coerce_args():
    assert T._coerce_args("media", {"action": "volume_up", "times": "5", "junk": 1}) == {"action": "volume_up", "times": 5}
    assert T._coerce_args("mouse_click", {"x": "10.6", "y": 20, "double": "false"}) == {"x": 11, "y": 20, "double": False}
    assert T._coerce_args("press_keys", {"keys": "ctrl+c"}) == {"keys": ["ctrl", "c"]}
    assert T._coerce_args("set_timer", {"minutes": "1,5", "text": 5}) == {"minutes": 1.5, "text": "5"}
    assert T._coerce_args("nope", "garbage") == {}


def test_unknown_tool():
    with pytest.raises(T.ToolError):
        T.ToolBox(lambda q: True).run("hack_pentagon", {})


def test_timers_fire_list_cancel():
    said = []
    tb = T.ToolBox(lambda q: True, notify=said.append)
    tb.run("set_timer", {"minutes": 0.01, "text": "проверить садок"})
    tb.run("set_timer", {"minutes": 10, "text": "покормить"})
    assert "покормить" in tb.run("list_timers", {})
    time.sleep(1.2)
    assert said == ["Напоминаю: проверить садок"]
    assert "Отменено напоминаний: 1" == tb.run("cancel_timers", {"text": "покорм"})
    assert tb.run("list_timers", {}) == "Напоминаний нет"
    with pytest.raises(T.ToolError):
        tb.run("set_timer", {"minutes": -1, "text": "x"})


@pytest.fixture
def win_power(monkeypatch):
    ran = []

    class P:
        returncode = 0
    monkeypatch.setattr(T, "SYSTEM", "Windows")
    monkeypatch.setattr(T.subprocess, "run", lambda a, **k: (ran.append(a), P())[1])
    monkeypatch.setattr(T.subprocess, "Popen", lambda a, **k: ran.append(a))
    return ran


def test_power(win_power):
    answers = iter([True, False])
    tb = T.ToolBox(lambda q: next(answers))
    assert "30 мин" in tb.run("power", {"action": "shutdown", "delay_minutes": 30})
    assert win_power[-1] == ["shutdown", "/s", "/t", "1800"]
    with pytest.raises(T.ToolError):
        tb.run("power", {"action": "restart"})  # сказали «нет»
    tb.run("power", {"action": "cancel"})
    assert win_power[-1] == ["shutdown", "/a"]
    tb.run("power", {"action": "lock"})  # без вопроса
    assert win_power[-1][0] == "rundll32.exe"
    tb.confirm_power = False
    tb.run("power", {"action": "sleep"})
    assert "SetSuspendState" in win_power[-1][-1]
    tb.run("power", {"action": "shutdown"})
    assert win_power[-1] == ["shutdown", "/s", "/t", "10"]  # минимум 10 секунд на «отмена»


def test_fishing_tool_open_modes(monkeypatch):
    import jarvis.fishing as F
    monkeypatch.setattr(F.FishingSearch, "search",
                        lambda self, t, w: [F.Hit("Telegram @x", "https://t.me/x/5", "перловица 1:2")])
    opened = []
    monkeypatch.setattr(T.webbrowser, "open", lambda u: opened.append(u))
    tb = T.ToolBox(lambda q: True)
    for mode in ("web", "app", "browser"):
        tb.tg_open_mode = mode
        tb.run("find_fishing_spot", {"target": "перловица", "water": "Река Вьюнок"})
    assert opened[0].startswith("https://web.telegram.org/") and opened[1].startswith("tg://") \
        and opened[2] == "https://t.me/x/5"


def test_fishing_tool_reports_offline(monkeypatch):
    import jarvis.fishing as F

    def search(self, t, w):
        self.errors += ["поиск: X"] * 3
        return []
    monkeypatch.setattr(F.FishingSearch, "search", search)
    with pytest.raises(T.ToolError) as e:
        T.ToolBox(lambda q: True).run("find_fishing_spot", {"target": "a", "water": "b"})
    assert "недоступен" in str(e.value)


def test_run_command_requires_confirmation():
    tb = T.ToolBox(lambda q: False)
    with pytest.raises(T.ToolError):
        tb.run("run_command", {"command": "echo hi"})


def test_human_minutes():
    assert T._human_minutes(90) == "1 ч 30 мин" and T._human_minutes(0.5) == "30 сек" and T._human_minutes(0) == "0 сек"
