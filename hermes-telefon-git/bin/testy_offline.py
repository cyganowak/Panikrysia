#!/usr/bin/env python3
"""testy_offline.py - sprawdza logike daemona BEZ telefonu (udawany HID).

Uruchom:  python3 testy_offline.py      (w tym samym folderze co telefon_daemon.py)
Nie dotyka telefonu, dzwieku ani Hermesa. Wynik: OK albo opis bledu.
"""
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path


# ---------------------------------------------------------------- udawany HID
class UdawanyHid:
    def __init__(self):
        self.zapisy = []
        self.raporty = []
        self.awaria = False

    def open_path(self, p):
        pass

    def set_nonblocking(self, v):
        pass

    def write(self, data):
        if self.awaria:
            return -1
        self.zapisy.append(bytes(data))
        return len(data)

    def error(self):
        return "udawana awaria"

    def send_feature_report(self, d):
        if self.awaria:
            raise OSError("awaria")

    def read(self, n, timeout_ms=0):
        if self.raporty:
            return self.raporty.pop(0)
        raise KeyboardInterrupt           # koniec testowej petli czytania

    def close(self):
        pass


URZADZENIA = []
hid = types.ModuleType("hid")
hid.enumerate = lambda v, p: [{"path": b"/dev/hidraw-test"}]


def _nowe():
    d = UdawanyHid()
    URZADZENIA.append(d)
    return d


hid.device = _nowe
sys.modules["hid"] = hid
sys.modules["sounddevice"] = types.ModuleType("sounddevice")

spec = importlib.util.spec_from_file_location(
    "td", Path(__file__).with_name("telefon_daemon.py"))
td = importlib.util.module_from_spec(spec)
spec.loader.exec_module(td)

CFG = {"ekran": {"szerokosc": 20, "polskie_znaki": True, "strona_s": 2.5,
                 "czyszczenie": "auto"},
       "tryby": {"spk_jak_sluchawka": False}}
bledy = []


def sprawdz(warunek, opis):
    print(("  ok    " if warunek else "  BLAD  ") + opis)
    if not warunek:
        bledy.append(opis)


# ---------------------------------------------------------------- 1. ramki
print("1. Ramki linii (wzor tSIP)")
r = td.ramki_linii("Pani Krysia - sluzy")          # 19 znakow
sprawdz(len(r) == 3, "19 znakow = 3 ramki")
sprawdz([x[1] for x in r] == [0x00, 0x00, 0x80], "tylko ostatnia ma 0x80")
sprawdz(all(len(x) == 18 for x in r), "kazda ramka 18 bajtow")
sprawdz(r[2][2:] == "zy ".encode("utf-16-le")[:6] + b"\x00" * 10
        or r[2][2:8] == "uzy".encode("utf-16-le"), "reszta dopelniona zerami")
sprawdz(len(td.ramki_linii("12345678")) == 1, "8 znakow = 1 ramka")
sprawdz(td.ramki_linii("")[0][2:4] == " ".encode("utf-16-le"), "pusta linia = spacja")

# ---------------------------------------------------------------- 2. strony
print("2. Dzielenie na strony bez ucinania slow")
s = td.zawin("Znajdz plik z umowa najmu magazynu i wyslij go mailem do brata", 20)
sprawdz(all(len(x) <= 20 for x in s), f"strony <= 20 znakow: {s}")
sprawdz(" ".join(s).split() == "Znajdz plik z umowa najmu magazynu i wyslij go "
        "mailem do brata".split(), "zadne slowo nie zgubione ani uciete")
dlugie = td.zawin("/home/unnamed/Dokumenty/bardzo_dluga_nazwa_pliku.pdf", 20)
sprawdz(all(len(x) <= 20 for x in dlugie), "slowo dluzsze niz linia dzielone")
sprawdz(td.czas_txt(42) == "0:42" and td.czas_txt(3725) == "1:02:05", "format czasu")

# ---------------------------------------------------------------- 3. telefon
print("3. Warstwa HID")
tel = td.Telefon(CFG)
dev = URZADZENIA[-1]
sprawdz(dev.zapisy[0] == b"\x13\x00", "jedno czyszczenie po polaczeniu")

# stan przyciskow: HEADSET potem spoczynek (stary blad: tryb sie zacinal)
zdarzenia = []
dev.raporty = [bytes([0, 0, 0, 0, 0x60, 0, 0, 0]),
               bytes([0, 0, 0, 0, 0x02, 0, 0, 0]),
               bytes([0, 0, 0, 0, 0x40, 0, 0, 0]),
               bytes([0, 0, 0, 0x03, 0x40, 0, 0, 0]),   # klawisz 2
               bytes([0, 0, 0, 0x03, 0x40, 0, 0, 0]),   # trzymany - bez powtorki
               bytes([0, 0, 0, 0, 0x02, 0, 0, 0])]
try:
    tel.czytaj(lambda p: zdarzenia.append(("widelki", p)),
               lambda k: zdarzenia.append(("klawisz", k)),
               lambda t: zdarzenia.append(("tryb", t)))
except KeyboardInterrupt:
    pass
sprawdz(("tryb", None) in zdarzenia[:3], "HEADSET -> spoczynek kasuje tryb")
sprawdz(zdarzenia.count(("klawisz", 0x03)) == 1, "trzymany klawisz liczony raz")
sprawdz([z for z in zdarzenia if z[0] == "widelki"] ==
        [("widelki", True), ("widelki", False)], "sluchawka: start i koniec rozmowy")
sprawdz(tel.tryb is None and not tel.podniesiona, "koncowy stan: spoczynek")

# SPK nie startuje rozmowy, chyba ze wlaczone w configu
tel2 = td.Telefon({**CFG, "tryby": {"spk_jak_sluchawka": True}})
d2 = URZADZENIA[-1]
z2 = []
d2.raporty = [bytes([0, 0, 0, 0, 0x50, 0, 0, 0])]
try:
    tel2.czytaj(lambda p: z2.append(p), lambda k: None, lambda t: None)
except KeyboardInterrupt:
    pass
sprawdz(z2 == [True], "spk_jak_sluchawka: SPK startuje rozmowe")

# dioda: kolor i poczta razem (stary blad: mruganie kasowalo poczte)
dev.zapisy.clear()
tel.led(poczta=True)
tel.led(kolor=td.CZERWONY)
tel.led(kolor=td.ZIELONY)
sprawdz(dev.zapisy[-1] == bytes([0x16, 0x01, 0x06]), "po mrugnieciu poczta zostaje")

# ekran: bez czyszczenia przy tej samej dlugosci, z czyszczeniem gdy krocej
dev.zapisy.clear()
tel.ekran("Robie 0:41", "Znajdz plik")
tel.ekran("Robie 0:42", "Znajdz plik")
sprawdz(b"\x13\x00" not in dev.zapisy, "licznik co sekunde bez czyszczenia")
dev.zapisy.clear()
tel.ekran("Krysia  18:21", "")
sprawdz(dev.zapisy[0] == b"\x13\x00", "krotsza linia -> jedno czyszczenie")

# przepiecie kabla
odswiezono = []
tel.po_polaczeniu.append(lambda: odswiezono.append(1))
dev.awaria = True
sprawdz(tel.ekran("x", "y") is False and not tel.polaczony, "awaria zapisu -> rozlaczony")
sprawdz(tel.polacz() and tel.polaczony and odswiezono, "ponowne polaczenie + przerysowanie")

# ---------------------------------------------------------------- 4. ekran
print("4. Co pokazuje ekran")
import datetime as _dt  # noqa: E402
import tempfile  # noqa: E402

ek = td.Ekran(CFG, tel)
ek.licznik_plik = Path(tempfile.mkdtemp()) / "licznik.json"   # nie ruszaj prawdziwego
ek.dzis = 0
l1, l2 = ek.klatka()
sprawdz(l1 == "Pani Krysia - służy" and l2.endswith("dziś: 0"),
        f"spoczynek: [{l1}] [{l2}]")
ek.ustaw_tryb("cichy")
sprawdz(ek.klatka()[0] == "Pani Krysia: cichy", "spoczynek pokazuje tryb cichy")
ek.ustaw_tryb("glosnik")
sprawdz(len(ek.klatka()[0]) <= 20, f"tryb glosnik miesci sie: [{ek.klatka()[0]}]")
ek.ustaw_tryb(None)

# licznik promptow
ek.start_zadania("zadanie Hermesa")
ek.koniec_zadania()
ek.koniec_zadania()                       # podwojny koniec z hooka
ek.dodaj_prompt()                         # polecenie glosowe do Krysi
sprawdz(ek.dzis == 2 and ek.klatka()[1].endswith("dziś: 2"),
        "licznik: zadanie + polecenie = 2 (podwojny koniec liczony raz)")
ek2 = td.Ekran(CFG, tel)
ek2.licznik_plik = ek.licznik_plik
ek2.dzis = 0
ek2._wczytaj_licznik()
sprawdz(ek2.dzis == 2, "licznik przetrwa restart daemona")
ek2.dzien = _dt.date.today() - _dt.timedelta(days=1)
sprawdz(ek2.klatka()[1].endswith("dziś: 0"), "po polnocy licznik od zera")

ek.rozmowa_start("Working {t}", "Znajdz plik z umowa najmu magazynu i wyslij go")
t0 = ek.rozmowa[2]
a = ek.klatka(t0 + 1)
b = ek.klatka(t0 + 4)
sprawdz(a[0] == "Working 0:01" and b[0] == "Working 0:04", f"licznik: {a[0]} -> {b[0]}")
sprawdz(a[1] != b[1] and len(a[1]) <= 20, f"strony: [{a[1]}] -> [{b[1]}]")
ek.rozmowa_koniec()
ek.ustaw_czeka(2)
sprawdz(ek.klatka() == ("2 wiadomości", "podnieś słuchawkę"), "czekajace wiadomosci")
ek.pokaz_chwilowo("Krysia:", "Otworzylem plik umowy na drugim pulpicie, szefie.")
sprawdz(ek.chwilowy[2] - time.time() >= 3 * 2.5 - 0.1,
        "dlugi komunikat stoi, az przejda wszystkie strony")
dl = max(len(x) for x in (ek.klatka()[0], ek.klatka()[1]))
sprawdz(dl <= 20, "nic nie wychodzi poza szerokosc")

# ---------------------------------------------------------------- 5. tryby
print("5. Tryby glosu: kto dzwoni, kto mowi, kto czeka")


class Atrapa:
    def __init__(self):
        self.lista = []

    def __getattr__(self, nazwa):
        return lambda *a, **k: self.lista.append((nazwa, a, k))


def daemon_testowy(tryb, podniesiona=False):
    d = td.Daemon.__new__(td.Daemon)
    d.telefon = types.SimpleNamespace(podniesiona=podniesiona, tryb=None,
                                      led=lambda **k: None)
    d.ekran = Atrapa()
    d.dzwiek = Atrapa()
    d.mowa = types.SimpleNamespace(plik_dla=lambda t: Path("/tmp/x.wav"))
    d.zalegle = []
    d.ostatni_plik = None
    d.krotka_s = 60
    d._dzwoni = False
    d._rozmowa_lock = threading.Lock()
    d._blink_stop = None
    d.tryb_glosu = tryb
    d.zadzwonione = []
    d.zadzwon = lambda plik, opis, czas: d.zadzwonione.append(opis)
    d._czerwony_blink = lambda: None
    return d


def wynik(d):
    if d.zadzwonione:
        return "dzwoni"
    if d.zalegle:
        return "czeka"
    if any(x[0] == "graj" for x in d.dzwiek.lista):
        return "mowi"
    return "nic"


for tryb, czas, dzw, oczek in [(None, 5, False, "mowi"), (None, 120, False, "dzwoni"),
                               (None, 0, True, "dzwoni"), ("glosnik", 120, False, "mowi"),
                               ("glosnik", 0, True, "mowi"), ("cichy", 5, False, "czeka"),
                               ("cichy", 120, False, "czeka"), ("cichy", 0, True, "czeka")]:
    d = daemon_testowy(tryb)
    d.dostarcz("tekst", "opis", czas, dzwonek=dzw)
    sprawdz(wynik(d) == oczek, f"tryb {tryb or 'zwykly':8} praca {czas:3}s "
            f"dzwonek={dzw!s:5} -> {wynik(d)}")

d = daemon_testowy("cichy", podniesiona=True)
d.dostarcz("tekst", "opis", 120)
sprawdz(wynik(d) == "mowi", "sluchawka w rece: zawsze mowi (nawet w cichym)")
d = daemon_testowy(None)
d._rozmowa_lock.acquire()
d.dostarcz("tekst", "hook w trakcie rozmowy", 120)
sprawdz(wynik(d) == "czeka", "hook w trakcie rozmowy z Krysia: czeka, nie przerywa")

d = daemon_testowy("cichy")
d.obsluz({"typ": "powiedz", "tekst": "Otwieram Steam.", "ekran": ["Otwieram Steam", ""]})
sprawdz(wynik(d) == "nic" and d.ekran.lista, "tryb cichy: potwierdzenie tylko na ekranie")

# ---------------------------------------------------------------- 6. poczta
print("6. Poczta na glosnik (klawisz # i przycisk SPK)")
import queue  # noqa: E402

d = daemon_testowy("cichy")
d.polecenia = queue.Queue()
d.telefon.spk_jak_sluchawka = False
d.obsluz_klawisz(td.KLAWISZ_POCZTA)
sprawdz(d.polecenia.get_nowait() == {"typ": "odtworz_zalegle", "glosnik": True},
        "klawisz # -> odtworz poczte na glosnik")

d.zalegle = [(Path("/tmp/a.wav"), "a"), (Path("/tmp/b.wav"), "b")]
d.obsluz({"typ": "odtworz_zalegle", "glosnik": True})
grane = [x[1][0] for x in d.dzwiek.lista if x[0] == "graj"]
sprawdz(grane == [Path("/tmp/a.wav"), Path("/tmp/b.wav")],
        "obie wiadomosci zagrane po kolei (nawet w trybie cichym)")
sprawdz(d.zalegle == [] and d.ostatni_plik == Path("/tmp/b.wav"),
        "poczta pusta, * powtorzy ostatnia")
sprawdz(any(x[0] == "pokaz_chwilowo" and x[1][0] == "Poczta: głośnik"
            for x in d.ekran.lista), "ekran: 'Poczta: głośnik'")

d = daemon_testowy(None)
d.obsluz({"typ": "odtworz_zalegle", "glosnik": True})
sprawdz(wynik(d) == "nic" and any(x[1][0] == "Brak wiadomości"
                                  for x in d.ekran.lista if x[0] == "pokaz_chwilowo"),
        "pusta poczta: 'Brak wiadomości', bez dzwieku")

d = daemon_testowy(None)
d.polecenia = queue.Queue()
d.telefon.spk_jak_sluchawka = False
d.zalegle = [(Path("/tmp/a.wav"), "a")]
d.zmiana_trybu("glosnik")
sprawdz(d.polecenia.get_nowait().get("glosnik") is True,
        "SPK przy czekajacej wiadomosci -> odtworz na glosnik")
d.zalegle = []
d.zmiana_trybu("glosnik")
sprawdz(d.polecenia.empty(), "SPK bez wiadomosci -> tylko napis na ekranie")

print()
if bledy:
    print(f"BLEDY: {len(bledy)}")
    sys.exit(1)
print("OK - wszystkie testy przeszly")
