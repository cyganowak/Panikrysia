# Ściąga dla Hermesa: programy na pulpitach (Omarchy / Hyprland)

Ernest mówi o **pulpitach** (workspace'ach Hyprlanda, Super+1, Super+2 ...), nie o monitorach.

## Nazwy pulpitów

| Ernest mówi | Numer |
|---|---|
| jedynka, pierwszy | 1 |
| dwójka, drugi | 2 |
| trójka, trzeci | 3 |
| gry, pulpit do gier | 5 |

(Uzupełnić razem z Ernestem. Te same nazwy są w ~/.config/hermes-telefon/config.yaml.)

## Jak uruchamiać

- Na wskazanym pulpicie, Ernest **zostaje** tam, gdzie jest:
  `hyprctl dispatch exec "[workspace 3 silent] steam"`
- Na wskazanym pulpicie i **przełącz** Ernesta tam:
  `hyprctl dispatch exec "[workspace 3] steam"`
- Bez wskazanego pulpitu: na bieżącym pulpicie:
  `hyprctl dispatch exec steam`

Domyślnie używaj wersji z `silent`, chyba że Ernest powie "i przełącz mnie" albo "pokaż".

## Ważne

- Zawsze uruchamiaj programy przez `hyprctl dispatch exec`, a nie bezpośrednio z terminala.
  Wtedy program nie jest "dzieckiem" terminala i nie zamknie się razem z nim.
  Dzięki temu hook potwierdzeń też rozpoznaje komendę i mówi "Otwieram ..., szefie".
- Steam i gry ze Steama mają stałe reguły w hyprland-reguly.conf. Dla nich wystarczy
  `hyprctl dispatch exec steam`, a okno samo trafi na swój pulpit.
