# hermes-telefon — instalacja na nowej maszynie

Cel: Hermes Agent w terminalu (Omarchy/Hyprland) rozmawia z szefem przez
telefon stacjonarny **Polycom CX300** na USB. „Pani Krysia” — sekretarka po
polsku — mówi przez telefon, dzwoni po długich zadaniach, a podniesienie
słuchawki włącza tryb polecenia głosowego.

Ten katalog (`hermes-telefon-backup/`) to PEŁNY obraz systemu z maszyny
`omarchy` (stan 2026-10-27). Zawiera aktualne, DZIAŁAJĄCE pliki (nie starsze
szkice). Docelowym modelem sterującym jest Hermes v0.19.0.


## 0. Co tu jest

    bin/telefon_daemon.py   program w tle — jedyny „właściciel” telefonu
    bin/hermes_hook.py      hook Hermesa (pre_llm / pre_tool / post_llm)
    bin/telefon_tts.py      tekst -> WAV polskim głosem Piper
    bin/telefon             CLI: dzwon / powiedz / status (wysyła do daemona)
    config/config.yaml      wszystkie ustawienia (próg dzwonienia, zwroty...)
    config/dzwonek.wav      dźwięk dzwonka (48 kHz mono PCM — kodek nieistotny)
    systemd/hermes-telefon.service   usługa user-a (autostart po zalogowaniu)
    udev/99-cx300.rules     zgoda udev na sterowanie telefonem bez roota
    docs/pulpity.md         ściąga dla Hermesa: programy na pulpitach Hyprland
    docs/hyprland-reguly.conf        reguły „Steam/gry zawsze na pulpit 5”
    docs/pl_PL-gosia-medium.onnx.json   konfiguracja głosu Piper (model .onnx
                                        osobno — patrz p.3, ~63 MB)
    requirements-telefon.txt   dokładne wersje pakietów Python
    ARCHWUM.txt             spisy/SHA-256 tego, co było na maszynie


## 1. Sprzęt i pakiet systemowy

Podłącz CX300 i sprawdź:

    lsusb | grep -i 095d:9201          # Polycom CX300
    wpctl status | grep -i CX300       # urządzenie odtwarzania ( PipeWire )
    cat /proc/asound/cards | grep -i CX300   # ALSA karta (do arecord / STT)

Pakiety systemowe (Arch/Omarchy — reszta dystrybucji: odpowiedniki):

    sudo pacman -S --needed portaudio alsa-utils

`arecord` (alsa-utils) jest wymagany do nagrywania głosu (STT). `portaudio`
jest wymagany przez `sounddevice`. Jeśli venv NIE ma dostępu do systemowego
`hid`, dopisz pakiet `python-hidapi` (patrz requirements).


## 2. venv + pakiet Python

    uv venv ~/.venvs/hermes-telefon --python 3.14 --system-site-packages
    uv pip install --python ~/.venvs/hermes-telefon/bin/python \
        -r requirements-telefon.txt

`--system-site-packages` jest WAŻNE: na źródłowej maszynie `import hid` idzie
z systemowego pythona. Pewniej jest jednak jawnie doinstalować HID:

    uv pip install --python ~/.venvs/hermes-telefon/bin/python hidapi

Wersje w requirements.txt = te, które realnie działały. Nowsze zwykle też;
testuj p.6 po instalacji.


## 3. Model głosu (Piper) i model STT (Whisper)

Głos „Pani Krysi” = Piper `pl_PL-gosia-medium` (zestaw rhasspy/piper-voices).

    mkdir -p ~/.local/share/piper
    cd ~/.local/share/piper
    curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx
    curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pl/pl_PL/gosia/medium/pl_PL-gosia-medium.onnx.json
    chmod +x ~/.venvs/hermes-telefon/bin/piper   # wrapper tworzy pip przy pakiecie

Model STT (faster-whisper `small`) pobierze się SAM przy pierwszym starcie
daemona do `~/.cache/huggingface` (~2.6 MB). Nie trzeba go kopiować ręcznie.

Model .onnx (~63 MB) celowo NIE jest w archiwum — pobierz go powyżej (lub
skopiuj z maszyny źródłowej ~/.local/share/piper/pl_PL-gosia-medium.onnx).
Jeżeli chcesz INNY polski głos: pobierz inny model z rhasspy/piper-voices i
zmień ścieżkę w config.yaml w sekcji `tts.model`.


## 4. Pliki na właściwe miejsca

    # pliki wykonywalne
    cp bin/* ~/.local/bin/
    chmod +x ~/.local/bin/telefon_daemon.py ~/.local/bin/hermes_hook.py \
             ~/.local/bin/telefon_tts.py ~/.local/bin/telefon

    # konfiguracja
    mkdir -p ~/.config/hermes-telefon
    cp config/config.yaml ~/.config/hermes-telefon/
    cp config/dzwonek.wav ~/.config/hermes-telefon/

    # reguła udev (wymaga sudo)
    sudo cp udev/99-cx300.rules /etc/udev/rules.d/
    sudo udevadm control --reload && sudo udevadm trigger
    # dopisz siebie do grupy input (jednorazowo), potem WYLOGUJ się i zaloguj:
    sudo usermod -aG input "$USER"

    # usługa systemd user-a
    mkdir -p ~/.config/systemd/user
    cp systemd/hermes-telefon.service ~/.config/systemd/user/


## 5. Dostosuj config.yaml do DOC LOWEJ maszyny

W ~/.config/hermes-telefon/config.yaml sprawdź/popraw:

- `gniazdo: /run/user/1000/hermes-telefon.sock` — 1000 to UID; sprawdź
  `id -u`, jeśli inny, popraw ścieżkę.
- `hermes.bin:` — ścieżka do binarnego Hermesa: `readlink -f $(command -v hermes)`.
- `stt.urzadzenie:` — ALSA mikrofonom CX300; na źródłowej maszynie
  `plughw:5,0`. Sprawdź numer karty: `arecord -L | grep -i cx300` albo
  `cat /proc/asound/cards` (numer karty = cyfra przed przecinkiem).
- `telefon.urzadzenie_dzwieku: "Polycom CX300"` — fragment nazwy z `wpctl status`.
- `ekran.szerokosc: 20` — ile znaków mieści linijka wyświetlacza (do korekty).
- `pulpity:` i `programy:` — nazwy pulpity/programów, jak Krysia ma je mówić.
- `prog_dzwonienia_s: 60` — po ilu sekundach zadania telefon ma zadzwonić.
- `czas_dzwonienia_s: 8` — ile sekund dzwonić nim poczta (nieodebrane).

Jeśli chcesz reguły pulpity (Steam/gry na „5”), dopisz zawartość
`docs/hyprland-reguly.conf` do `~/.config/hypr/hyprland.conf`.


## 6. Zarejestruj hooki w Hermesie

Do `~/.hermes/config.yaml` dopisz sekcję `hooks` (identycznie jak na źródle):

    hooks:
      pre_llm_call:
        - command: ~/.local/bin/hermes_hook.py
          timeout: 5
      pre_tool_call:
        - command: ~/.local/bin/hermes_hook.py
          matcher: terminal
          timeout: 5
      post_llm_call:
        - command: ~/.local/bin/hermes_hook.py
          timeout: 5

Po każdej zmianie hooka Hermes wymaga PONOWNEJ AKCEPTACJI (hasz skryptu).
Akceptacja: `hermes` → komenda `/hooks` (lub `hermes hooks doctor` pokazuje
czy hook jest allowlisted + „script unchanged since approval”). Jeśli
zmienisz plik po akceptacji — doctor zgłosi zmianę i trzeba zaakceptować
ponownie. To jest MECHANIZM, o którym trzeba pamiętać przy każdej edycji.


## 7. Start i testy

    systemctl --user daemon-reload
    systemctl --user enable --now hermes-telefon
    systemctl --user status hermes-telefon --no-pager   # Active: active (running)
    hermes hooks doctor                                  # wszystkie ✓

Testy cząstkowe (każdy z osobna):

    telefon status                     # -> ok  (daemon żyje)
    telefon powiedz "Dzien dobry szefie"   # słychać głos w słuchawce/głośniku
    telefon dzwon "Testowy telefon"    # dzwoni; podnieś słuchawkę -> komunikat

    # hooki:
    printf '{"hook_event_name":"pre_llm_call","extra":{"user_message":"otworz steam"},"session_id":"t","cwd":"/tmp","extra2":{}}' \
      | ~/.local/bin/hermes_hook.py    # cicho, rc=0; na ekranie telefonu "Pracuje"
    # twarda blokada okien (Krysia nie zamyka terminali szefa):
    printf '{"hook_event_name":"pre_tool_call","tool_name":"terminal","tool_input":{"command":"pkill -f foot"}}' \
      | HERMES_TELEFON_INNER=1 ~/.local/bin/hermes_hook.py
    # -> JSON {"action":"block","message":"Pani Krysia nie zamyka okien..."}

    # rozmowa: podnieś słuchawkę, powiedz polecenie, odłóż -> Krysia wykona i odpowie

Dziennik daemona: `journalctl --user -u hermes-telefon -f`.
Dziennik hooka (każde zdarzenie JSON): `~/.cache/hermes-telefon/hook.log`.


## 8. Jak to działa (architektura)

    Hermes (terminal)
      | hooki: start zadania / przed narzędziem / koniec zadania
      v
    hermes_hook.py  --- JSON przez gniazdo unix (/run/user/...) --->  telefon_daemon.py
    (startuje i kończy  --- jedyny właściciel telefonu: widełki HID,
     w ułamku sekundy)                                                ekran, diody, dzwonek,
                                                                      TTS, nagrywanie+STT

Hook MUSI być natychmiastowy, więc tylko wysyła JSON i wychodzi. Cała wolna
robota (dzwonienie, czekanie na słuchawkę, mowa, STT, `hermes chat`) jest w
daemonie. `HERMES_TELEFON_INNER=1` w środowisku oznacza „to zadanie wykonało
się WEWNĄTRZ telefonu” — wtedy hook nie dzwoni (żeby Krysia nie dzwoniła do
szefa w pół sekundy po tym, jak mu już odpowiedziała) i działa twarda blokada
zamykania okien.

Protokół HID CX300 (vendor 0x095D product 0x9201) ustalony z projektów
OpenPhone/tSIP i potwierdzony na żywo: widełki = usage 0x0B (raport
8 bajtów, bajt4==0x40 => słuchawka podniesiona), ekran/diody = usage 0xFF99
(raporty wyjściowe 0x16 LED, 0x13/0x14/0x15 ekran UTF-16LE, feature 0x17
keepalive co 10 s).


## 9. Bezpieczeństwo / zachowanie

- Hook NIGDY nie blokuje zwykłego Hermesa (pusty stdout, exit 0).
- Twarda blokada (`action: block`) działa TYLKO gdy HERMES_TELEFON_INNER=1,
  czyli na polecenia głosowe Krysi: blokuje pkill/killall/kill/hyprctl
  closewindow|killactive|closeorfocus.
- Prompt „Pani Krysia” (w telefon_daemon.py, funkcja wykonaj_przez_hermesa)
  ma miekki zakaz zamykania okien; hook to twarda egzekucja.

## 10. Awaria / rollback

    systemctl --user stop hermes-telefon          # wyłącz telefon
    # usuń sekcję hooks z ~/.hermes/config.yaml   # odłącz hooki

Po tym Hermes działa dokładnie jak przed instalacją.
