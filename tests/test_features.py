"""Органайзер, курсы, пульт, MCP, конспекты, умный дом и шаги агента."""
from __future__ import annotations

import json
import sys
import textwrap
import time
from datetime import date

import pytest
import requests

from conftest import openai_reply
from jarvis.config import Config


def _http():
    s = requests.Session()
    s.trust_env = False  # мимо прокси
    return s


# --- органайзер ------------------------------------------------------------------

def test_parse_date_variants():
    from jarvis.organizer import parse_date

    today = date(2026, 10, 18)  # воскресенье
    assert parse_date("24 октября день рождения мамы", today)[0] == date(2026, 10, 24)
    assert parse_date("двадцать четвертое октября днюха", today)[0] == date(2026, 10, 24)
    assert parse_date("завтра в 18:00 тренировка", today)[:2] == (date(2026, 10, 19), "18:00")
    assert parse_date("5 января новый год", today)[0] == date(2027, 1, 5)  # прошло — следующий год
    d, hhmm, rest = parse_date("в пятницу в 7 вечера кино", today)
    assert d == date(2026, 10, 23) and hhmm == "19:00" and "кино" in rest


def test_quick_organizer_flow():
    from jarvis.organizer import Organizer, quick_organizer

    org = Organizer(notify=lambda t: None)
    assert "Добавил задачу" in quick_organizer("добавь задачу купить хлеб", org)
    assert "купить хлеб" in quick_organizer("какие задачи", org)
    assert "выполнена" in quick_organizer("купить хлеб выполнена", org)
    assert quick_organizer("какие задачи", org) == "Задач нет, красота."
    assert "Записал в календарь" in quick_organizer("24 октября день рождения мамы", org)
    assert quick_organizer("завтра какая погода", org) is None  # вопрос — не событие
    assert quick_organizer("напомни через 10 минут чай", org) is None  # это таймер
    assert "Удалил" in quick_organizer("удали событие день рождения", org)
    org2 = Organizer()  # сохранилось на диск
    assert org2.list_tasks(include_done=True)[0]["done"] is True


def test_organizer_morning(monkeypatch):
    from jarvis.organizer import Organizer

    org = Organizer(notify=lambda t: None)
    org.add_event("стрим", date.today(), "23:59")
    assert "стрим" in org.start()


# --- курсы и погода ------------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_rates_and_plural(monkeypatch):
    import jarvis.rates as R

    R._cache.clear()
    monkeypatch.setattr(R.requests, "get", lambda *a, **k: _Resp({"Valute": {
        "USD": {"Value": 81.5, "Previous": 81.0, "Nominal": 1}, "KZT": {"Value": 16.2, "Previous": 16.3, "Nominal": 100}}}))
    r = R.rates()
    assert r["USD"]["value"] == 81.5 and r["USD"]["change"] == pytest.approx(0.5)
    assert R.plural(81, "рубль", "рубля", "рублей") == "рубль"
    assert R.plural(12, "рубль", "рубля", "рублей") == "рублей"
    assert R.plural(23, "рубль", "рубля", "рублей") == "рубля"
    assert "81" in R.say_rate("USD")


def test_weather_data_declension(monkeypatch):
    import jarvis.rates as R

    R._cache.clear()
    asked = []

    def get(url, **k):
        asked.append(url)
        if url.endswith("/москве"):
            raise R.requests.ConnectionError("нет такого")
        return _Resp({"current_condition": [{"temp_C": "-3", "FeelsLikeC": "-8", "windspeedKmph": "18",
                                             "lang_ru": [{"value": "Снег"}]}]})
    monkeypatch.setattr(R.requests, "get", get)
    w = R.weather_data("москве")
    assert w == {"city": "Москва", "temp": -3, "feels": -8, "desc": "снег", "wind": 5}
    from jarvis.quick import weather
    assert "минус 3" in weather("москве")


def test_rate_quick_phrases():
    from jarvis.quick import RATE_RE

    for phrase in ("курс доллара", "че по баксу", "курсы валют", "сколько стоит евро"):
        assert RATE_RE.search(phrase), phrase


# --- шаги агента ------------------------------------------------------------------------

def test_describe_call():
    from jarvis.tools import describe_call

    assert describe_call("mouse_click", {"x": 640, "y": 700}) == "Нажимаю (640, 700)"
    assert describe_call("type_text", {"text": "привет"}) == "Печатаю «привет»"
    assert describe_call("mcp_files_read", {}) == "MCP: files_read"
    assert describe_call("weird_tool", {}) == "Weird tool"


def test_agent_events(make_brain):
    brain, said, _ = make_brain([
        openai_reply(calls=[("list_tasks", {})]),
        openai_reply(calls=[("add_task", {"text": "позвонить"})]),
        openai_reply("Готово, задача добавлена."),
    ], quick_commands=False)
    events = []
    brain.on_event = lambda k, d: events.append((k, d))
    brain.handle("посмотри задачи и добавь позвонить")
    kinds = [k for k, _ in events]
    assert kinds == ["start", "step", "step", "done"]
    assert events[2][1] == {"n": 2, "text": "Добавляю задачу «позвонить»"}


def test_agent_events_cancelled(make_brain):
    brain, said, _ = make_brain([openai_reply(calls=[("list_tasks", {})])], quick_commands=False)
    events = []

    def on(kind, data):
        events.append(kind)
        if kind == "step":
            brain.interrupt()
    brain.on_event = on
    brain.handle("сделай что-нибудь")
    assert events[-1] == "cancelled"


def test_broken_event_handler_does_not_break_brain(make_brain):
    brain, said, _ = make_brain([openai_reply("Привет!")], quick_commands=False)
    brain.on_event = lambda k, d: 1 / 0
    brain.handle("привет")
    assert said == ["Привет!"]


# --- пульт ---------------------------------------------------------------------------------

@pytest.fixture
def panel(make_brain):
    from jarvis.panel import Panel

    brain, said, _ = make_brain([openai_reply("Ответ с пульта")])
    ran, stopped = [], []
    p = Panel(brain.cfg, brain, run=lambda t: (ran.append(t), brain.handle(t)), stop=lambda: stopped.append(1), port=0)
    assert p.start()
    p.ran, p.stopped, p.said = ran, stopped, said
    yield p
    p.stop()


def test_panel_page_and_state(panel):
    s = _http()
    page = s.get(panel.url)
    assert page.status_code == 200 and "Пульт" in page.text
    panel.log("jarvis", "привет")
    panel.on_event("start", {"request": "открой стим"})
    panel.on_event("step", {"n": 1, "text": "Открываю steam"})
    st = s.get(panel.url + "api/state?since=0").json()
    assert st["chat"][0]["text"] == "привет" and st["agent"]["steps"][0]["text"] == "Открываю steam"
    assert s.get(panel.url + f"api/state?since={st['chat'][-1]['n']}").json()["chat"] == []


def test_panel_crud(panel, monkeypatch):
    import jarvis.rates as R
    monkeypatch.setattr(R, "weather_data", lambda c: None)
    monkeypatch.setattr(R, "rates", lambda: None)
    s = _http()
    post = lambda path, body: s.post(panel.url + path, json=body).json()
    assert post("api/task", {"text": "купить корм"})["ok"]
    tid = s.get(panel.url + "api/widgets").json()["tasks"][0]["id"]
    assert "выполнена" in post("api/task/done", {"id": tid, "done": True})["msg"]
    assert post("api/note", {"text": "идея"})["ok"]
    assert "Записал" in post("api/event", {"text": "созвон", "date": "2030-01-02", "time": "10:00"})["msg"]
    w = s.get(panel.url + "api/widgets").json()
    assert w["notes"][0]["text"] == "идея"
    import importlib.util
    if importlib.util.find_spec("psutil"):
        assert w["system"]["ram_total"] > 0
    else:
        assert w["system"] is None  # нет модуля — виджет пустой, а не падение
    assert post("api/delete", {"kind": "notes", "id": w["notes"][0]["id"]})["ok"]
    bad = s.post(panel.url + "api/task", json={"text": ""})
    assert bad.status_code == 400 and not bad.json()["ok"]
    assert s.post(panel.url + "api/nope", json={}).status_code == 404


def test_panel_say_and_stop(panel):
    s = _http()
    assert s.post(panel.url + "api/say", json={"text": "как дела"}).json()["ok"]
    for _ in range(50):
        if panel.said:
            break
        time.sleep(0.05)
    assert panel.ran == ["как дела"] and panel.said == ["Ответ с пульта"]
    s.post(panel.url + "api/stop", json={})
    assert panel.stopped == [1]


def test_panel_desks_saved(panel):
    s = _http()
    d = s.get(panel.url + "api/desks").json()
    assert "Утро" in d["templates"] and d["desks"][0]["name"] == "Главный"
    new = [{"id": "x", "name": "Игры", "widgets": ["fishing", "bogus", "system"]}]
    saved = s.post(panel.url + "api/desks", json={"desks": new}).json()["desks"]
    assert saved == [{"id": "x", "name": "Игры", "widgets": ["fishing", "system"]}]
    assert s.get(panel.url + "api/desks").json()["desks"] == saved


def test_panel_rejects_foreign_sites(panel):
    s = _http()
    # чужой сайт через DNS rebinding — другой Host
    assert s.get(panel.url + "api/state", headers={"Host": "evil.com"}).status_code == 403
    # чужая страница шлёт форму без JSON или с чужим Origin
    assert s.post(panel.url + "api/say", data="text=rm", headers={"Content-Type": "text/plain"}).status_code == 403
    assert s.post(panel.url + "api/say", json={"text": "x"}, headers={"Origin": "http://evil.com"}).status_code == 403
    assert panel.ran == []


# --- MCP ------------------------------------------------------------------------------------

FAKE_MCP = textwrap.dedent('''
    import json, sys
    def send(m): sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    print("мусор в stdout", flush=True)
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m: continue
        meth, p = m["method"], m.get("params", {})
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": p["protocolVersion"], "capabilities": {}}})
        elif meth == "tools/list" and not p.get("cursor"):
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [{"name": "echo", "description": "Повтор",
                  "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}], "nextCursor": "2"}})
        elif meth == "tools/list":
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [{"name": "fail-me"}]}})
        elif meth == "tools/call" and p["name"] == "echo":
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": "эхо: " + p["arguments"]["text"]}]}})
        elif meth == "tools/call":
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"isError": True, "content": [{"type": "text", "text": "сломалось"}]}})
''')


def test_mcp_hub(tmp_path):
    from jarvis.mcp import MCPHub

    script = tmp_path / "srv.py"
    script.write_text(FAKE_MCP, encoding="utf-8")
    hub = MCPHub([("demo", [sys.executable, str(script)], {})])
    got = []
    hub.start(on_ready=got.append)
    assert hub.ready.wait(20)
    try:
        names = [t["name"] for t in hub.tools]
        assert names == ["mcp_demo_echo", "mcp_demo_fail_me"] and got[0] is hub.tools
        assert hub.call("mcp_demo_echo", {"text": "привет"}) == "эхо: привет"
        assert hub.call("mcp_demo_fail_me", {}) == "Ошибка: сломалось"
    finally:
        hub.stop()


def test_mcp_bad_server_does_not_block(tmp_path):
    from jarvis.mcp import MCPHub

    hub = MCPHub([("nope", ["definitely-not-a-command-xyz"], {})])
    hub.start()
    assert hub.ready.wait(10) and hub.tools == []


def test_mcp_specs(tmp_path):
    from jarvis.mcp import load_specs

    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "x"], "env": {"A": "1"}}, "off": {"command": "x", "disabled": True}}}))
    specs = load_specs(["npx -y @modelcontextprotocol/server-filesystem"], "mcp.json", tmp_path)
    assert specs[0][0] == "filesystem" and specs[0][1][:2] == ["npx", "-y"]
    assert specs[1] == ("fs", ["npx", "-y", "x"], {"A": "1"}) and len(specs) == 2


def test_brain_routes_mcp_tools(tmp_path, make_brain):
    script = tmp_path / "srv.py"
    script.write_text(FAKE_MCP, encoding="utf-8")
    brain, said, server = make_brain([
        openai_reply(calls=[("mcp_srv_echo", {"text": "раз"})]),
        openai_reply("Сервер сказал эхо."),
    ], quick_commands=False, mcp_servers=[f'"{sys.executable}" "{script}"'])
    assert brain.mcp.ready.wait(20)
    try:
        assert any(t["name"] == "mcp_srv_echo" for t in brain.tools)
        brain.handle("повтори через сервер раз")
        tool_msg = [m for m in brain.messages if m["role"] == "tool"][0]
        assert tool_msg["content"] == "эхо: раз" and said == ["Сервер сказал эхо."]
        sent = server.requests[0][2]["tools"]
        assert any(t["function"]["name"] == "mcp_srv_echo" for t in sent)
    finally:
        brain.shutdown(timeout=1)


# --- конспекты ----------------------------------------------------------------------------

def test_chunks_and_parse():
    from jarvis.summary import chunks, parse_summary

    parts = chunks("Фраза номер один. " * 1000, size=500)
    assert len(parts) > 10 and all(len(p) <= 500 for p in parts)
    assert chunks("x" * 1200, size=500) == ["x" * 500, "x" * 500, "x" * 200]
    p = parse_summary("**Итог:** обсудили релиз.\nРешения:\n- выпускаем в пятницу\nЗадачи:\n1. Петя пишет тесты\n- нет")
    assert p == {"итог": "обсудили релиз.", "решения": ["выпускаем в пятницу"], "задачи": ["Петя пишет тесты"]}
    assert parse_summary("Итог: ничего\nРешения: нет\nЗадачи:\n- нет")["задачи"] == []


def test_meeting_summary_flow(make_brain, isolated_memory):
    brain, said, server = make_brain([
        openai_reply("Итог: обсудили релиз.\nРешения:\n- релиз в пятницу\nЗадачи:\n- написать тесты\n- обновить доки"),
    ])
    brain.handle("веди конспект")
    assert brain.recorder.active and "Веду конспект" in said[-1]
    assert brain.note_heard("значит релиз в пятницу")
    brain.note_heard("Петя пишет тесты, Маша доки")
    brain.handle("закончи конспект")
    assert not brain.recorder.active
    assert said[-1].startswith("обсудили релиз.") and "Задач добавил: 2" in said[-1]
    assert [t["text"] for t in brain.organizer.list_tasks()] == ["написать тесты", "обновить доки"]
    files = list((isolated_memory / "summaries").glob("*.md"))
    assert files and "Петя пишет тесты" in files[0].read_text(encoding="utf-8")
    assert "Петя пишет тесты" in server.requests[-1][2]["messages"][1]["content"]


def test_long_meeting_map_reduce(make_brain):
    from jarvis.summary import Recorder, chunks

    rec = Recorder()
    rec.start()
    for i in range(500):
        rec.add(f"Предложение номер {i} про очень важные дела.")
    n = len(chunks("\n".join(rec.lines)))
    assert n >= 3
    brain, said, server = make_brain([openai_reply("часть")] * n + [openai_reply("Итог: длинно.\nЗадачи:\n- нет")])
    brain.recorder = rec
    reply = brain.finish_summary()
    assert reply.startswith("длинно.") and len(server.requests) == n + 1
    assert server.requests[-1][2]["messages"][1]["content"].count("часть") == n


def test_empty_meeting(make_brain):
    brain, said, _ = make_brain([])
    brain.handle("начни конспект")
    brain.handle("закончи конспект")
    assert said[-1] == "Конспект пустой — я ничего не услышал."


def test_not_recording_ignores_speech(make_brain):
    brain, _, _ = make_brain([])
    assert brain.note_heard("что-то") is False


# --- умный дом --------------------------------------------------------------------------

def test_home_assistant(fake_server, make_brain):
    calls = []

    def handler(method, path, body):
        if path == "/api/states":
            return 200, [{"entity_id": "light.room", "state": "off", "attributes": {"friendly_name": "Свет в комнате"}},
                         {"entity_id": "sensor.t", "state": "21", "attributes": {}},
                         {"entity_id": "cover.shtory", "state": "open", "attributes": {"friendly_name": "Шторы"}}]
        calls.append((path, body))
        return 200, []
    ha = fake_server(handler)
    brain, said, _ = make_brain([], ha_url=ha.url, ha_token="t")
    assert any(t["name"] == "smart_home" for t in brain.tools)
    brain.handle("включи свет в комнате")
    assert calls[-1] == ("/api/services/light/turn_on", {"entity_id": "light.room"}) and "включил" in said[-1]
    assert brain.toolbox.run("smart_home", {"action": "off", "name": "шторы"}) == "Шторы: закрыл"
    assert calls[-1][0] == "/api/services/cover/close_cover"
    assert "Свет в комнате: off" in brain.toolbox.run("smart_home", {"action": "list"})
    assert ha.requests[0][0] == "GET"


def test_home_not_device_falls_through(fake_server, make_brain):
    ha = fake_server(lambda m, p, b: (200, [{"entity_id": "light.room", "state": "off",
                                             "attributes": {"friendly_name": "Люстра"}}]))
    brain, said, _ = make_brain([], ha_url=ha.url, ha_token="t")
    assert brain._quick_home("включи музыку") is None


def test_no_home_hides_tool(make_brain):
    brain, _, _ = make_brain([])
    assert not any(t["name"] == "smart_home" for t in brain.tools)
