from pathlib import Path

from jarvis.config import Config, load_config, parse_simple_yaml

ROOT = Path(__file__).resolve().parent.parent


def test_parse_values():
    data = parse_simple_yaml(
        'a: 1\nb: 2.5\nc: true\nd: no\ne: null\nf: ""\ng: "x # y"  # коммент\n'
        "h: [один, два,  три]\ni: []\nurl: http://localhost:1234\nname: Джарвис\n# только коммент\n"
    )
    assert data == {"a": 1, "b": 2.5, "c": True, "d": False, "e": None, "f": "", "g": "x # y",
                    "h": ["один", "два", "три"], "i": [], "url": "http://localhost:1234", "name": "Джарвис"}


def test_example_config_loads_and_matches_defaults_types():
    cfg = load_config(ROOT / "config.example.yaml")
    default = Config()
    for field, value in vars(default).items():
        got = getattr(cfg, field)
        if value is not None and got is not None:
            assert isinstance(got, type(value)) or (isinstance(value, float) and isinstance(got, int)), field


def test_example_matches_pyyaml_if_available():
    yaml = __import__("pytest").importorskip("yaml")
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert parse_simple_yaml(text) == yaml.safe_load(text)


def test_missing_config_gives_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.model and cfg.wake_words and cfg.vision_model == "llama3.2-vision"


def test_bom_and_case(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("﻿wake_words: [ДЖАРВИС]\nvision_model: \"\"\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.wake_words == ["джарвис"] and cfg.vision_model == ""
