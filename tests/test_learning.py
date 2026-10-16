import pytest

import jarvis.learning as L


def test_remember_forget_snapshot(isolated_memory):
    assert L.remember("preferences", "  Музыка: Яндекс  ") == "Записано в preferences"
    assert L.remember("preferences.md", "музыка: яндекс") == "Это уже есть в памяти"
    L.remember("profile", "Зовут Дима")
    snap = L.memory_snapshot()
    assert "[profile]\n- Зовут Дима" in snap and "[preferences]\n- Музыка: Яндекс" in snap
    assert L.forget("preferences", "ЯНДЕКС") == "Удалено строк: 1"
    assert L.forget("pc", "x") == "В этом файле пока ничего нет"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "profile/../x", "секреты", ""])
def test_memory_path_is_locked_down(bad):
    with pytest.raises(ValueError):
        L.remember(bad, "x")


def test_empty_fact():
    with pytest.raises(ValueError):
        L.remember("profile", "  - ")


def test_snapshot_limit(isolated_memory):
    for i in range(400):
        L.remember("pc", f"факт номер {i} " + "x" * 20)
    snap = L.memory_snapshot()
    assert len(snap) <= L.SNAPSHOT_LIMIT + 5 and "факт номер 399" in snap  # обрезаем старое, свежее остаётся


def test_transcript():
    msgs = [{"role": "user", "content": "<memory>x</memory>\n[01.01] открой стим"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "open_app", "arguments": {"name": "стим"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "open_app", "content": "Запущено"},
            {"role": "assistant", "content": "Открыл.", "tool_calls": []}]
    t = L.transcript(msgs, "Джарвис")
    assert "Пользователь: [01.01] открой стим" in t and "[вызов open_app" in t and "Джарвис: Открыл." in t
    assert "<memory>" not in t


def test_wake_words_learn(isolated_memory):
    w = L.WakeWords(["джарвис"])
    assert w.strip("джарвис открой стим") == "открой стим"
    assert w.strip("Джарвиса, включи") == "включи"
    assert w.strip("привет как дела") is None
    assert w.strip("гарвис громче") == "громче"
    assert "гарвис" not in w.learned
    w.strip("гарвис тише")
    assert "гарвис" in L.WakeWords(["джарвис"]).learned  # сохранилось на диск после двух раз
