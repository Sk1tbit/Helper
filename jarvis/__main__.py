"""Точка входа: python -m jarvis [--text] [--list-mics] [--config путь]."""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time

from .config import load_config

YES_WORDS = ("да", "давай", "подтверждаю", "выполняй", "конечно", "ок", "окей", "делай", "можно", "ага")
STOP_WORDS = ("стоп", "хватит", "отбой", "спасибо", "ничего", "отмена")
EXIT_PHRASES = ("отключись", "выключись", "завершить работу", "заверши работу")
RESET_PHRASES = ("новый разговор", "забудь всё", "забудь все", "начнём сначала", "начнем сначала")
REPEAT_RE = re.compile(r"^ ?(повтори|еще раз|ещё раз|что ты сказал|не расслышал) ?$")
HELP_RE = re.compile(r"^ ?(что ты умеешь|что умеешь|помощь|список команд|что ты можешь) ?$")
HELP_TEXT = ("Открываю программы и сайты, рулю звуком и музыкой, ставлю напоминания, делаю скрины, "
             "выключаю и перезагружаю комп, ищу в интернете и точки в Русской Рыбалке, смотрю на экран "
             "и запоминаю, что ты любишь. Скажи «игровой режим», чтобы я не мешал в игре.")
LAST_SAID = {"text": ""}
LISTEN_ON = "Слушаю без имени. Скажи «стоп», чтобы перестал."
LISTEN_OFF = "Окей, теперь только по имени."


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))).strip()
GAME_ON_RE = re.compile(r"\b(игровой режим|режим игры|включи игровой режим|игровой режим включи)\b")
GAME_OFF_RE = re.compile(r"\b(обычный режим|выключи игровой режим|игровой режим выключи|выйди из игрового режима)\b")


def is_yes(text: str | None) -> bool:
    if not text:
        return False
    words = text.lower().split()
    return any(w in YES_WORDS for w in words) and "нет" not in words


def run_voice(cfg) -> None:
    from .brain import Brain
    from .learning import WakeWords
    from .listener import Listener
    from .speaker import Speaker

    from .hud import Hud

    listener = Listener(cfg)
    speaker = Speaker(cfg)
    hud = Hud(cfg)
    idle = {"state": "wake"}  # что показывать на HUD, когда ничего не происходит
    whisper = None
    if cfg.stt_engine == "whisper":
        from .stt import WhisperSTT

        whisper = WhisperSTT(cfg)  # грузится в фоне, пока не готов — работаем на Vosk

    def hear(timeout: float | None = None) -> str | None:
        """Следующая фраза: Vosk ловит, Whisper (если есть) переслушивает точнее."""
        text = listener.next_utterance(timeout=timeout)
        if text and whisper and whisper.ready:
            better = whisper.transcribe(listener.last_audio)
            if better:
                print(f"[stt] vosk: {text!r} -> whisper: {better!r}")
                return better
        return text

    speak_lock = threading.Lock()  # напоминания приходят из других потоков — не говорим хором

    def say(text: str) -> None:
        if brain_ref and brain_ref[0].interrupted:
            return  # перебили — дальше не болтаем, пока не дадут новую команду
        with speak_lock:
            print(f"{cfg.assistant_name}: {text}")
            hud.jarvis(text)
            hud.state("speak")
            # Пока говорим, микрофон ловит только стоп-слова — чтобы можно было перебить
            # и чтобы не распознавать собственный голос как команду.
            try:
                with listener.mode("barge"):
                    speaker.say(text)
            finally:
                hud.state(idle["state"])

    def notify(text: str) -> None:
        """Напоминания: говорим, даже если перед этим Джарвиса перебили."""
        with speak_lock:
            print(f"{cfg.assistant_name}: {text}")
            hud.jarvis(text)
            with listener.mode("barge"):
                speaker.say(text)

    say = _styled(say, cfg)
    notify = _styled(notify, cfg)

    def confirm(question: str) -> bool:
        say(question + (" Да или нет?" if cfg.swearing else " Скажите да или нет."))
        with listener.mode("listen"):
            answer = hear(timeout=7)
        print(f"Вы: {answer or '(молчание)'}")
        return is_yes(answer)

    brain_ref: list = []
    brain = Brain(cfg, say=say, confirm=confirm, notify=notify)
    brain_ref.append(brain)
    wake = WakeWords(cfg.wake_words)
    state = {"interrupted_at": 0.0}

    def on_interrupt(word: str) -> None:
        speaker.stop()
        brain.interrupt()
        state["interrupted_at"] = time.time()

    listener.on_interrupt = on_interrupt
    listener.start()
    print(f"Скажите «{cfg.assistant_name}» и вашу просьбу (или «слушай» — слушать без имени). Ctrl+C — выход.")
    problem = brain.check()
    if problem:
        print(f"[!] {problem}")
        say("Внимание: не могу достучаться до нейросети. Подробности в консоли.")
    else:
        say(f"{cfg.assistant_name} на связи, бля. Чё делаем?" if cfg.swearing else f"{cfg.assistant_name} на связи.")

    on_phrases = {_norm(p) for p in cfg.listen_on_phrases}
    off_phrases = {_norm(p) for p in cfg.listen_off_phrases}
    always = bool(cfg.start_listening)   # режим «слушай»: каждая фраза — команда
    follow_until = 0.0                    # до этого момента слушаем продолжение без имени
    last_command = time.time()
    if always:
        say(LISTEN_ON)

    def run(command: str) -> None:
        # Пока выполняем, тоже можно перебить «стопом».
        hud.state("think")
        prev = idle["state"]
        idle["state"] = "think"  # если по ходу заговорит — потом вернёмся в «думаю»
        try:
            with listener.mode("barge"):
                if not _handle_local(command, brain, say):
                    brain.handle(command)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001 — что бы ни случилось, Джарвис должен слушать дальше
            import traceback

            traceback.print_exc()
            brain.clear_interrupt()
            say("Что-то пошло не так. Подробности в консоли.")
        finally:
            idle["state"] = "game" if brain.game_mode else ("listen" if always else prev if prev != "think" else "wake")
            hud.state(idle["state"])

    try:
        while True:
            heard = listener.next_utterance(timeout=60)
            now = time.time()
            if heard is None:
                brain.reset_if_idle()
                if always and cfg.always_listen_minutes and now - last_command > cfg.always_listen_minutes * 60:
                    always = False
                    idle["state"] = "wake"
                    say(LISTEN_OFF)
                continue

            wake_cmd = wake.strip(heard)
            just_interrupted = now - state["interrupted_at"] < 8
            in_follow_up = now < follow_until
            plain = _norm(heard)
            addressed = wake_cmd is not None or always or just_interrupted or in_follow_up or plain in on_phrases
            if not addressed:
                continue
            brain.clear_interrupt()  # новая фраза к нам — снова можно говорить

            # Имя и стоп-слова ловит Vosk, а смысл фразы переслушиваем Whisper'ом.
            text = heard
            if whisper and whisper.ready:
                better = whisper.transcribe(listener.last_audio)
                if better:
                    print(f"[stt] vosk: {heard!r} -> whisper: {better!r}")
                    text = better
            command = wake.strip(text)
            if command is None:
                if wake_cmd is not None and len(text.split()) > 1:
                    command = " ".join(text.split()[1:])  # Whisper написал имя по-своему
                elif wake_cmd is not None:
                    command = ""
                else:
                    command = text
            command = command.strip(" ,.!?")
            print(f"Вы: {text}")
            hud.user(text)
            low = _norm(command) or plain

            if low in on_phrases:
                always = True
                idle["state"] = "listen"
                last_command = now
                say(LISTEN_ON)
                continue
            if always and low in off_phrases:
                always = False
                idle["state"] = "game" if brain.game_mode else "wake"
                follow_until = 0
                say(LISTEN_OFF)
                continue
            if not always and in_follow_up and wake_cmd is None and low in STOP_WORDS:
                follow_until = 0
                say("Хорошо.")
                continue
            if not command:
                if cfg.wake_beep:
                    from .speaker import beep

                    beep()  # быстрее, чем говорить «Слушаю»
                else:
                    say("Слушаю.")
                follow_until = time.time() + 8
                continue
            if always and len(low) < 2:
                continue  # шорох и обрывки в режиме «слушай» не считаем командами

            run(command)
            last_command = time.time()
            follow_until = 0 if always else time.time() + max(0, cfg.follow_up_seconds)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        listener.stop()
        print("Запоминаю, что узнал за сессию...")
        brain.shutdown()


def _styled(say, cfg):
    """Обёртка над say: готовые фразы в выбранном стиле (с матом или без)."""
    from .persona import phrase

    def styled(text: str):
        text = phrase(text, cfg.swearing)
        LAST_SAID["text"] = text
        return say(text)

    return styled


def _handle_local(command: str, brain, say) -> bool:
    """Команды, для которых не нужна нейросеть. True — если команда обработана."""
    low = " " + re.sub(r"[^\w\s]", "", command.lower()) + " "
    if any(f" {p} " in low for p in EXIT_PHRASES) and not re.search(r"комп|пк|ноут|систем", low):
        say("Отключаюсь. Хорошего дня.")
        raise SystemExit
    if any(f" {p} " in low for p in RESET_PHRASES):
        brain.reset()
        say("Начинаем с чистого листа.")
        return True
    if REPEAT_RE.match(low) and LAST_SAID["text"]:
        say(LAST_SAID["text"])
        return True
    if HELP_RE.search(low):
        say(HELP_TEXT + (" Короче, дохуя чего." if getattr(brain, "cfg", None) and brain.cfg.swearing else ""))
        return True
    if GAME_OFF_RE.search(low):
        say(brain.set_game_mode(False))
        return True
    if GAME_ON_RE.search(low):
        say(brain.set_game_mode(True))
        return True
    return False


def run_text(cfg) -> None:
    from .brain import Brain

    def say(text: str) -> None:
        print(f"{cfg.assistant_name}: {text}")

    def confirm(question: str) -> bool:
        return is_yes(input(f"{cfg.assistant_name}: {question} [да/нет] > "))

    say = _styled(say, cfg)

    def notify(text: str) -> None:
        text = _styled(lambda t: t, cfg)(text)
        print(f"\n{cfg.assistant_name}: {text}\nВы: ", end="", flush=True)

    brain = Brain(cfg, say=say, confirm=confirm, notify=notify)
    problem = brain.check()
    if problem:
        print(f"[!] {problem}")
    print("Текстовый режим. Пиши просьбы, пустая строка или Ctrl+C — выход.")
    try:
        while True:
            text = input("Вы: ").strip()
            if not text:
                break
            if not _handle_local(text, brain, say):
                brain.handle(text)
    except (KeyboardInterrupt, EOFError, SystemExit):
        pass
    print("Запоминаю, что узнал за сессию...")
    brain.shutdown()


class _Tee:
    """Пишет всё, что выводится в консоль, ещё и в logs/jarvis.log — чтобы было что прислать при баге."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, data):
        try:
            self.stream.write(data)
        except UnicodeEncodeError:
            self.stream.write(data.encode("ascii", "replace").decode())
        try:
            self.log.write(data)
            self.log.flush()
        except (OSError, ValueError):
            pass

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _setup_log() -> None:
    from datetime import datetime

    from .config import ROOT

    logs = ROOT / "logs"
    try:
        logs.mkdir(exist_ok=True)
        path = logs / "jarvis.log"
        if path.exists() and path.stat().st_size > 5_000_000:  # не даём логу разрастись
            path.replace(logs / "jarvis.old.log")
        log = open(path, "a", encoding="utf-8")
    except OSError:
        return
    log.write(f"\n===== {datetime.now():%d.%m.%Y %H:%M:%S} =====\n")
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # кириллица в консоли Windows
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="jarvis", description="Голосовой помощник для ПК")
    parser.add_argument("--text", action="store_true", help="текстовый режим без микрофона и голоса")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны и выйти")
    parser.add_argument("--doctor", action="store_true", help="проверить нейросеть, микрофон, Whisper и голос")
    parser.add_argument("--config", help="путь к config.yaml")
    args = parser.parse_args()
    _setup_log()

    if args.doctor:
        from .doctor import run_doctor

        run_doctor(load_config(args.config))
        return

    if args.list_mics:
        from .listener import list_microphones

        list_microphones()
        return

    cfg = load_config(args.config)
    if args.text:
        run_text(cfg)
    else:
        run_voice(cfg)


if __name__ == "__main__":
    main()
