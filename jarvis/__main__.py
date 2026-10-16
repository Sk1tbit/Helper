"""Точка входа: python -m jarvis [--text] [--list-mics] [--config путь]."""

from __future__ import annotations

import argparse
import re
import sys
import threading

from .config import load_config

YES_WORDS = ("да", "давай", "подтверждаю", "выполняй", "конечно", "ок", "окей", "делай", "можно", "ага")
STOP_WORDS = ("стоп", "хватит", "отбой", "спасибо", "ничего", "отмена")
EXIT_PHRASES = ("отключись", "выключись", "завершить работу", "заверши работу")
RESET_PHRASES = ("новый разговор", "забудь всё", "забудь все", "начнём сначала", "начнем сначала")
REPEAT_RE = re.compile(r"^ ?(повтори|еще раз|ещё раз|что ты сказал|не расслышал) ?$")
HELP_RE = re.compile(r"\b(что ты умеешь|что умеешь|помощь|список команд)\b")
HELP_TEXT = ("Открываю программы и сайты, рулю звуком и музыкой, ставлю напоминания, делаю скрины, "
             "выключаю и перезагружаю комп, ищу в интернете и точки в Русской Рыбалке, смотрю на экран "
             "и запоминаю, что ты любишь. Скажи «игровой режим», чтобы я не мешал в игре.")
LAST_SAID = {"text": ""}
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

    listener = Listener(cfg)
    speaker = Speaker(cfg)
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
        with speak_lock:
            print(f"{cfg.assistant_name}: {text}")
            listener.mute()
            try:
                speaker.say(text)
            finally:
                listener.unmute()

    say = _styled(say, cfg)

    def confirm(question: str) -> bool:
        say(question + (" Да или нет?" if cfg.swearing else " Скажите да или нет."))
        answer = hear(timeout=7)
        print(f"Вы: {answer or '(молчание)'}")
        return is_yes(answer)

    brain = Brain(cfg, say=say, confirm=confirm)
    wake = WakeWords(cfg.wake_words)
    listener.start()
    print(f"Скажите «{cfg.assistant_name}» и вашу просьбу. Ctrl+C — выход.")
    problem = brain.check()
    if problem:
        print(f"[!] {problem}")
        say("Внимание: не могу достучаться до нейросети. Подробности в консоли.")
    else:
        say(f"{cfg.assistant_name} на связи, бля. Чё делаем?" if cfg.swearing else f"{cfg.assistant_name} на связи.")

    try:
        while True:
            heard = listener.next_utterance(timeout=60)
            if heard is None:
                brain.reset_if_idle()
                continue
            command = wake.strip(heard)
            if command is None:
                continue
            if whisper and whisper.ready:
                # Имя поймал Vosk, а саму команду переслушиваем Whisper'ом.
                better = whisper.transcribe(listener.last_audio)
                if better:
                    better_cmd = wake.strip(better)
                    if better_cmd is None:  # Whisper написал имя иначе — отрезаем первое слово
                        better_cmd = " ".join(better.split()[1:]) if len(better.split()) > 1 else ""
                    print(f"[stt] vosk: {heard!r} -> whisper: {better!r}")
                    heard, command = better, better_cmd.strip(" ,.!?")
            print(f"Вы: {heard}")
            if not command:
                say("Слушаю.")
                command = hear(timeout=8)
                if not command:
                    continue
                print(f"Вы: {command}")

            # Диалог: после ответа какое-то время слушаем продолжение без имени.
            while command:
                if _handle_local(command, brain, say):
                    break
                brain.handle(command)
                if cfg.follow_up_seconds <= 0:
                    break
                follow = hear(timeout=cfg.follow_up_seconds)
                if not follow:
                    break
                print(f"Вы: {follow}")
                after_wake = wake.strip(follow)
                command = after_wake if after_wake is not None else follow
                if any(command.lower().strip(" .,!") == w for w in STOP_WORDS):
                    say("Хорошо.")
                    break
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


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # кириллица в консоли Windows
    parser = argparse.ArgumentParser(prog="jarvis", description="Голосовой помощник для ПК")
    parser.add_argument("--text", action="store_true", help="текстовый режим без микрофона и голоса")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны и выйти")
    parser.add_argument("--config", help="путь к config.yaml")
    args = parser.parse_args()

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
