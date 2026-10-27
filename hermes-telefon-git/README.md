# hermes-telefon

Polycom CX300 jako telefon głosowy do [Hermes Agent](https://hermes-agent.nousresearch.com) —
głosowe polecenia do asystenta na Linuxie (Hyprland/PipeWire), z wyświetlaczem LCD,
diodami, dzwonkiem i pocztą głosową.

Podnosisz słuchawkę, mówisz, odkładasz — agent wykonuje zadanie na komputerze i
oddzwania albo mówi wynik przez głośnik.

```
mowa -> arecord (mikrofon CX300) -> faster-whisper (STT, pl)
     -> hermes chat --resume (jedna sesja Krysi)
     -> piper TTS (pl_PL-gosia) -> słuchawka / głośnik / dzwonek
ekran LCD 2x20 + diody + klawisze <- HID (usb/hidraw, python-hidapi)
```

## Wymagania

- Polycom CX300 (USB `095d:9201`) — działa bez firmware / LineSIP, czysty HID + ALSA
- Linux z PipeWire (testowane: Arch/Omarchy, Hyprland)
- Python 3.12+, `arecord` (alsa-utils), `socat` (opcjonalnie), udev
- Głos Piper `pl_PL-gosia-medium` (~63 MB, pobierany osobno) i model faster-whisper `small`

## Instalacja

Patrz [INSTALL.md](INSTALL.md) — krok po kroku (venv, udev, systemd, piper, numery kart ALSA).

Szybko:

```bash
# 1. pliki
sudo cp udev/99-cx300.rules /etc/udev/rules.d/ && sudo udevadm ctrl --reload && sudo udevadm trigger
sudo usermod -aG input $USER          # wyloguj się i zaloguj
mkdir -p ~/.config/hermes-telefon ~/.local/bin
cp config/config.yaml config/dzwonek.wav ~/.config/hermes-telefon/
cp bin/telefon_daemon.py bin/telefon_tts.py bin/hermes_hook.py bin/telefon ~/.local/bin/

# 2. venv + zależności
uv venv ~/.venvs/hermes-telefon --python 3.14 --system-site-packages
uv pip install --python ~/.venvs/hermes-telefon/bin/python -r requirements-telefon.txt

# 3. serwis
mkdir -p ~/.config/systemd/user
cp systemd/hermes-telefon.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now hermes-telefon

# 4. diagnostyka
telefon status
python3 bin/cx300_test_ekranu.py
```

## Konfiguracja (`config/config.yaml`)

| klucz | znaczenie |
|---|---|
| `telefon.urzadzenie_dzwieku` | nazwa wyjścia PipeWire (fragment z `wpctl status`) |
| `stt.urzadzenie` | ALSA mikrofonu (`plughw:X,0` — **sprawdź `/proc/asound/cards`, numer karty zmienia się z portem USB!**) |
| `krotka_odpowiedz_s` | wynik krótszy = głośnik, dłuższy = telefon dzwoni |
| `czas_dzwonienia_s` | ile dzwonić, zanim wiadomość wpadnie do poczty |
| `hermes.bin` | ścieżka do binarki `hermes` |
| `ekran.szerokosc` | szerokość LCD (dla CX300: 20) |
| `tryby.spk_jak_sluchawka` | SPK = off-hook (rozmowa bez słuchawki), jeśli mikrofon podstawy nagrywa |

## Sterowanie telefonem

| ster | akcja |
|---|---|
| podnieść słuchawkę | nagrywanie polecenia (ekran: *Słucham*) |
| **odłożyć słuchawkę** | wyślij polecenie (koniec nagrywania) |
| klawisz `1` | odsłuchaj pocztę głosową przez głośnik |
| klawisz `2` | tryb głośnika (wyniki zawsze na SPK) |
| klawisz `3` | tryb cichy (wynik czeka w słuchawce, mruga czerwona dioda) |
| klawisz `*` | powtórz ostatni komunikat |
| klawisz `#` | skasuj całą pocztę głosową |
| przycisk SPK | tryb głośnomówiący; przy czekającej poczcie = odsłuchaj |
| przycisk HEADSET | tryb cichy — powiadomienia bez dzwonka, tylko dioda |

## Ciągłość rozmowy

Wszystkie polecenia głosowe trafiają do **jednej sesji Hermesa** (`hermes chat --resume`,
cache `~/.cache/hermes-telefon/krysia_sesja.json`, ważność 8 h, osobne `--source`).
Dzięki temu „znajdź plik X" → „otwórz go" działa między kolejnymi telefonami.

## Hook powiadomień

`bin/hermes_hook.py` to hook Hermes Agent: każde zadanie trwające dłużej niż
`prog_dzwonienia_s` kończy się telefonem do szefa (wynik w poczcie głosowej).
Hook wymaga jednorazowej zgody użytkownika i chroni przed zabijaniem procesów.

## Rozwiązywanie problemów

- **słychać buczenie/hum** — uruchom jednostkę podtrzymania strumienia ALSA (`alsa-keepalive`)
- **ekran czarny** — `telefon status`; daemon sam wznawia HID po odpięciu USB (retry 1 s)
- **polecenia nie wysyłają się po odłożeniu** — telefon musi wysyłać kod spoczynku `0x00`/`0x02`; sprawdź `journalctl --user -u hermes-telefon | grep "HID << "`
- **klawisze nie reagują** — klawiatura CX300 jest w **bajcie 2** raportu HID (nie w 3!), debounce 400 ms
- **numer karty ALSA skoczył** — CX300 w innym porcie USB = inna karta; popraw `stt.urzadzenie`
- **zmiana szerokości LCD** — `python3 bin/cx300_test_ekranu.py`

## Pliki

```
bin/telefon_daemon.py   daemon: HID, widełki, STT, TTS, ekran, poczta
bin/hermes_hook.py      hook Agenta -> telefon przy długich zadaniach
bin/telefon             CLI: status / powiedz / dzwon / restart
bin/telefon_tts.py      wywołanie piper -> wav
bin/cx300_test_ekranu.py  test szerokości i znaków LCD
bin/testy_offline.py      testy jednostkowe bez sprzętu
docs/hyprland-reguly.conf przykładowe reguły okien (terminal kryzysowy)
docs/pulpity.md          nazwy pulpity w mowie (pulpity w configu)
```

## Licencja

MIT (o ile nie postanowisz inaczej, Szefie).
