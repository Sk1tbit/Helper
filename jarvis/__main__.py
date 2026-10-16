"""Точка входа: python -m jarvis [--text] [--list-mics] [--config путь]."""

from __future__ import annotations

import argparse
import re
import sys

from .config import load_config

YES_WORDS = ("да", "давай", "подтверждаю", "выполняй", "конечно", "ок", "окей", "делай", "можно", "ага")
STOP_WORDS = ("стоп", "хватит", "отбой", "спасибо", "ничего", "отмена")
EXIT_PHRASES = ("отключись", "выключись", "завершить работу", "заверши работу")
RESET_PHRASES = ("новый разговор", "забудь всё", "забудь все", "начнём сначала", "начнем сначала")


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

    def say(text: str) -> None:
        print(f"{cfg.assistant_name}: {text}")
        listener.mute()
        try:
            speaker.say(text)
        finally:
            listener.unmute()

    def confirm(question: str) -> bool:
        say(question + " Скажите да или нет.")
        answer = listener.next_utterance(timeout=7)
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
        say(f"{cfg.assistant_name} на связи.")

    try:
        while True:
            heard = listener.next_utterance(timeout=60)
            if heard is None:
                brain.reset_if_idle()
                continue
            command = wake.strip(heard)
            if command is None:
                continue
            print(f"Вы: {heard}")
            if not command:
                say("Слушаю.")
                command = listener.next_utterance(timeout=8)
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
                follow = listener.next_utterance(timeout=cfg.follow_up_seconds)
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


def _handle_local(command: str, brain, say) -> bool:
    """Команды, для которых не нужна нейросеть. True — если команда обработана."""
    low = " " + re.sub(r"[^\w\s]", "", command.lower()) + " "
    if any(f" {p} " in low for p in EXIT_PHRASES):
        say("Отключаюсь. Хорошего дня.")
        raise SystemExit
    if any(f" {p} " in low for p in RESET_PHRASES):
        brain.reset()
        say("Начинаем с чистого листа.")
        return True
    return False


def run_text(cfg) -> None:
    from .brain import Brain

    def say(text: str) -> None:
        print(f"{cfg.assistant_name}: {text}")

    def confirm(question: str) -> bool:
        return is_yes(input(f"{cfg.assistant_name}: {question} [да/нет] > "))

    brain = Brain(cfg, say=say, confirm=confirm)
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
