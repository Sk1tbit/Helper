"""Голосовой цикл целиком — с подставным микрофоном, голосом и мозгом."""
from contextlib import contextmanager

import pytest

import jarvis.__main__ as M
from jarvis.config import Config


class FakeListener:
    script: list = []

    def __init__(self, cfg):
        self.last_audio, self.on_interrupt, self.modes = b"", None, []

    def start(self):
        pass

    def stop(self):
        pass

    @contextmanager
    def mode(self, m):
        self.modes.append(m)
        yield

    def next_utterance(self, timeout=None):
        item = self.script.pop(0)
        if item == "EXIT":
            raise KeyboardInterrupt
        if item == "INTERRUPT":
            self.on_interrupt("стоп")
            return None
        if item == "CRASH":
            return "сломайся"
        return item


class FakeSpeaker:
    def __init__(self, cfg):
        self.spoken = []

    def say(self, t):
        FakeSpeaker.all.append(t)

    def stop(self):
        FakeSpeaker.stopped += 1


class FakeBrain:
    game_mode = False

    def __init__(self, cfg, say, confirm, notify=None):
        self.cfg, self.say, self.notify, self._i = cfg, say, notify, False
        FakeBrain.inst = self
        self.handled = []

    def check(self):
        return None

    def handle(self, t):
        if t == "сломайся":
            raise KeyError("boom")
        self.handled.append(t)
        self.say(f"ok: {t}")

    def interrupt(self):
        self._i = True

    def clear_interrupt(self):
        self._i = False

    @property
    def interrupted(self):
        return self._i

    def reset_if_idle(self):
        pass

    def shutdown(self):
        pass

    def reset(self):
        pass

    def set_game_mode(self, on):
        self.game_mode = on
        return "Игровой режим включён. Удачной игры." if on else "Игровой режим выключен."


@pytest.fixture
def voice(monkeypatch):
    import jarvis.brain as B
    import jarvis.listener as LI
    import jarvis.speaker as SP
    monkeypatch.setattr(LI, "Listener", FakeListener)
    monkeypatch.setattr(SP, "Speaker", FakeSpeaker)
    monkeypatch.setattr(SP, "beep", lambda: FakeSpeaker.all.append("*beep*"))
    monkeypatch.setattr(B, "Brain", FakeBrain)
    FakeSpeaker.all, FakeSpeaker.stopped = [], 0

    def run(script, **over):
        FakeListener.script = list(script) + ["EXIT"]
        c = Config()
        c.stt_engine, c.follow_up_seconds, c.hud = "vosk", 0, False
        for k, v in over.items():
            setattr(c, k, v)
        M.run_voice(c)
        return FakeBrain.inst.handled, FakeSpeaker.all
    return run


def test_wake_word_required(voice):
    handled, said = voice(["привет как дела", "джарвис открой телеграм", "громче"])
    assert handled == ["открой телеграм"]


def test_listen_mode(voice):
    handled, said = voice(["слушай", "громче", "сделай тише", "стоп", "громче", "джарвис открой стим"])
    assert handled == ["громче", "сделай тише", "открой стим"]
    assert "Слушаю без имени. Скажи «стоп», чтобы перестал." in said


def test_start_listening_option(voice):
    handled, _ = voice(["громче"], start_listening=True)
    assert handled == ["громче"]


def test_bare_name_beeps_then_takes_command(voice):
    handled, said = voice(["джарвис", "открой стим"])
    assert "*beep*" in said and handled == ["открой стим"]


def test_follow_up_window(voice):
    handled, _ = voice(["джарвис громче", "ещё громче"], follow_up_seconds=6)
    assert handled == ["громче", "ещё громче"]


def test_interrupt_then_command_without_name(voice):
    handled, _ = voice(["джарвис открой стим", "INTERRUPT", "открой дискорд"])
    assert handled == ["открой стим", "открой дискорд"] and FakeSpeaker.stopped == 1


def test_crash_in_handler_keeps_listening(voice):
    handled, said = voice(["джарвис CRASH".replace("CRASH", "сломайся"), "джарвис открой стим"])
    assert handled == ["открой стим"] and "Что-то пошло не так. Подробности в консоли." in said


def test_reminder_speaks_even_after_interrupt(voice):
    handled, said = voice(["джарвис открой стим", "INTERRUPT"])
    FakeBrain.inst.notify("Напоминаю: проверить садок")
    assert said[-1] == "Напоминаю: проверить садок"


def test_exit_phrase(voice):
    handled, said = voice(["джарвис отключись", "джарвис открой стим"])
    assert handled == [] and said[-1] == "Отключаюсь. Хорошего дня."


def test_game_mode_local(voice):
    handled, said = voice(["джарвис игровой режим", "джарвис обычный режим"])
    assert handled == [] and "Игровой режим включён. Удачной игры." in said and said[-1] == "Игровой режим выключен."


# --- локальные команды -----------------------------------------------------------

class B:
    cfg = Config()

    def reset(self):
        self.was_reset = True

    def set_game_mode(self, on):
        return f"game {on}"


def test_handle_local_rules():
    out = []
    say = M._styled(out.append, Config())
    b = B()
    assert M._handle_local("что ты умеешь", b, say) and "Открываю программы" in out[-1]
    assert not M._handle_local("мне нужна помощь с задачей по физике", b, say)  # не перехватываем
    say("Готово.")
    assert M._handle_local("повтори", b, say) and out[-1] == "Готово."
    assert not M._handle_local("повтори мне песню", b, say)
    assert M._handle_local("новый разговор", b, say) and b.was_reset
    assert not M._handle_local("заверши работу компьютера", b, say)  # это выключение компа, а не выход
    with pytest.raises(SystemExit):
        M._handle_local("джарвис отключись", b, say)
    assert M._handle_local("выключи игровой режим", b, say) and out[-1] == "game False"


def test_is_yes():
    assert M.is_yes("да давай") and M.is_yes("Ок") and not M.is_yes("нет не надо") and not M.is_yes(None)


def test_swearing_phrases():
    c = Config()
    c.swearing = True
    out = []
    say = M._styled(out.append, c)
    say("Сделал громче.")
    say("Напоминаю: садок")
    assert out[0] != "Сделал громче." and out[1].startswith("Эй, бля")
    c.swearing = False
    say("Сделал громче.")
    assert out[-1] == "Сделал громче."
