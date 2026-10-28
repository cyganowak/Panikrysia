# 📞 Pani Krysia — głosowy asystent Hermesa na telefonie Polycom CX300

**Podnosisz słuchawkę, mówisz po polsku, odkładasz — komputer słucha i wykonuje.**

Hermes Agent sterowany głosem przez klasyczny telefon stacjonarny **Polycom
CX300** podłączony do USB. Osobista sekretarka „Pani Krysia" odpowiada przez
słuchawkę, wyświetla status na ekraniku LCD 2×20, dzwoni po zakończeniu
długich zadań i prowadzi pocztę głosową — wszystko na Linuxie (Arch/Omarchy,
Hyprland, PipeWire), bez chmury, bez konwerterów, bez LineSIP.

```
mowa -> arecord (mikrofon CX300) -> faster-whisper (STT, pl)
     -> hermes chat --resume (jedna sesja Krysi)
     -> piper TTS (pl_PL-gosia) -> słuchawka / głośnik / dzwonek
ekran LCD 2x20 + diody + klawisze  <->  HID (usb/hidraw, python-hidapi)
```

## ✨ Możliwości

- 🎙️ Rozmowa głosowa po polsku — odebranie słuchawki = dyktowanie polecenia
- 🔊 Synteza mowy Piper (głos `pl_PL-gosia`), odpowiedzi odczytywane do słuchawki
- ☎️ Automatyczne oddzwanianie po długich zadaniach + poczta głosowa
- 📟 Obsługa wyświetlacza LCD 2×20, diod i klawiszy telefonu przez HID
- 🔒 Tryb bezpieczeństwa: Krysia nie zamyka okien ani procesów szefa
- 🖥️ Integracja z Hyprlandem (pulpity robocze, reguły okien — patrz `docs/`)

## 📦 Zawartość repozytorium

| Ścieżka | Opis |
|---|---|
| `bin/telefon_daemon.py` | Główny program w tle — jedyny właściciel telefonu |
| `bin/hermes_hook.py` | Hook Hermesa (pre_llm / pre_tool / post_llm) |
| `bin/telefon_tts.py` | Tekst → WAV polskim głosem Piper |
| `bin/telefon` | CLI: `dzwon` / `powiedz` / `status` (komunikacja z daemonem) |
| `bin/testy_offline.py` | Testy offline (STT/TTS/parsing bez telefonu) |
| `config/config.yaml` | Wszystkie ustawienia (progi, zwroty, numery kart) |
| `config/dzwonek.wav` | Dźwięk dzwonka (48 kHz mono PCM) |
| `systemd/hermes-telefon.service` | Usługa user-a (autostart po zalogowaniu) |
| `udev/99-cx300.rules` | Dostęp do telefonu bez roota |
| `docs/` | Ściągki: pulpity Hyprland, reguły okien |

## 🔧 Wymagania

- Polycom CX300 (USB `095d:9201`) — działa czysto jako HID + ALSA
- Linux z PipeWire (testowane: Arch/Omarchy + Hyprland)
- Python 3.12+, `arecord` (alsa-utils), `uv`
- Głos Piper `pl_PL-gosia-medium` (~63 MB) i model faster-whisper `small`
  (pobierane osobno — patrz [INSTALL.md](INSTALL.md))

## 🚀 Szybki start

```bash
# 1. dostęp do telefonu (udev) + uprawnienia
sudo cp udev/99-cx300.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG input $USER        # wyloguj się i zaloguj

# 2. pliki konfiguracyjne i skrypty
mkdir -p ~/.config/hermes-telefon ~/.local/bin
cp config/config.yaml config/dzwonek.wav ~/.config/hermes-telefon/
cp bin/telefon_daemon.py bin/telefon_tts.py bin/hermes_hook.py bin/telefon ~/.local/bin/

# 3. środowisko Python
uv venv ~/.venvs/hermes-telefon --python 3.14 --system-site-packages
uv pip install --python ~/.venvs/hermes-telefon/bin/python -r requirements-telefon.txt

# 4. usługa
cp systemd/hermes-telefon.service ~/.config/systemd/user/
systemctl --user enable --now hermes-telefon
```

Pełna instrukcja krok po kroku (głosy Piper, numery kart ALSA, integracja
z Hermesem, troubleshooting): **[INSTALL.md](INSTALL.md)**.

## 🧪 Weryfikacja

```bash
python bin/testy_offline.py         # testy bez telefonu
~/.local/bin/telefon status         # daemon żyje?
~/.local/bin/telefon powiedz "Cześć szefie"
```

## ⚠️ Znane ograniczenia

- Klawisze SPK/HEADSET są zablokowane sprzętowo — tryby głosu przełącza się
  klawiszami 1/2/3
- Podłączenie telefonu do innego portu USB zmienia numer karty ALSA
  (`config.yaml` → karta)
- Hook blokuje `pkill`/`kill` — celowo, żeby asystent nie pozamykał okien

## 📄 Licencja

MIT — patrz [LICENSE](LICENSE).
