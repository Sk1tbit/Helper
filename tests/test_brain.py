import threading

import pytest

from conftest import openai_reply
import jarvis.tools as T


@pytest.fixture
def tools_log(monkeypatch):
    calls = []

    def run(self, name, args):
        calls.append((name, args))
        return "ok"
    monkeypatch.setattr(T.ToolBox, "run", run)
    return calls


def test_quick_command_does_not_call_llm(make_brain, tools_log):
    brain, said, srv = make_brain()
    brain.handle("громче")
    assert said == ["Сделал громче."] and not [r for r in srv.requests if r[0] == "POST"]


def test_tool_loop(make_brain, monkeypatch):
    brain, said, srv = make_brain([openai_reply("Смотрю.", [("system_info", {})]), openai_reply("Всё ок.")])
    monkeypatch.setattr(T.ToolBox, "tool_system_info", lambda self: "CPU 5%")
    brain.handle("как там комп")
    assert said == ["Смотрю.", "Всё ок."]
    tool_msg = [m for m in srv.requests[-1][2]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"] == "CPU 5%"


def test_nudge_when_model_only_talks(make_brain, tools_log):
    brain, said, srv = make_brain([openai_reply("Запустил, сэр."), openai_reply("", [("open_app", {"name": "x"})]),
                                   openai_reply("Готово.")])
    brain.cfg.quick_commands = False
    brain.handle("открой абракадабру")
    assert said == ["Готово."]  # враньё «Запустил» не озвучено
    assert tools_log == [("open_app", {"name": "x"})]


def test_cancel_stops_before_tools(make_brain, tools_log):
    brain, said, srv = make_brain([openai_reply("", [("open_app", {"name": "x"})])])
    brain.cfg.quick_commands = False
    real_chat = brain.llm.chat

    def chat(*a, **k):
        r = real_chat(*a, **k)
        brain.interrupt()  # сказали «стоп», пока модель думала
        return r
    brain.llm.chat = chat
    brain.handle("сделай что-то сложное")
    assert tools_log == [] and said == []
    roles = [m["role"] for m in brain.messages]
    assert roles[-1] == "tool"  # история целая: на каждый вызов есть ответ


def test_context_overflow_retries_fresh(make_brain):
    err = (400, {"error": "context length exceeded (n_ctx 8192)"})
    brain, said, srv = make_brain([err, openai_reply("Ответ.")])
    brain.cfg.disable_thinking = False
    brain.messages = [{"role": "user", "content": "старое"}, {"role": "assistant", "content": "x", "tool_calls": []}]
    brain.last_activity = __import__("time").time()
    brain.handle("вопрос")
    assert said == ["Ответ."]
    sent = srv.requests[-1][2]["messages"]
    assert len(sent) == 2 and "вопрос" in sent[1]["content"]  # system + только новая просьба


@pytest.mark.parametrize("payload", [b"garbage", {"choices": []}])
def test_bad_server_answer_does_not_crash(make_brain, payload):
    brain, said, srv = make_brain([(200, payload)])
    brain.cfg.quick_commands = False
    brain.handle("привет")
    assert said == ["Нейросеть вернула ошибку, попробуйте ещё раз."]
    assert brain.messages == []  # сломанный разговор сброшен


def test_llm_down_is_spoken(make_brain):
    brain, said, srv = make_brain()
    brain.llm.url = "http://127.0.0.1:1"
    brain.cfg.quick_commands = False
    brain.handle("привет")
    assert said == ["Нет связи с нейросетью. Проверьте, что она запущена."]


def test_macro_with_llm_step_passes_step_text(make_brain, tools_log):
    brain, said, srv = make_brain([openai_reply("Нашёл погоду.")])
    brain.handle("Запомни команду утро: открой телеграм, расскажи анекдот")
    assert "Запомнил команду «утро»" in said[0]
    said.clear()
    brain.handle("утро")
    assert tools_log == [("open_app", {"name": "телеграм"})]
    last_user = [m for m in srv.requests[-1][2]["messages"] if m["role"] == "user"][-1]
    assert last_user["content"] == "расскажи анекдот"
    assert said[0] == "Выполняю «утро»." and said[-1] == "Готово."


def test_macro_management(make_brain):
    brain, said, _ = make_brain()
    brain.handle("какие у меня команды")
    assert "пока нет" in said[-1]
    brain.handle("запомни команду погнали открой дискорд и открой стим")
    brain.handle("какие у меня команды")
    assert "погнали — открой дискорд, открой стим" in said[-1]
    brain.handle("удали команду погнали")
    assert said[-1] == "Удалил команду «погнали»."


def test_fishing_quick_path(make_brain, monkeypatch):
    brain, said, srv = make_brain([openai_reply("Точка 63:71, на мормышку.")])
    monkeypatch.setattr(T.ToolBox, "tool_find_fishing_spot",
                        lambda self, target, water, open_link=True: "1. Telegram — https://t.me/x/1\n   Координаты: 63:71\nОткрыл в браузере: https://t.me/x/1")
    brain.handle("вьюнок перловица")
    assert said == ["Ищу: перловица, Река Вьюнок.", "Точка 63:71, на мормышку."]
    assert "tools" not in srv.requests[-1][2]  # пересказ без инструментов


def test_game_mode_note_and_model_switch(make_brain):
    brain, said, srv = make_brain([openai_reply("Ок.")], game_model="light-model", game_name="РР4")
    assert "включён" in brain.set_game_mode(True)
    brain.cfg.quick_commands = False
    brain.handle("привет")
    body = srv.requests[-1][2]
    assert body["model"] == "light-model" and "РР4" in body["messages"][0]["content"]
    brain.set_game_mode(False)
    assert brain.cfg.model == "test-model"


def test_memory_tools_write_files(make_brain, isolated_memory):
    brain, said, srv = make_brain([openai_reply("", [("remember", {"file": "preferences", "fact": "Любит РР4"})]),
                                   openai_reply("Запомнил.")])
    brain.cfg.quick_commands = False
    brain.handle("запомни что я играю в рр4")
    assert (isolated_memory / "memories" / "preferences.md").read_text(encoding="utf-8").strip() == "- Любит РР4"
    brain.reset()
    brain.handle("привет")  # новый разговор — память в первом сообщении
    first = srv.requests[-1][2]["messages"][1]["content"]
    assert "Любит РР4" in first


def test_reflection_runs_on_reset(make_brain, isolated_memory):
    brain, said, srv = make_brain([openai_reply("Привет."),
                                   openai_reply("", [("remember", {"file": "profile", "fact": "Зовут Дима"})]),
                                   openai_reply("готово")], learning=True)
    brain.cfg.quick_commands = False
    brain.handle("меня зовут Дима")
    brain.shutdown(timeout=10)
    assert "Зовут Дима" in (isolated_memory / "memories" / "profile.md").read_text(encoding="utf-8")


def test_bad_tool_args_are_coerced(make_brain, monkeypatch):
    got = {}
    monkeypatch.setattr(T.ToolBox, "tool_media", lambda self, action, times=1: got.update(a=action, t=times) or "ok")
    brain, said, srv = make_brain([openai_reply("", [("media", {"action": "volume_up", "times": "3", "junk": 1})]),
                                   openai_reply("Громче.")])
    brain.cfg.quick_commands = False
    brain.handle("сделай погромче на чуть-чуть")
    assert got == {"a": "volume_up", "t": 3}
