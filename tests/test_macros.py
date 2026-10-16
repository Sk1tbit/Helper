import pytest

from jarvis.macros import Macros, parse_define


@pytest.mark.parametrize("text,expected", [
    ("Запомни команду «погнали»: открой Дискорд, открой Стим, громкость 30.",
     ("погнали", ["открой дискорд", "открой стим", "громкость 30"])),
    ("запомни команду погнали открой дискорд и открой стим громкость тридцать",
     ("погнали", ["открой дискорд", "открой стим", "громкость тридцать"])),
    ("Запомни команду рабочий режим, открой почту, открой VS Code.", ("рабочий режим", ["открой почту", "открой vs code"])),
    ("создай команду спать выключи комп через полчаса", ("спать", ["выключи комп через полчаса"])),
    ("запомни команду когда я говорю отбой сверни все окна и сделай тише", ("отбой", ["сверни все окна", "сделай тише"])),
    ("запомни команду привет", None), ("открой стим", None),
])
def test_parse_define(text, expected):
    assert parse_define(text) == expected


def test_store_find_delete(isolated_memory):
    m = Macros()
    assert "Запомнил" in m.handle_management("запомни команду погнали открой стим")
    assert Macros().find("Погнали!") == ("погнали", ["открой стим"])  # сохранилось на диск
    assert Macros().find("давай погнали")
    assert Macros().find("погнали потом") is None
    assert "Удалил" in m.handle_management("удали команду погнали")
    assert Macros().find("погнали") is None
    assert m.handle_management("открой стим") is None
