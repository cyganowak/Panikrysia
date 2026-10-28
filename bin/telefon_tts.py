#!/usr/bin/env python3
"""telefon_tts.py - zamien tekst na WAV polskim głosem Piper.

Użycie: telefon_tts.py "tekst" /sciezka/wyjsciowy.wav
Czyta model z ~/.config/hermes-telefon/config.yaml (sekcja tts.model).
"""
import subprocess
import sys
from pathlib import Path

import yaml

def main():
    tekst, wyjscie = sys.argv[1], sys.argv[2]
    cfg = yaml.safe_load(open(Path("~/.config/hermes-telefon/config.yaml")
                              .expanduser(), encoding="utf-8"))
    piper = cfg["tts"].get("piper", "~/.venvs/hermes-telefon/bin/piper")
    model = Path(cfg["tts"]["model"]).expanduser()
    tymcz = wyjscie + ".tmp"
    subprocess.run(
        [str(Path(piper).expanduser()), "--model", str(model),
         "--length-scale", "0.8", "--sentence-silence", "0.15",
         "--output_file", tymcz],
        input=tekst.encode("utf-8"), check=True, capture_output=True,
        timeout=60,
    )
    Path(tymcz).rename(wyjscie)

if __name__ == "__main__":
    main()
