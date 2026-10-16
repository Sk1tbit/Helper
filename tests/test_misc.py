import json
import sys
from pathlib import Path

import pytest

from conftest import openai_reply
from jarvis.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_doctor_reports_tool_support(lmstudio, capsys, monkeypatch):
    import jarvis.doctor as D
    srv = lmstudio([openai_reply("", [("ping", {"text": "привет"})]), openai_reply("Не буду.")], models=("m",))
    c = Config()
    c.llm_backend, c.llm_url, c.model, c.vision_model, c.stt_engine = "openai", srv.url, "m", "", "vosk"
    monkeypatch.setattr(D, "_mic", lambda cfg: None)
    monkeypatch.setattr(D, "_voice", lambda cfg: None)
    D.run_doctor(c)
    out = capsys.readouterr().out
    assert "[OK] Модель умеет вызывать инструменты" in out
    D._llm(c)
    assert "НЕ вызвала инструмент" in capsys.readouterr().out


def test_doctor_llm_down(capsys):
    import jarvis.doctor as D
    c = Config()
    c.llm_url = "http://127.0.0.1:1"
    D._llm(c)
    assert "[!!] Нейросеть" in capsys.readouterr().out


def test_setup_whisper_picks_windows_asset(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import setup_whisper as SW
    releases = [{"tag_name": "v9", "assets": [{"name": "whisper-v9-src.zip", "browser_download_url": "x"}]},
                {"tag_name": "v8", "assets": [{"name": "whisper-bin-x64.zip", "browser_download_url": "cpu"},
                                               {"name": "whisper-cublas-12.4.0-bin-x64.zip", "browser_download_url": "gpu"}]}]

    class R:
        def read(self, *a):
            return json.dumps(releases).encode()

    monkeypatch.setattr(SW.urllib.request, "urlopen", lambda req: R())
    monkeypatch.setattr(SW.json, "load", lambda r: releases)
    assert SW.pick_asset(False) == ("v8", "cpu") and SW.pick_asset(True) == ("v8", "gpu")
    assert set(SW.MODELS) == {"tiny", "base", "small", "medium", "turbo"}


def test_setup_whisper_falls_back_when_api_down(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import setup_whisper as SW

    def boom(req):
        raise OSError("403")
    monkeypatch.setattr(SW.urllib.request, "urlopen", boom)
    tag, url = SW.pick_asset(False)
    assert tag == "v1.8.2" and url.endswith("/v1.8.2/whisper-bin-x64.zip")
    assert SW.pick_asset(True)[1].endswith("whisper-cublas-12.4.0-bin-x64.zip")


def test_setup_piper_urls():
    sys.path.insert(0, str(ROOT / "scripts"))
    import setup_piper as SPp
    assert SPp.VOICE_URL.format(name="dmitri").endswith("/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx")


@pytest.mark.parametrize("name", ["install.bat", "run.bat", "update.bat", "autostart.bat"])
def test_batch_files_are_ascii_crlf(name):
    data = (ROOT / name).read_bytes()
    assert all(b < 128 for b in data), "кириллица в .bat ломает cmd"
    assert b"\r\n" in data and data.count(b"\n") == data.count(b"\r\n")


def test_requirements_have_no_py315_blockers():
    req = (ROOT / "requirements.txt").read_text()
    for bad in ("PyYAML", "faster-whisper", "numpy", "anthropic"):
        assert bad not in req
    assert 'pygame>=2.5; sys_platform != "win32"' in req


def test_gitignore_keeps_private_stuff_out():
    gi = (ROOT / ".gitignore").read_text()
    for item in ("config.yaml", "memory/", "models/", "tools/", "logs/", ".venv/"):
        assert item in gi
