#!/usr/bin/python3
"""
hermes_hook.py - shell hook Hermesa (v0.19), wołany przy zdarzeniach.
Musi skończyć się w ułamku sekundy: tylko wysyła JSON do daemona i wychodzi.

Rejestracja w ~/.hermes/config.yaml:
  hooks:
    pre_llm_call:   -> początek zadania (opis + start odliczania)
    pre_tool_call:  -> natychmiastowe potwierdzenie ("Otwieram Steam...")
    post_llm_call:  -> koniec zadania; >= próg czasu: telefon dzwoni

Dane (potwierdzone w źródle 0.19, agent/shell_hooks.py):
  stdin: {"hook_event_name", "tool_name", "tool_input", "session_id",
          "cwd", "extra": {...}}
  extra dla pre_llm_call:  user_message, is_first_turn, ...
  extra dla post_llm_call: user_message, assistant_response, ...
Hook NIGDY nie wypisuje nic na stdout (to kontekst dla LLM) i zawsze
kończy się 0 - Hermes działa dalej nawet bez telefonu.
"""

import json
import os
import re
import socket
import sys
import time
from pathlib import Path

import yaml

CONFIG = Path("~/.config/hermes-telefon/config.yaml").expanduser()
STAN = Path("~/.cache/hermes-telefon/biezace_zadanie.json").expanduser()
DZIENNIK = Path("~/.cache/hermes-telefon/hook.log").expanduser()


def wyslij(cfg: dict, wiadomosc: dict):
    """Wyślij do daemona i nie czekaj. Brak daemona = cisza."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(json.dumps(wiadomosc).encode(), cfg["gniazdo"])
    except OSError:
        pass


def potwierdzenie(cfg: dict, komenda: str):
    """Z komendy terminala -> (zdanie, [linia1, linia2]) albo None.

    Szukamy tylko uruchamiania programów (hyprctl exec / plain exec),
    żeby nie gadać przy każdym ls/grep.
    """
    low = komenda.lower()
    if "exec" not in low:
        return None
    program = next((nazwa for slowo, nazwa in cfg["programy"].items()
                    if slowo in low), None)
    if program is None:
        return cfg["zwroty"]["ogolny"], ["Juz robie", ""]
    m = re.search(r"workspace\s+(-?\d+)", low)
    if m:
        nr = int(m.group(1))
        pulpit = cfg["pulpity"].get(nr, f"na pulpicie {nr}")
        zdanie = cfg["zwroty"]["otwieram_na_pulpicie"].format(
            program=program, pulpit=pulpit)
        return zdanie, [f"Otwieram {program}", f"pulpit {nr}"]
    return (cfg["zwroty"]["otwieram"].format(program=program),
            [f"Otwieram {program}", ""])


def krotki_opis(tekst: str, limit=60) -> str:
    linia = tekst.strip().splitlines()[0] if tekst.strip() else ""
    return linia[:limit]


def pierwsze_zdanie(tekst: str, limit=200) -> str:
    zdanie = re.split(r"(?<=[.!?])\s", tekst.strip(), maxsplit=1)[0]
    return zdanie[:limit]


def _block(powod: str):
    """Zablokuj wywolanie narzadzia (pre_tool_call)."""
    print(json.dumps({"action": "block", "message": powod}))
    sys.exit(0)


def pilnuj_okien(dane: dict):
    """Twarda blokada: polecenia glosowe p. Krysi (HERMES_TELEFON_INNER)
    nie moga zamykac ani zabijac okien/terminali szefa."""
    if not os.environ.get("HERMES_TELEFON_INNER"):
        return
    if dane.get("tool_name") not in ("terminal",):
        return
    komenda = str((dane.get("tool_input") or {}).get("command") or "")
    nisk = komenda.lower()
    for w in ("pkill", "killall"):
        if w in nisk:
            _block("Pani Krysia nie zamyka okien ani terminali szefa.")
            return
    if re.search(r"(^|[;&|]\s*)kill(\s|$)", nisk):
        _block("Pani Krysia nie zamyka okien ani terminali szefa.")
        return
    if "hyprctl" in nisk and re.search(r"closewindow|killactive|closeorfocus", nisk):
        _block("Pani Krysia nie zamyka okien ani terminali szefa.")
        return


def main():
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    surowe = sys.stdin.read() if not sys.stdin.isatty() else ""
    DZIENNIK.parent.mkdir(parents=True, exist_ok=True)
    with open(DZIENNIK, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%F %T')} {surowe[:400]}\n")
    try:
        dane = json.loads(surowe) if surowe else {}
    except json.JSONDecodeError:
        dane = {}
    extra = dane.get("extra") or {}

    # twarda blokada: p. Krysia nie zamyka okien/terminali szefa
    if dane.get("hook_event_name") == "pre_tool_call":
        pilnuj_okien(dane)

    # zadania wykonywane WEWNATRZ telefonu (polecenia glosowe p. Krysi):
    # nie dzwonimy do szefa wpol sekundy po tym, jak mu juz odpowiedzielismy
    if os.environ.get("HERMES_TELEFON_INNER"):
        return

    if dane.get("hook_event_name") == "pre_llm_call":
        opis = krotki_opis(str(extra.get("user_message") or ""))
        STAN.write_text(json.dumps({"start": time.time(), "opis": opis}))
        wyslij(cfg, {"typ": "start_zadania", "opis": opis})

    elif dane.get("hook_event_name") == "pre_tool_call":
        if dane.get("tool_name") == "terminal":
            komenda = str((dane.get("tool_input") or {}).get("command")
                          or "")
            wynik = potwierdzenie(cfg, komenda)
            if wynik:
                zdanie, ekran = wynik
                wyslij(cfg, {"typ": "powiedz", "tekst": zdanie,
                             "ekran": ekran})

    elif dane.get("hook_event_name") == "post_llm_call":
        try:
            stan = json.loads(STAN.read_text())
        except (OSError, ValueError):
            stan = None
        wyslij(cfg, {"typ": "koniec_zadania"})
        if not stan:
            return
        czas = time.time() - stan["start"]
        if czas >= cfg["prog_dzwonienia_s"]:
            odpowiedz = str(extra.get("assistant_response") or "")
            tekst = cfg["zwroty"]["koniec_zadania"].format(
                podsumowanie=pierwsze_zdanie(odpowiedz))
            wyslij(cfg, {"typ": "zadzwon", "tekst": tekst,
                         "opis": stan["opis"], "czas": czas})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    # hook nigdy nie blokuje Hermesa: pusty stdout, kod 0
    sys.exit(0)
