#!/usr/bin/env python3
"""cx300_test_ekranu.py - 5 krotkich testow ekranu CX300, po kazdym jedno pytanie.

Na koncu wypisuje gotowe ustawienia do config.yaml (sekcja ekran).

UZYCIE (daemon musi byc zatrzymany, bo to on trzyma telefon):
    systemctl --user stop hermes-telefon.service
    ~/.venvs/hermes-telefon/bin/python cx300_test_ekranu.py
    systemctl --user start hermes-telefon.service

Zapis ramek jest identyczny jak w telefon_daemon.py (wzor tSIP).
"""
import sys
import threading
import time

import hid

VENDOR, PRODUCT = 0x095D, 0x9201
MAKS = 40

dev = hid.device()
dev.open(VENDOR, PRODUCT)
stop = threading.Event()


def write(data: bytes):
    if dev.write(bytes(data)) < 0:
        raise OSError(dev.error())


def ramki(tekst: str):
    tekst = tekst or " "
    n = -(-len(tekst) // 8)
    for i in range(n):
        body = tekst[i * 8:(i + 1) * 8].encode("utf-16-le")
        body += b"\x00" * (16 - len(body))
        write(b"\x15" + (b"\x80" if i == n - 1 else b"\x00") + body)


def ekran(l1: str, l2: str, czysc=False):
    if czysc:
        write(b"\x13\x00")
    write(b"\x13\x15")
    write(b"\x14\x05\x80")
    ramki(l1)
    write(b"\x14\x0A\x80")
    ramki(l2)


def keepalive():
    while not stop.wait(10):
        dev.send_feature_report(b"\x17\x09\x04\x01\x02")


def pytaj(pytanie: str) -> str:
    return input(f"\n>>> {pytanie}\n    odpowiedz: ").strip().lower()


def liczba(pytanie: str) -> int:
    while True:
        o = pytaj(pytanie)
        if o.isdigit():
            return int(o)
        print("    wpisz sama liczbe")


dev.send_feature_report(b"\x17\x09\x04\x01\x02")
threading.Thread(target=keepalive, daemon=True).start()
wyniki = {}

# 1. Linijka z cyframi: ile znakow miesci linia
linijka = "".join(str(i % 10) for i in range(1, MAKS + 1))
dziesiatki = "".join(str(i // 10) if i % 10 == 0 else "." for i in range(1, MAKS + 1))
ekran(linijka, dziesiatki, czysc=True)
print("TEST 1: gorna linia = cyfry 1234567890123..., dolna = kropki i numer "
      "dziesiatki (1 pod 10. znakiem, 2 pod 20. itd.).")
wyniki["cyfry"] = liczba("Ile znakow widac w GORNEJ linii, zanim tekst znika "
                         "albo zawija? (policz z pomoca dolnej linii)")

# 2. Szerokie i waskie litery: czy czcionka ma stala szerokosc
ekran("M" * MAKS, "i" * MAKS, czysc=True)
print("\nTEST 2: gorna linia = same 'M', dolna = same 'i'.")
o = pytaj("Czy obie linie koncza sie w tym samym miejscu ekranu? (t/n)")
wyniki["stala_czcionka"] = o.startswith("t")
if not wyniki["stala_czcionka"]:
    wyniki["m"] = liczba("Ile liter M miesci sie w gornej linii?")

# 3. Polskie litery
ekran("ąćęłńóśźż", "ĄĆĘŁŃÓŚŹŻ", czysc=True)
print("\nTEST 3: polskie litery.")
wyniki["polskie"] = pytaj("Czy widac poprawnie ąćęłńóśźż i wielkie "
                          "ĄĆĘŁŃÓŚŹŻ? (t/n)").startswith("t")

# 4. Resztki bez czyszczenia: dlugi tekst, potem krotki bez 13 00
ekran("DLUGI TEKST ABCDEFGH", "DRUGA LINIA ABCDEFG", czysc=True)
time.sleep(1.5)
ekran("krotki", "xy", czysc=False)
print("\nTEST 4: najpierw byl dlugi tekst, teraz krotki BEZ czyszczenia ekranu.")
wyniki["resztki"] = pytaj("Czy za 'krotki' albo 'xy' zostaly resztki starego "
                          "tekstu (np. ABCDEFGH)? (t/n)").startswith("t")

# 5. Migotanie: licznik co 0.5 s, raz bez czyszczenia, raz z czyszczeniem
print("\nTEST 5: przez 5 s licznik BEZ czyszczenia, potem 5 s Z czyszczeniem.")
for czysc, nazwa in ((False, "bez czyszczenia"), (True, "z czyszczeniem")):
    koniec = time.time() + 5
    i = 0
    while time.time() < koniec:
        ekran(f"Licznik {i}", nazwa, czysc=czysc)
        i += 1
        time.sleep(0.5)
wyniki["miga_z"] = pytaj("Czy wersja 'z czyszczeniem' migotala wyraznie "
                         "bardziej? (t/n)").startswith("t")

ekran("Test skonczony", "", czysc=True)
stop.set()

# --- zalecenia
szer = wyniki["cyfry"] if wyniki["stala_czcionka"] else min(wyniki["cyfry"], wyniki["m"])
czyszczenie = "auto" if wyniki["resztki"] else "nigdy"
print("\n" + "=" * 60)
print("Wklej do ~/.config/hermes-telefon/config.yaml (sekcja ekran):\n")
print(f"  szerokosc: {szer}")
print(f"  polskie_znaki: {'true' if wyniki['polskie'] else 'false'}")
print(f"  czyszczenie: {czyszczenie}")
if not wyniki["stala_czcionka"]:
    print("\nUWAGA: czcionka ma rozna szerokosc liter. Szerokosc ustawiona wg "
          "najszerszej litery (M), wiec zwykly tekst zawsze sie zmiesci, ale "
          "zostanie troche miejsca po prawej.")
if not wyniki["miga_z"] and czyszczenie == "auto":
    print("\nCzyszczenie nie migocze, wiec 'auto' jest bezpieczne.")
print("\nPelne wyniki:", wyniki)
sys.exit(0)
