"""Голос (speaker), распознавание (stt/whisper.cpp), микрофон (listener)."""
import array
import json
import math
import os
import queue
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

import jarvis.speaker as SP
import jarvis.stt as ST
from jarvis.config import Config


def test_clean_and_split():
    assert SP.clean_for_speech("**Готово**, вот https://x.ru `код`") == "Готово, вот ссылка код"
    parts = SP.split_sentences("Привет. Открыл телеграм, бля. Сейчас в Москве плюс пять, облачно, ветер слабый. "
                               "Напомню через десять минут проверить садок. Что-то ещё?")
    assert len(parts) == 2 and parts[-1].endswith("Что-то ещё?")
    assert SP.split_sentences("Одно") == ["Одно"]


def test_jarvis_effect(tmp_path):
    p = tmp_path / "t.wav"
    rate = 22050
    x = array.array("h", (int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(rate)))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(x.tobytes())
    t = time.time()
    SP.jarvis_effect(p, pitch_factor=0.93)
    assert time.time() - t < 1
    with wave.open(str(p)) as w:
        assert w.getframerate() == int(rate * 0.93) and w.getnframes() == rate
        y = array.array("h", w.readframes(w.getnframes()))
    assert max(abs(v) for v in y) <= 30000  # без перегруза


@pytest.fixture
def speaker():
    sp = SP.Speaker(Config())
    log = []

    def synth(text):
        log.append(("synth", text[:10]))
        time.sleep(0.2)
        return Path("x.mp3")

    def play(path):
        log.append(("play",))
        end = time.time() + 0.4
        while time.time() < end and not sp._stop.is_set():
            time.sleep(0.01)
    sp._synth, sp._play = synth, play
    sp.log = log
    return sp


LONG = ("Первое предложение довольно длинное, чтобы не склеилось с другими никак. Второе предложение тоже весьма "
        "длинное и самостоятельное, да. Третье предложение, которое мы не услышим, потому что перебьём.")


def test_pipeline_prefetches_next_sentence(speaker):
    speaker.say(LONG)
    kinds = [e[0] for e in speaker.log]
    assert kinds == ["synth", "play", "synth", "play", "synth", "play"] or kinds.count("play") == 3
    assert kinds.index("play") < kinds.index("synth", 1)  # второе готовится, пока играет первое


def test_stop_interrupts(speaker):
    threading.Timer(0.35, speaker.stop).start()
    speaker.say(LONG)
    assert [e[0] for e in speaker.log].count("play") == 1


def test_fallback_to_sapi_only_after_three_failures(speaker):
    spoken = []
    speaker._say_sapi = lambda t: spoken.append(t)

    def bad(text):
        raise RuntimeError("нет интернета")
    speaker._synth = bad
    for i in range(3):
        speaker.say("Раз. Два.")
        assert speaker.engine == ("sapi" if i == 2 else "edge")
    assert spoken == ["Раз. Два."] * 3


def test_piper_missing_falls_back_to_edge(monkeypatch):
    monkeypatch.setattr(SP, "find_piper", lambda v: None)
    c = Config()
    c.tts_engine = "piper"
    assert SP.Speaker(c).engine == "edge"


@pytest.mark.skipif(os.name == "nt", reason="подставной piper — shell-скрипт")
def test_piper_engine_with_fake_binary(tmp_path, monkeypatch):
    exe = tmp_path / "piper"
    exe.write_text(f"""#!{sys.executable}
import sys, wave, array
out = sys.argv[sys.argv.index("--output_file") + 1]
text = sys.stdin.buffer.read().decode("utf-8")
assert "Проверка" in text
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(array.array("h", [1000] * 22050).tobytes())
""")
    exe.chmod(0o755)
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"")
    monkeypatch.setattr(SP, "find_piper", lambda v: (exe, voice))
    c = Config()
    c.tts_engine, c.voice_effect = "piper", "jarvis"
    sp = SP.Speaker(c)
    out = sp._synth_piper("Проверка связи.", tmp_path / "o.wav")
    with wave.open(str(out)) as w:
        assert w.getframerate() == int(22050 * 0.93)


# --- whisper.cpp -------------------------------------------------------------

FAKE_SERVER = r'''#!{py}
import sys, json
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv
if "-l" in args and "--no-l" in open(__file__).read():
    sys.exit(2)
port = int(args[args.index("--port") + 1]); n = [0]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        n[0] += 1; self.send_response(503 if n[0] < 3 else 200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        f = {{p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}}
        assert f["file"][:4] == b"RIFF" and f["language"] == b"ru" and f["prompt"]
        out = json.dumps({{"text": {reply!r}}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def whisper_dir(tmp_path, monkeypatch):
    d = tmp_path / "whisper"
    d.mkdir()
    (d / "ggml-small.bin").write_bytes(b"0" * 100)
    monkeypatch.setattr(ST, "WHISPER_DIR", d)

    def install(reply=" Джарвис, открой Телеграм.", extra=""):
        exe = d / "whisper-server"
        exe.write_text(FAKE_SERVER.format(py=sys.executable, reply=reply) + extra)
        exe.chmod(0o755)
    return install


@pytest.mark.skipif(os.name == "nt", reason="подставной сервер — shell-скрипт")
def test_whisper_cpp_end_to_end(whisper_dir):
    whisper_dir()
    c = Config()
    c.whisper_port = _free_port()
    w = ST.WhisperSTT(c)
    assert w._ready.wait(30) and w.backend == "cpp", w.error
    assert w.transcribe(b"\x01\x00" * 16000) == "Джарвис, открой Телеграм."
    assert w.transcribe(b"\x01\x00" * 100) is None  # слишком коротко
    w.close()


@pytest.mark.skipif(os.name == "nt", reason="подставной сервер — shell-скрипт")
def test_whisper_cpp_retries_without_language_flag(whisper_dir):
    whisper_dir(extra="\n# --no-l\n")
    c = Config()
    c.whisper_port = _free_port()
    w = ST.WhisperSTT(c)
    assert w._ready.wait(30) and w.backend == "cpp", w.error
    w.close()


@pytest.mark.skipif(os.name == "nt", reason="подставной сервер — shell-скрипт")
def test_whisper_hallucination_dropped(whisper_dir):
    whisper_dir(reply=" Субтитры сделал DimaTorzok")
    c = Config()
    c.whisper_port = _free_port()
    w = ST.WhisperSTT(c)
    w._ready.wait(30)
    assert w.transcribe(b"\x01\x00" * 16000) is None
    w.close()


def test_whisper_absent_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "WHISPER_DIR", tmp_path / "none")
    c = Config()
    c.whisper_backend = "cpp"
    w = ST.WhisperSTT(c)
    w._ready.wait(5)
    assert not w.ready and w.transcribe(b"\x01\x00" * 16000) is None


# --- listener ------------------------------------------------------------------

def make_listener():
    from jarvis.listener import Listener

    class Rec:
        def Reset(self):
            pass
    L = Listener.__new__(Listener)
    L.audio, L._barge_audio = queue.Queue(), queue.Queue()
    L._mode, L._modes, L._modes_lock = "listen", [], threading.Lock()
    L._barge_reset, L.interrupt_words, L.rec, L._utterance = threading.Event(), ["стоп"], Rec(), bytearray(b"xx")
    return L


def test_listener_modes_nest_and_route():
    L = make_listener()
    L._on_audio(b"a", 1, None, None)
    assert (L.audio.qsize(), L._barge_audio.qsize()) == (1, 0)
    with L.mode("barge"):
        L._on_audio(b"echo", 1, None, None)
        assert L._barge_audio.qsize() == 1 and L._barge_reset.is_set()
        with L.mode("listen"):
            assert L._mode == "listen" and L.audio.qsize() == 0  # своё эхо выкинули
        assert L._mode == "barge"
    assert L._mode == "listen" and len(L._utterance) == 0
    L.interrupt_words = []
    with L.mode("barge"):
        assert L._mode == "mute"


def test_listener_mode_restores_after_exception():
    L = make_listener()
    with pytest.raises(RuntimeError):
        with L.mode("barge"):
            raise RuntimeError
    assert L._mode == "listen"
