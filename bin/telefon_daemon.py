#!/usr/bin/env python3
"""
telefon_daemon.py (cx300-fixed) - program w tle, jedyny "wlasciciel" telefonu
Polycom CX300 (USB 095d:9201).

Watki:
  1. odczyt HID: widelki, przyciski SPK/HEADSET (bajt 4), klawiatura (bajt 3)
  2. gniazdo lokalne (hooki Hermesa i polecenie `telefon` -> JSON)
  3. kolejka polecen z dzwiekiem (potwierdzenia, dzwonienie, wiadomosci)
  4. ekran: rysuje TYLKO gdy tresc sie zmienila
  5. keepalive co 10 s + ponowne polaczenie po przepieciu kabla

Co zmienione wzgledem poprzedniej wersji - patrz README_POPRAWKI.md.

Protokol ekranu (wzor: tSIP SetDisplayTwoLines, sprawdzony na tym modelu):
  13 15                      tryb dwoch linii (wysylany przed kazda klatka)
  14 05 80 / 14 0A 80        kursor: gorna / dolna linia
  15 <00|80> <16 bajtow>     do 8 znakow UTF-16LE, reszta zera;
                             0x80 = ostatni kawalek linii, 0x00 = ciag dalszy
  13 00                      czyszczenie - TYLKO po polaczeniu i gdy linia
                             sie skrocila (tryb "auto"); co klatke = migotanie
  feature 17 09 04 01 02     keepalive, inaczej telefon pokazuje ekran Lync
"""

import hashlib
import json
import logging
import os
import queue
import socket
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path

import yaml

CONFIG = Path("~/.config/hermes-telefon/config.yaml").expanduser()
STAN_TRYBU = Path("~/.cache/hermes-telefon/tryb.json").expanduser()
log = logging.getLogger("hermes-telefon")

TYLKO_EKRAN = {"start_zadania", "koniec_zadania"}

VENDOR_ID = 0x095D
PRODUCT_ID = 0x9201

# Bajt 4 raportu wejsciowego = co fizycznie jest wlaczone w telefonie.
# 0x00 i 0x02 (spoczynek) MUSZA byc w tej tabeli: telefon realnie wysyla
# 0x00 przy odlozeniu sluchawki - wczesniej nie byl znany, wiec "rozmowa"
# nie konczyla sie nigdy i polecenie szlo dopiero po 30s limitu nagrywania.
TRYB_AUDIO = {0x40: "sluchawka",
              0x50: "glosnik", 0x52: "glosnik",   # przycisk SPK
              0x60: "zestaw",                      # przycisk HEADSET
              0x02: None,                          # wszystko wylaczone
              0x00: None}                          # odlozony/spoczynek

# Klawisze (bajt 3 & 0x1F): '1'=0x02, '2'=0x03, '3'=0x04, '*'=0x0B, '#'=0x0C
KLAWISZE_TRYBU = {0x03: "glosnik", 0x04: "cichy"}             # 2 / 3
KLAWISZ_POWTORZ = 0x0B                                         # *
KLAWISZ_POCZTA = 0x02                                          # 1 = odsłuchaj pocztę
KLAWISZ_POCZTA_G = 0x0C                                        # # = poczta na głośnik
KLAWISZ_TRANSKRYPT = 0x0A                                      # 9 = nagraj konwersacje -> transkrypt
KLAWISZ_RING = 0x05                                            # 4 = tryb ring (kazda wiadomosc dzwoni)
RING_STAN = Path("~/.cache/hermes-telefon/ring.json").expanduser()

# Kolory diody statusu (raport 0x16)
ZIELONY, CZERWONY, WYLACZONY = 0x01, 0x03, 0x07


def wczytaj_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- tekst na ekran
POLSKIE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def czas_txt(sekundy: float) -> str:
    """42 -> '0:42', 125 -> '2:05', 3725 -> '1:02:05' (krotko, oszczedza znaki)."""
    s = int(sekundy)
    g, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{g}:{m:02d}:{s:02d}" if g else f"{m}:{s:02d}"


def zawin(tekst: str, szer: int) -> list[str]:
    """Dzieli tekst na strony po CALYCH slowach (zadne slowo nie jest uciete).

    Tylko slowo dluzsze niz cala linia (np. sciezka pliku) jest dzielone.
    """
    strony, biez = [], ""
    for slowo in " ".join(tekst.split()).split(" "):
        if not slowo:
            continue
        while len(slowo) > szer:
            if biez:
                strony.append(biez)
                biez = ""
            strony.append(slowo[:szer])
            slowo = slowo[szer:]
        if not slowo:
            continue
        if not biez:
            biez = slowo
        elif len(biez) + 1 + len(slowo) <= szer:
            biez += " " + slowo
        else:
            strony.append(biez)
            biez = slowo
    if biez:
        strony.append(biez)
    return strony or [""]


def ramki_linii(tekst: str) -> list[bytes]:
    """Raporty 0x15 dla jednej linii - dokladnie jak tSIP.

    ceil(len/8) kawalkow, BEZ dopelniania spacjami; ostatni kawalek ma 0x80.
    Pusta linia = jedna spacja (tSIP robi tak samo: line[0] = ' ').
    """
    tekst = tekst or " "
    n = -(-len(tekst) // 8)
    ramki = []
    for i in range(n):
        body = tekst[i * 8:(i + 1) * 8].encode("utf-16-le")
        body += b"\x00" * (16 - len(body))
        ramki.append(b"\x15" + (b"\x80" if i == n - 1 else b"\x00") + body)
    return ramki


# ---------------------------------------------------------------- telefon (HID)
class Telefon:
    """Warstwa HID: odczyt widelek/klawiszy, dioda, ekran, ponowne polaczenie."""

    def __init__(self, cfg: dict):
        import hid
        self._hid = hid
        self.dev = None
        self.lock = threading.Lock()
        self.tryb = None              # "sluchawka"/"glosnik"/"zestaw"/None
        self.podniesiona = False      # trwa rozmowa (sluchawka, opcjonalnie SPK)
        self.spk_jak_sluchawka = bool(
            cfg.get("tryby", {}).get("spk_jak_sluchawka", False))
        self.czyszczenie = cfg.get("ekran", {}).get("czyszczenie", "auto")
        self._ostatni_klawisz = 0
        self._led_kolor = ZIELONY
        self._led_poczta = False
        self._dlugosci = (0, 0)       # dlugosci ostatnio wyslanych linii
        self.po_polaczeniu = []       # np. Ekran.odswiez - pelne przerysowanie
        if not self.polacz():
            log.warning("CX300 niedostepny - ponowie probe co 10 s")

    @property
    def polaczony(self) -> bool:
        return self.dev is not None

    # --- polaczenie
    def polacz(self) -> bool:
        if self.dev is not None:
            return True
        for probe in self._hid.enumerate(VENDOR_ID, PRODUCT_ID):
            try:
                dev = self._hid.device()
                dev.open_path(probe["path"])
                dev.set_nonblocking(True)
            except (OSError, ValueError) as e:
                log.warning("CX300 %s: %s", probe["path"], e)
                continue
            with self.lock:
                self.dev = dev
            log.info("CX300 polaczony: %s", probe["path"])
            break
        else:
            return False
        self.keepalive()
        try:
            self._write(b"\x13\x00")     # jedno czyszczenie po polaczeniu
        except OSError:
            return False
        self._dlugosci = (0, 0)
        self._led_zapisz()
        for cb in self.po_polaczeniu:
            cb()
        return True

    def _rozlacz(self, powod):
        with self.lock:
            dev, self.dev = self.dev, None
        if dev is not None:
            log.warning("CX300 rozlaczony (%s)", powod)
            try:
                dev.close()
            except Exception:                          # noqa: BLE001
                pass

    # --- niski poziom
    def _write(self, data: bytes):
        if log.isEnabledFor(logging.DEBUG):
            log.debug("HID >> %s", data.hex(" "))
        with self.lock:
            dev = self.dev
            if dev is None:
                raise OSError("CX300 rozlaczony")
            try:
                n = dev.write(bytes(data))   # bez prefiksu ID raportu
            except (OSError, ValueError) as e:
                n, blad = -1, str(e)
            else:
                blad = None if n >= 0 else dev.error()
        if n < 0:
            self._rozlacz(blad)
            raise OSError(f"write: {blad}")

    def keepalive(self):
        with self.lock:
            dev = self.dev
            if dev is None:
                return
            try:
                dev.send_feature_report(b"\x17\x09\x04\x01\x02")
                return
            except (OSError, ValueError) as e:
                blad = str(e)
        self._rozlacz(f"keepalive: {blad}")

    # --- odczyt
    def czytaj(self, na_widelki, na_klawisz, na_tryb):
        while True:
            dev = self.dev
            if dev is None:
                time.sleep(1)
                continue
            try:
                raport = dev.read(8, timeout_ms=1000)
            except (OSError, ValueError) as e:
                self._rozlacz(f"read: {e}")
                continue
            if not raport or len(raport) < 8:
                continue
            log.debug("HID << %s", " ".join(f"{b:02X}" for b in raport))
            b4 = raport[4]
            if b4 in TRYB_AUDIO:
                tryb = TRYB_AUDIO[b4]
                if tryb != self.tryb:
                    self.tryb = tryb
                    log.info("Przyciski telefonu: %s", tryb or "spoczynek")
                    na_tryb(tryb)
            rozmowa = (self.tryb == "sluchawka"
                       or (self.spk_jak_sluchawka and self.tryb == "glosnik"))
            if rozmowa != self.podniesiona:
                self.podniesiona = rozmowa
                na_widelki(rozmowa)
            # Kod cyfry = BAJT 2 (zywe logi 19:26: '2'=0x03 '3'=0x04 '4'=0x05
            # ... '*'=0x0B '#'=0x0C). Bajt 3 to NIE klawiatura: staly 0x02/0x00
            # w spoczynku i smieci (0x02->0x03->0x01) w momencie podniesienia
            # sluchawki — czytanie z niego dawalo falszywe 'Klawisz 0x01/0x02'
            # i gubilo prawdziwe wciśniecia. 0x01 = '0', nic nie obsluguje;
            # odrzucamy, bo to rowniez smieć z chwili podniesienia.
            klawisz = raport[2] & 0x1F
            # Debounce czasowy: telefon wysyla TE SAM kod klawisza dwukrotnie
            # (chwila = bajt3 0x00, przytrzymanie = 0x02, ~16ms roznicy) a
            # miedzy ramkami trafia sie smiecioframe z b2=0, ktory kascki
            # _ostatni_klawisz i drugie 'powtorz' gralo wiadomosc 2x.
            if klawisz and klawisz != 0x01 \
                    and (klawisz != self._ostatni_klawisz
                         or time.monotonic() - getattr(self, "_ts_klawisza", 0) > 0.4):
                self._ts_klawisza = time.monotonic()
                self._ostatni_klawisz = klawisz
                na_klawisz(klawisz)
            elif not klawisz:
                self._ostatni_klawisz = 0

    # --- dioda: kolor i "poczta" zawsze wysylane RAZEM
    def led(self, kolor=None, poczta=None):
        """Wczesniej kazde mrugniecie wysylalo 16 xx 00 i kasowalo diode poczty."""
        if kolor is not None:
            self._led_kolor = kolor
        if poczta is not None:
            self._led_poczta = poczta
        self._led_zapisz()

    def _led_zapisz(self):
        try:
            self._write(bytes([0x16, self._led_kolor,
                               0x06 if self._led_poczta else 0x00]))
        except OSError:
            pass

    # --- ekran
    def ekran(self, linia1: str, linia2: str) -> bool:
        """Rysuje dwie linie. True = wyslane. Bez czyszczenia co klatke."""
        if self.dev is None:
            return False
        d1, d2 = self._dlugosci
        czysc = (self.czyszczenie == "zawsze"
                 or (self.czyszczenie == "auto"
                     and (len(linia1) < d1 or len(linia2) < d2)))
        try:
            if czysc:
                self._write(b"\x13\x00")
            self._write(b"\x13\x15")
            for kursor, tekst in ((b"\x14\x05\x80", linia1),
                                  (b"\x14\x0A\x80", linia2)):
                self._write(kursor)
                for ramka in ramki_linii(tekst):
                    self._write(ramka)
        except OSError as e:
            log.debug("ekran: %s", e)
            return False
        self._dlugosci = (len(linia1), len(linia2))
        return True


# ---------------------------------------------------------------- ekran: co pokazac
class Ekran:
    """Wybiera dwie linie wg priorytetu i rysuje je tylko po zmianie.

    Priorytet: chwilowy > dzwonienie > rozmowa z Krysia > zadanie Hermesa >
    czekajace wiadomosci > spoczynek.

    Linia 1: krotki status, ktory zawsze sie miesci (pisany tak, by mial
    ponizej 20 znakow). Linia 2: dluzszy tekst, dzielony na strony po calych
    slowach, kazda strona przez `strona_s` sekund. Zamiast przewijanej tasmy,
    bo ta na wolnym LCD szarpie i tnie slowa.
    """

    def __init__(self, cfg: dict, telefon: Telefon):
        e = cfg.get("ekran", {})
        self.szer = int(e.get("szerokosc", 20))
        self.polskie = bool(e.get("polskie_znaki", True))
        self.chwilowy_s = float(e.get("komunikat_chwilowy_s", 4))
        self.strona_s = float(e.get("strona_s", 2.5))
        self.telefon = telefon
        self.lock = threading.Lock()
        self.chwilowy = None       # (l1, l2, do_kiedy)
        self.dzwonienie = None     # (l1, l2)
        self.rozmowa = None        # (l1 z {t}, l2, start)
        self.zadanie = None        # (opis, start) - zadanie Hermesa z hookow
        self.czeka = 0             # ile wiadomosci czeka
        self.tryb = None           # tryb glosu do pokazania w spoczynku
        # licznik promptow z dzisiaj (zadania Hermesa + polecenia do Krysi);
        # ten sam plik co w poprzedniej wersji, wiec dzisiejszy stan zostaje
        self.licznik_plik = Path("~/.cache/hermes-telefon/licznik.json"
                                 ).expanduser()
        self.dzis, self.dzien = 0, date.today()
        self._wczytaj_licznik()
        self._l2 = None
        self._l2_od = 0.0
        self.poprzednie = None
        telefon.po_polaczeniu.append(self.odswiez)

    # --- API (wolane z innych watkow)
    def pokaz_chwilowo(self, l1: str, l2: str = "", sek=None):
        if sek is None:   # dlugi tekst: tyle czasu, by przeszly wszystkie strony
            sek = max(self.chwilowy_s,
                      len(zawin(self._znaki(l2), self.szer)) * self.strona_s)
        with self.lock:
            self.chwilowy = (l1, l2, time.time() + sek)

    def rozmowa_start(self, l1: str, l2: str = ""):
        """l1 moze zawierac {t} - wstawiany jest czas od startu."""
        with self.lock:
            self.rozmowa = (l1, l2, time.time())
            self.chwilowy = None

    def rozmowa_koniec(self):
        with self.lock:
            self.rozmowa = None

    def ustaw_dzwonienie(self, l1=None, l2=""):
        with self.lock:
            self.dzwonienie = (l1, l2) if l1 else None

    def start_zadania(self, opis: str):
        with self.lock:
            self.zadanie = (opis or "zadanie", time.time())

    def koniec_zadania(self):
        with self.lock:
            if self.zadanie is None:          # podwojny koniec - nie licz 2 razy
                return
            self.zadanie = None
        self.dodaj_prompt()

    def dodaj_prompt(self):
        with self.lock:
            self._nowy_dzien()
            self.dzis += 1
        self._zapisz_licznik()

    def _nowy_dzien(self):
        if date.today() != self.dzien:        # po polnocy od zera
            self.dzien, self.dzis = date.today(), 0

    def _wczytaj_licznik(self):
        try:
            d = json.loads(self.licznik_plik.read_text(encoding="utf-8"))
            if date.fromisoformat(d["dzien"]) == date.today():
                self.dzis = int(d.get("dzis", 0))
        except (OSError, ValueError, KeyError):
            pass

    def _zapisz_licznik(self):
        try:
            self.licznik_plik.parent.mkdir(parents=True, exist_ok=True)
            self.licznik_plik.write_text(json.dumps(
                {"dzien": str(self.dzien), "dzis": self.dzis}), encoding="utf-8")
        except OSError:
            pass

    def ustaw_czeka(self, ile: int):
        with self.lock:
            self.czeka = ile

    def ustaw_tryb(self, tryb):
        with self.lock:
            self.tryb = tryb

    def odswiez(self):
        self.poprzednie = None

    # --- wybor tresci
    def _wybierz(self, teraz: float):
        if self.chwilowy and teraz < self.chwilowy[2]:
            return self.chwilowy[0], self.chwilowy[1]
        self.chwilowy = None
        if self.dzwonienie:
            return self.dzwonienie
        if self.rozmowa:
            l1, l2, start = self.rozmowa
            return l1.format(t=czas_txt(teraz - start)), l2
        if self.zadanie:
            opis, start = self.zadanie
            if teraz - start <= 900:              # zgubiony koniec zadania
                return f"Hermes {czas_txt(teraz - start)}", opis
            self.zadanie = None
        if self.czeka:
            l1 = "Wiadomość czeka" if self.czeka == 1 \
                else f"{self.czeka} wiadomości"
            return l1, "podnieś słuchawkę"
        self._nowy_dzien()
        l1 = {"glosnik": "Pani Krysia: głośnik",
              "cichy": "Pani Krysia: cichy"}.get(self.tryb, "Pani Krysia - służy")
        return l1, f"{datetime.now():%H:%M}  dziś: {self.dzis}"

    def _znaki(self, tekst: str) -> str:
        return tekst if self.polskie else tekst.translate(POLSKIE)

    def klatka(self, teraz=None) -> tuple[str, str]:
        teraz = time.time() if teraz is None else teraz
        with self.lock:
            l1, l2 = self._wybierz(teraz)
        l1 = zawin(self._znaki(l1), self.szer)[0]
        l2 = self._znaki(l2)
        if l2 != self._l2:                # nowy tekst: od pierwszej strony
            self._l2, self._l2_od = l2, teraz
        strony = zawin(l2, self.szer)
        nr = int((teraz - self._l2_od) / self.strona_s) % len(strony)
        return l1, strony[nr]

    def petla(self):
        while True:
            nowe = self.klatka()
            if nowe != self.poprzednie and self.telefon.ekran(*nowe):
                self.poprzednie = nowe
            time.sleep(0.25)


# ---------------------------------------------------------------- dzwiek
class Dzwiek:
    """Odtwarzanie na urzadzeniu CX300 przez PipeWire."""

    def __init__(self, cfg: dict):
        import sounddevice as sd
        self.sd = sd
        self.nazwa = cfg["telefon"]["urzadzenie_dzwieku"]
        self.stop = threading.Event()

    def _urzadzenie(self):
        for i, d in enumerate(self.sd.query_devices()):
            if self.nazwa in d["name"] and d["max_output_channels"] > 0:
                return i
        return None

    def graj(self, plik, w_petli=False):
        import numpy as np
        import soundfile as sf
        self.stop.clear()
        urz = self._urzadzenie()
        dane, czest = sf.read(str(plik), dtype="float32")
        if dane.ndim > 1:
            dane = dane.mean(axis=1)
        if czest != 16000:
            # CX300 USB audio przyjmuje tylko 16 kHz - interpolacja liniowa
            n = int(len(dane) * 16000 / czest)
            dane = np.interp(np.linspace(0, len(dane) - 1, n),
                             np.arange(len(dane)), dane).astype("float32")
            czest = 16000
        try:
            while not self.stop.is_set():
                with self.sd.OutputStream(samplerate=czest, device=urz,
                                          channels=1) as st:
                    blok = int(czest * 0.05)
                    for i in range(0, len(dane), blok):
                        if self.stop.is_set():
                            break
                        st.write(dane[i:i + blok])
                if not w_petli:
                    break
        except Exception as e:                       # noqa: BLE001
            log.warning("graj(%s): %s", plik, e)

    def przerwij(self):
        self.stop.set()


# ---------------------------------------------------------------- TTS
class Mowa:
    def __init__(self, cfg: dict):
        self.python = str(Path(cfg["tts"].get("python", "python3")).expanduser())
        self.skrypt = cfg["tts"].get("skrypt", "~/.local/bin/telefon_tts.py")
        self.cache = Path(cfg["tts"]["katalog_cache"]).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)

    def plik_dla(self, tekst: str) -> Path:
        p = self.cache / (hashlib.sha1(tekst.encode()).hexdigest() + ".wav")
        if not p.exists():
            subprocess.run(
                [self.python, os.path.expanduser(self.skrypt), tekst, str(p)],
                check=True, capture_output=True, timeout=60)
        return p


# ---------------------------------------------------------------- glowna logika

# Ciagly kontekst Krysi: jedna sesja Hermesa 'krysitelefon' wznawiana --resume.
SESJKA = Path("~/.cache/hermes-telefon/krysia_sesja.json").expanduser()
ZRODLO = "krysitelefon"
WIEK_SESJKI_S = 8 * 3600


class Daemon:
    def __init__(self):
        self.cfg = wczytaj_config()
        self._hermes_bin = self.cfg.get("hermes", {}).get("bin", "hermes")
        self.telefon = Telefon(self.cfg)
        self.ekran = Ekran(self.cfg, self.telefon)
        self.dzwiek = Dzwiek(self.cfg)
        self.mowa = Mowa(self.cfg)
        self.polecenia: queue.Queue = queue.Queue()
        self.odebrano = threading.Event()
        self.odlozono = threading.Event()
        self.zalegle: list[tuple[Path, str]] = []    # (plik, opis)
        self._trk = None          # nagrywanie konwersacji (klawisz 9): (proc, wav, start)
        self.ostatni_plik: Path | None = None        # do klawisza *
        self.krotka_s = self.cfg.get("krotka_odpowiedz_s", 60)
        self.ring = self._wczytaj_ring()           # tryb ring (klawisz 4)
        self.ring_s = self.cfg.get("ring_czas_s", 30)
        self._dzwoni = False
        self._rozmowa_lock = threading.Lock()        # jedna rozmowa naraz
        self._blink_stop: threading.Event | None = None
        self.tryb_glosu = self._wczytaj_tryb()
        self.ekran.ustaw_tryb(self.tryb_glosu)

    # ============================================================ tryby glosu
    # Jedno zrodlo prawdy: klawisze 1/2/3. Przycisk HEADSET nie wlacza juz
    # trybu cichego (stan sie "zacinal"); jest tylko informacja na ekranie.
    #   zwykly  - krotkie wyniki z glosnika, dlugie: telefon dzwoni
    #   glosnik - wszystko od razu z glosnika, bez dzwonienia
    #   cichy   - zadnego dzwieku przy odlozonej sluchawce; wiadomosc czeka,
    #             dioda mruga na czerwono, odtworzy sie po podniesieniu

    def _wczytaj_tryb(self):
        try:
            tryb = json.loads(STAN_TRYBU.read_text(encoding="utf-8")).get("tryb")
            return tryb if tryb in ("glosnik", "cichy") else None
        except (OSError, ValueError):
            return None

    # ============================================================ tryb ring (klawisz 4)
    def _wczytaj_ring(self) -> bool:
        try:
            return bool(json.loads(RING_STAN.read_text(encoding="utf-8")).get("ring"))
        except (OSError, ValueError):
            return False

    def ring_przelacz(self):
        self.ring = not self.ring
        try:
            RING_STAN.parent.mkdir(parents=True, exist_ok=True)
            RING_STAN.write_text(json.dumps({"ring": self.ring}), encoding="utf-8")
        except OSError:
            pass
        if self.ring:
            self.ekran.pokaz_chwilowo("Tryb RING wlaczony", f"ring {self.ring_s}s", sek=3)
        else:
            self.ekran.pokaz_chwilowo("Tryb RING wylaczony", "", sek=3)
        log.info("tryb ring: %s", "WL" if self.ring else "WYL")

    def ustaw_tryb(self, tryb):
        self.tryb_glosu = tryb
        self.ekran.ustaw_tryb(tryb)
        try:
            STAN_TRYBU.parent.mkdir(parents=True, exist_ok=True)
            STAN_TRYBU.write_text(json.dumps({"tryb": tryb}), encoding="utf-8")
        except OSError:
            pass
        l1, l2 = {None: ("Tryb zwykły", ""),
                  "glosnik": ("Tryb: głośnik", "2 = wyłącz"),
                  "cichy": ("Tryb: cichy", "3 = wyłącz")}[tryb]
        self.ekran.pokaz_chwilowo(l1, l2, sek=3)
        if tryb != "cichy":
            self._koniec_blink()
        elif self.zalegle:
            self._czerwony_blink()
        log.info("tryb glosu: %s", tryb or "zwykly")

    def obsluz_klawisz(self, kod: int):
        log.info("Klawisz 0x%02X", kod)
        if kod in KLAWISZE_TRYBU:
            tryb = KLAWISZE_TRYBU[kod]
            # ponowne nacisniecie tego samego klawisza = powrot do zwyklego
            self.ustaw_tryb(None if tryb == self.tryb_glosu else tryb)
        elif kod == KLAWISZ_POWTORZ:
            self.polecenia.put({"typ": "powtorz"})
        elif kod == KLAWISZ_POCZTA:
            # 1 = jawne zadanie szefa: gra nawet w trybie cichym
            self.polecenia.put({"typ": "odtworz_zalegle", "glosnik": True})
        elif kod == KLAWISZ_POCZTA_G:
            # # = kasuj czekajace wiadomosci
            self.polecenia.put({"typ": "usun_zalegle"})
        elif kod == KLAWISZ_TRANSKRYPT:
            # 9 = wlacz/wylacz nagrywanie konwersacji (mikrofon glosnikowy)
            self.polecenia.put({"typ": "transkrypt_przelacz"})
        elif kod == KLAWISZ_RING:
            # 4 = wlacz/wylacz tryb ring (kazda wiadomosc dzwoni, potem poczta)
            self.ring_przelacz()

    # ============================================================ transkrypt konwersacji (klawisz 9)
    def _transkrypt_przelacz(self):
        if self._trk:
            proc, wav, start = self._trk
            self._trk = None
            try:
                proc.terminate(); proc.wait(timeout=3)
            except Exception:                        # noqa: BLE001
                proc.kill()
            time.sleep(0.2)
            self.ekran.pokaz_chwilowo("Transkrypuje...", wav.stem[:18], sek=30)
            threading.Thread(target=self._transkrypt_zakoncz,
                             args=(wav, start), daemon=True).start()
            return
        stt = self.cfg.get("stt", {})
        katalog = Path("~/Documents/nagrania").expanduser()
        katalog.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d_%H%M%S")
        wav = katalog / f"konwersacja_{ts}.wav"
        try:
            proc = subprocess.Popen(
                ["arecord", "-D", stt.get("urzadzenie", "plughw:4,0"),
                 "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", str(wav)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.ekran.pokaz_chwilowo("Mikrofon zajety", "", sek=3)
            return
        self._trk = (proc, wav, time.time())
        log.info("transkrypt: nagrywanie START -> %s", wav.name)
        self.ekran.pokaz_chwilowo("Nagrywam konwersacje", "9 = stop", sek=3)

    def _transkrypt_zakoncz(self, wav: Path, start: float):
        try:
            import soundfile as sf
            dane, _ = sf.read(wav)
            segm, _i = self._model_stt().transcribe(
                dane.astype("float32"), language="pl", vad_filter=True)
            tekst = "\n".join(s.text.strip() for s in segm if s.text.strip())
        except Exception:                            # noqa: BLE001
            log.exception("transkrypt STT")
            self.ekran.pokaz_chwilowo("Blad transkryptu", "")
            return
        dlugosc = (time.time() - start) / 60
        txt = wav.with_suffix(".txt")
        txt.write_text(
            f"=== Konwersacja {wav.stem[12:]}, okolo {dlugosc:.0f} min ===\n"
            + (tekst or "(cisza — nic nie slychac)") + "\n", encoding="utf-8")
        log.info("transkrypt ZAPISANY: %s (%d znakow)", txt.name, len(tekst))
        self.ekran.pokaz_chwilowo("Transkrypt:", txt.name[-18:], sek=4)

    # ============================================================ diody
    def _czerwony_blink(self):
        if self._blink_stop:
            return
        ev = self._blink_stop = threading.Event()

        def miganie():
            on = True
            while not ev.wait(0.5):
                self.telefon.led(kolor=CZERWONY if on else ZIELONY)
                on = not on
            self.telefon.led(kolor=ZIELONY)
        threading.Thread(target=miganie, daemon=True).start()

    def _koniec_blink(self):
        if self._blink_stop:
            self._blink_stop.set()
            self._blink_stop = None

    # ============================================================ dostarczanie
    def dostarcz(self, tekst: str, opis: str, czas: float, dzwonek=False):
        """JEDNO miejsce, ktore decyduje, jak wynik trafia do szefa.

        Wczesniej byly trzy rozne sciezki (powiedz/zadzwon/podaj_wynik) i np.
        tryb cichy nie blokowal dzwonienia z hookow Hermesa.
        """
        plik = self.mowa.plik_dla(tekst)
        self.ostatni_plik = plik
        if self._rozmowa_lock.locked() and not self._dzwoni:
            self._zostaw(plik, opis)          # szef wlasnie rozmawia z Krysia
        elif self.telefon.podniesiona:
            self.dzwiek.graj(plik)
        elif self.ring:
            self.zadzwon(plik, opis, czas)    # tryb ring: kazda wiadomosc dzwoni
        elif self.tryb_glosu == "cichy":
            self._zostaw(plik, opis)
        elif self.tryb_glosu == "glosnik" or (czas < self.krotka_s
                                              and not dzwonek):
            self.dzwiek.graj(plik)
        else:
            self.zadzwon(plik, opis, czas)

    def _zostaw(self, plik: Path, opis: str):
        self.zalegle.append((plik, opis))
        self.ekran.ustaw_czeka(len(self.zalegle))
        self.telefon.led(poczta=True)
        if self.tryb_glosu == "cichy":
            self._czerwony_blink()
        log.info("wiadomosc czeka (%d): %s", len(self.zalegle), opis)

    def zadzwon(self, plik: Path, opis: str, czas: float):
        self.odebrano.clear()
        self._dzwoni = True
        self.ekran.ustaw_dzwonienie("Wynik gotowy", "podnieś słuchawkę")
        dzwonek = Path(self.cfg["dzwonek"]).expanduser()
        threading.Thread(target=self.dzwiek.graj, args=(dzwonek, True),
                         daemon=True).start()
        migaj = threading.Event()

        def miganie():
            on = True
            while not migaj.wait(0.5):
                self.telefon.led(kolor=CZERWONY if on else ZIELONY)
                on = not on
        threading.Thread(target=miganie, daemon=True).start()

        odebral = self.odebrano.wait(self.cfg["czas_dzwonienia_s"])
        self.dzwiek.przerwij()
        migaj.set()
        self.telefon.led(kolor=ZIELONY)
        self.ekran.ustaw_dzwonienie(None)
        self._dzwoni = False
        if odebral:
            time.sleep(0.3)
            self.dzwiek.graj(plik)
            threading.Thread(target=self.rozmowa, daemon=True).start()
        else:
            log.info("Nieodebrane, komunikat czeka")
            self._zostaw(plik, opis)

    # ============================================================ zdarzenia HID
    def zmiana_widelek(self, podniesiona: bool):
        log.info("Rozmowa %s", "START" if podniesiona else "koniec")
        if podniesiona:
            self.odlozono.clear()
            self.odebrano.set()
            self.dzwiek.przerwij()
            self._koniec_blink()
            if self._dzwoni:
                return                        # zadzwon() sam przejmie
            if self.zalegle:
                self.polecenia.put({"typ": "odtworz_zalegle"})
            else:
                threading.Thread(target=self.rozmowa, daemon=True).start()
        else:
            self.odlozono.set()
            self.odebrano.clear()

    def zmiana_trybu(self, tryb):
        """Fizyczne przyciski: pokazujemy tylko SPK i HEADSET (sluchawka to szum)."""
        if tryb == "glosnik":
            # SPK przy czekajacej wiadomosci = odsluchaj ja z glosnika.
            # (Przy spk_jak_sluchawka SPK startuje rozmowe, a ta i tak
            # najpierw odtwarza czekajace wiadomosci.)
            if self.zalegle and not self.telefon.spk_jak_sluchawka:
                self.polecenia.put({"typ": "odtworz_zalegle", "glosnik": True})
                return
            self.ekran.pokaz_chwilowo("Głośnomówiący", "", sek=2)
        elif tryb == "zestaw":
            self.ekran.pokaz_chwilowo("Zestaw słuchawkowy", "", sek=2)

    # ============================================================ polecenia
    def obsluz_ekran(self, p: dict):
        if p["typ"] == "start_zadania":
            self.ekran.start_zadania(p.get("opis", ""))
        elif p["typ"] == "koniec_zadania":
            self.ekran.koniec_zadania()

    def obsluz(self, p: dict):
        typ = p.get("typ")
        try:
            if typ == "transkrypt_przelacz":    # klawisz 9
                self._transkrypt_przelacz()
            elif typ == "powiedz":              # potwierdzenie z hooka
                if p.get("ekran"):
                    self.ekran.pokaz_chwilowo(*p["ekran"])
                cicho = (self.tryb_glosu == "cichy"
                         and not self.telefon.podniesiona)
                if not cicho:                 # w trybie cichym tylko ekran
                    plik = self.mowa.plik_dla(p["tekst"])
                    self.dzwiek.graj(plik)
            elif typ == "zadzwon":            # hook (dlugie zadanie) lub `telefon dzwon`
                self.dostarcz(p["tekst"], p.get("opis", ""), p.get("czas", 0),
                              dzwonek=bool(p.get("dzwonek")))
            elif typ == "powtorz":
                if self.ostatni_plik and self.ostatni_plik.exists():
                    self.ekran.pokaz_chwilowo("Powtarzam", "", sek=2)
                    self.dzwiek.graj(self.ostatni_plik)
                else:
                    self.ekran.pokaz_chwilowo("Nie ma czego", "powtórzyć", sek=2)
            elif typ == "usun_zalegle":
                ile = len(self.zalegle)
                self.zalegle = []
                self.ekran.ustaw_czeka(0)
                self.telefon.led(poczta=False)
                self._koniec_blink()
                self.ekran.pokaz_chwilowo(
                    "Skasowano", f"{ile} wiadomości" if ile else "poczta pusta",
                    sek=2)
                log.info("# kasuje poczte (%d)", ile)
            elif typ == "odtworz_zalegle":
                zalegle, self.zalegle = self.zalegle, []
                if not zalegle:
                    self.ekran.pokaz_chwilowo("Brak wiadomości", "", sek=2)
                    return
                self.ekran.ustaw_czeka(0)
                self.telefon.led(poczta=False)
                self._koniec_blink()
                if p.get("glosnik"):          # klawisz # albo przycisk SPK
                    ile = len(zalegle)
                    self.ekran.pokaz_chwilowo(
                        "Poczta: głośnik",
                        "1 wiadomość" if ile == 1 else f"{ile} wiadomości",
                        sek=2)
                    time.sleep(0.3)
                else:
                    self.ekran.pokaz_chwilowo("Wiadomości", "", sek=2)
                    time.sleep(1.0)           # czas na przylozenie do ucha
                for plik, _ in zalegle:
                    self.ostatni_plik = plik
                    self.dzwiek.graj(plik)
                if self.telefon.podniesiona:
                    threading.Thread(target=self.rozmowa, daemon=True).start()
        except Exception:                            # noqa: BLE001
            log.exception("obsluz %r", p)

    # ============================================================ rozmowa z Krysia
    def _model_stt(self):
        if getattr(self, "_stt", None) is None:
            from faster_whisper import WhisperModel
            self._stt = WhisperModel(
                self.cfg.get("stt", {}).get("model", "small"),
                device="cpu", compute_type="int8")
        return self._stt

    def rozmowa(self):
        """Nagrywaj do konca rozmowy, rozpoznaj, wykonaj, dostarcz wynik.

        Tylko JEDNA rozmowa naraz: wczesniej podniesienie sluchawki w trakcie
        pracy Hermesa odpalalo drugie nagrywanie i drugi proces Hermesa.
        """
        if not self._rozmowa_lock.acquire(blocking=False):
            self.ekran.pokaz_chwilowo("Chwileczkę", "kończę poprzednie", sek=3)
            return
        try:
            wynik = self._rozmowa_w_zamku()
        finally:
            self._rozmowa_lock.release()
            self.ekran.rozmowa_koniec()
        if wynik:                             # poza zamkiem: moze zadzwonic
            odpowiedz, czas = wynik
            self.dostarcz(odpowiedz, "wynik zadania", czas)

    def _rozmowa_w_zamku(self):
        stt = self.cfg.get("stt", {})
        maks = stt.get("dlugosc_s", 30)
        plik = "/tmp/hermes_telefon_polecenie.wav"
        koniec = "SPK = wyślij" if self.telefon.tryb == "glosnik" \
            else "odłóż = wyślij"
        self.ekran.rozmowa_start("Słucham {t}", koniec)
        proc = subprocess.Popen(
            ["arecord", "-D", stt.get("urzadzenie", "plughw:4,0"),
             "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", plik],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not self.odlozono.wait(maks):
            log.info("nagrywanie: limit %ds", maks)
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:                            # noqa: BLE001
            proc.kill()
        time.sleep(0.2)                              # dociagnij naglowek WAV
        self.ekran.rozmowa_start("Rozumiem...", "")
        try:
            import soundfile as sf
            dane, _ = sf.read(plik)
            segm, _info = self._model_stt().transcribe(
                dane.astype("float32"), language="pl", vad_filter=True)
            tekst = " ".join(s.text for s in segm).strip()
        except Exception:                            # noqa: BLE001
            log.exception("stt")
            self.ekran.pokaz_chwilowo("Błąd", "rozpoznawania mowy")
            return None
        if not tekst:
            log.info("STT: pusto")
            self.ekran.pokaz_chwilowo("Nic nie słyszę", "")
            if self.tryb_glosu != "cichy":
                self.dzwiek.graj(self.mowa.plik_dla("Nic nie słyszę, szefie."))
            return None
        log.info("STT polecenie: %s", tekst)
        self.ekran.dodaj_prompt()
        self.ekran.rozmowa_start("Working {t}", tekst)  # cale polecenie, stronami
        start = time.time()
        odpowiedz = self.wykonaj_przez_hermesa(tekst)
        self.ekran.pokaz_chwilowo("Krysia:", odpowiedz)
        return odpowiedz, time.time() - start

    # --- ciagly watek rozmowy Krysi (jedna sesja Hermesa, --resume)
    def _id_sesji(self):
        try:
            d = json.loads(SESJKA.read_text(encoding="utf-8"))
            if time.time() - d.get("ts", 0) < WIEK_SESJKI_S:
                return d.get("id")
        except (OSError, ValueError):
            pass
        return None

    def _zapisz_sesje(self, sid: str):
        try:
            SESJKA.parent.mkdir(parents=True, exist_ok=True)
            SESJKA.write_text(json.dumps({"id": sid, "ts": time.time()}),
                              encoding="utf-8")
        except OSError:
            pass

    def _znajdz_sesje(self):
        try:
            w = subprocess.run([self._hermes_bin, "sessions", "list",
                                "--source", ZRODLO, "--limit", "1"],
                               capture_output=True, text=True, timeout=15)
            for linia in w.stdout.splitlines()[2:]:
                pola = linia.split()
                if pola:
                    return pola[-1]
        except Exception:                            # noqa: BLE001
            log.debug("sesja list", exc_info=True)
        return None

    def wykonaj_przez_hermesa(self, tekst: str) -> str:
        """hermes chat -Q w ciaglej sesji Krysi. Zwraca tekst odpowiedzi."""
        prompt = ("Jestes 'pani Krysia' - osobista sekretarka szefa, "
                  "mowiaca po polsku. Szef dzwoni do Ciebie na jego "
                  "telefon stacjonarny (Polycom CX300), wiec odpowiedzi "
                  "beda ODCZYTANE GLOSEM: WYLACZNIE po polsku, maksymalnie "
                  "jedno krotkie zdanie, bez list, kodow, emoji. "
                  "Jesli polecenie wymaga wykonania akcji - wykonaj ja "
                  "i potwierdz jednym slowem. Zwracaj sie do niego 'szefie'. "
                  "Jestes w CIAGLYM watku rozmowy: pamietaj pliki, "
                  "programy i fakty z wczesniejszych polecen szefa - "
                  "'to', 'ten plik', 'on' odnosza sie do wczesniejszej "
                  "rozmowy. "
                  "ZASADY BEZPIECZENSTWA: Wolno ci ZAMYKAC zwykle programy "
                  "(pkill nazwa, hyprctl dispatch killactive) - ale NIGDY "
                  "terminala (kitty, foot, alacritty, wezterm, xterm, "
                  "konsole) ani procesu telefon_daemon, ydotoold, triki. "
                  "Zabijanie terminala albo wlasnej sluzby zostalo technicznie "
                  "zablokowane - zostaniesz o tym uprzedzona. Przed "
                  "zamknieciem aktywnego okna upewnij sie przez 'hyprctl "
                  "-j activewindow', ze to nie terminal. Zamykaj tylko to, "
                  "o co szef wyraźnie prosil. Polecenie szefa: " + tekst)
        cmd_base = [self._hermes_bin, "chat", "-q", prompt, "-Q",
                    "--accept-hooks", "--yolo", "--max-turns", "6",
                    "--source", ZRODLO]
        env = {**os.environ, "HERMES_TELEFON_INNER": "1"}
        odpowiedz = ""
        sid = self._id_sesji()
        try:
            cmd = (cmd_base + ["--resume", sid]) if sid else cmd_base
            wyn = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=300, env=env)
            if wyn.returncode != 0 and sid:
                log.info("resume %s nie udalo sie, nowa sesja", sid)
                wyn = subprocess.run(cmd_base, capture_output=True,
                                     text=True, timeout=300, env=env)
            odpowiedz = (wyn.stdout or "").strip()
            nowy = self._znajdz_sesje()
            if nowy:
                self._zapisz_sesje(nowy)
        except Exception:                            # noqa: BLE001
            log.exception("hermes chat")
        # -Q drukuje naglowek "Reasoning" i mysli; odpowiedz = ostatnia linia
        linie = [l.strip() for l in odpowiedz.splitlines() if l.strip()]
        linie = [l for l in linie if not l.startswith("┌─ Reasoning")]
        odpowiedz = linie[-1] if linie else "Nie udało się, szefie."
        odpowiedz = odpowiedz.replace("\n", " ")[:400]
        log.info("Hermes odpowiedzial: %s", odpowiedz[:120])
        return odpowiedz

    # ============================================================ gniazdo
    def nasluchuj(self):
        sciezka = self.cfg["gniazdo"]
        os.makedirs(os.path.dirname(sciezka), exist_ok=True)
        if os.path.exists(sciezka):
            os.unlink(sciezka)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(sciezka)
        os.chmod(sciezka, 0o600)
        log.info("gniazdo: %s", sciezka)
        while True:
            dane, adres = s.recvfrom(65536)
            try:
                p = json.loads(dane)
            except json.JSONDecodeError:
                log.warning("Zla wiadomosc: %r", dane[:100])
                continue
            if p.get("typ") == "ping":
                try:
                    if adres:
                        s.sendto(b"ok", adres)
                except OSError:
                    pass
                continue
            if p.get("typ") in TYLKO_EKRAN:
                self.obsluz_ekran(p)
            else:
                self.polecenia.put(p)

    def start(self):
        threading.Thread(target=self.telefon.czytaj,
                         args=(self.zmiana_widelek, self.obsluz_klawisz,
                               self.zmiana_trybu), daemon=True).start()
        threading.Thread(target=self.nasluchuj, daemon=True).start()
        threading.Thread(target=self.ekran.petla, daemon=True).start()

        def keepalive():                     # + ponowne polaczenie po przepieciu
            while True:
                time.sleep(10)
                if self.telefon.polaczony:
                    self.telefon.keepalive()
                else:
                    self.telefon.polacz()
        threading.Thread(target=keepalive, daemon=True).start()

        def prewarm():
            try:
                self._model_stt()
                log.info("model STT gotowy")
            except Exception:                        # noqa: BLE001
                log.exception("prewarm STT")
        threading.Thread(target=prewarm, daemon=True).start()

        self.telefon.led(kolor=ZIELONY, poczta=False)
        log.info("hermes-telefon dziala (tryb: %s)", self.tryb_glosu or "zwykly")
        while True:
            self.obsluz(self.polecenia.get())


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        Daemon().start()
    except Exception:                                # noqa: BLE001
        log.exception("daemon padl")
        raise
