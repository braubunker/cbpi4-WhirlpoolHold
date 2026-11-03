# cbpi4-WhirlpoolHold

CraftBeerPi-4-Plugin, das die Whirlpool-Temperatur auf dem eingestellten Sollwert hält. Sobald der Schritt startet, übernimmt der PID des HLT-Kessels die Heizung und lässt sie nicht einfach auslaufen wie der eingebaute Wait-Schritt.

Der PID sitzt nicht im Schritt. CraftBeerPi regelt einen Kessel über seine Kessel-Logik, und diese Logik liest dauernd `target_temp`. Der Schritt schreibt den Whirlpool-Sollwert auf den Kessel mit dem HLT-Heizstab, schaltet dessen Auto-Modus ein und hält den Sollwert, bis die Zeit um ist.

## Ablauf

1. Optional wird der Auto-Modus eines anderen Kessels ausgeschaltet, damit der Kochkessel nicht weiterheizt.
2. Der Sollwert des HLT-Kessels wird auf `Temp` gesetzt.
3. Die PID-Logik dieses Kessels wird eingeschaltet (`AutoMode = Yes`).
4. Die Whirlpool-Pumpe wird eingeschaltet, falls eine gewählt ist.
5. Der Timer läuft sofort oder erst, wenn die Temperatur im Toleranzband liegt.
6. Am Ende gehen Pumpe und, wenn `HeaterOff = Yes`, auch der PID aus. Der Sollwert wird auf 0 gesetzt.

Der PID kann nur heizen. Liegt die Würze nach dem Kochen über dem Sollwert, bleibt die Heizung aus, bis die Temperatur von selbst auf den Sollwert fällt. Danach hält der PID dagegen.

## Was am Kessel eingestellt sein muss

Der Kessel im Feld `Kettle` braucht drei Dinge:

- **Heizung:** der Aktor am Heizstab im HLT
- **Sensor:** der Fühler, den der PID konstant halten soll
- **Logik:** eine PID-Logik

Zwei übliche Aufbauten:

| Ziel | Sensor am HLT-Kessel | Heizung am HLT-Kessel |
| --- | --- | --- |
| HLT-Wasser konstant, Würze folgt über die HERMS-Wendel | Fühler im HLT | Heizstab im HLT |
| Würzetemperatur konstant, Stellglied ist der HLT | Fühler in der Würze (Kochkessel / Whirlpool) | Heizstab im HLT |

Der Sensor am Schritt ist nur für die Anzeige und für den Timer „Bei Soll“. Geregelt wird immer der Sensor, der am Kessel hinterlegt ist. Am klarsten ist es, wenn beide derselbe Fühler sind.

Ohne Umwälzung sieht der PID nur seinen eigenen Fühler. Die Pumpe muss laufen, wenn die Wärme über eine Wendel in die Würze soll.

### Welche PID-Logik

`PIDBoil` regelt per PID, bis die Kochschwelle erreicht ist (Standard 98 °C). Darunter, also bei einem Whirlpool um 80 °C, passt diese Logik.

`PID_HERMS` verlässt den PID oberhalb von `Max_PID_Temp` (Standard 88 °C) und heizt dann mit voller Leistung. Die Whirlpool-Temperatur muss unter `Max_PID_Temp` liegen, sonst hält der HLT nicht. `DeltaTemp` schaltet die Heizung ab, wenn der HLT zu weit über dem Soll liegt. Für einen Hop-Stand, der ein etwas heisseres HLT-Wasser braucht, den Wert anheben.

`HltHoldPID` kommt mit diesem Plugin. Das ist ein PID ohne Koch-Umschaltung und ohne Delta-Abschaltung. Sinnvoll auf einem HLT, der nicht selbst kocht. P, I und D stammen aus [cbpi4-PID_AutoTune](https://github.com/PiBrewing/cbpi4-PID_AutoTune) und haben dieselbe Skalierung wie PIDBoil.

`Hysteresis` hält die Temperatur nur mit Ein/Aus. Der Schritt warnt einmal, wenn die Kessel-Logik kein PID ist.

## Installation

Auf dem Raspberry Pi, im virtuellen Environment von CraftBeerPi 4:

```bash
pipx runpip cbpi4 install -e /pfad/zu/diesem/repo
```

Ohne pipx, im selben Environment, in dem `cbpi` läuft:

```bash
pip install -e /pfad/zu/diesem/repo
```

Danach CraftBeerPi neu starten. `cbpi add` ist in aktuellen Versionen nicht mehr nötig. Das Paket heisst `cbpi4-WhirlpoolHold` und wird beim Start geladen, weil der Modulname mit `cbpi` beginnt.

Im Maischprofil nach dem Koch-Schritt einen Schritt vom Typ **WhirlpoolHold** einfügen. Unter Hardware die Kessel-Logik **HltHoldPID** nur dann wählen, wenn der HLT eine eigene PID-Logik ohne Kochschwelle bekommen soll.

## Parameter von WhirlpoolHold

| Parameter | Bedeutung |
| --- | --- |
| Timer | Minuten. `0` hält den Sollwert, bis du Next drückst. |
| Temp | Solltemperatur in der Einheit von CraftBeerPi (°C oder °F). |
| Sensor | Fühler für Anzeige und Timer. Leer lässt den Kessel-Sensor. |
| Kettle | Kessel mit HLT-Heizstab und PID-Logik. |
| StopKettle | Optional. Dessen Auto-Modus geht beim Start aus. Leer oder derselbe Kessel wie `Kettle`, wenn nichts gestoppt werden soll. |
| Pump | Optionale Whirlpool-Pumpe, an beim Start und aus am Ende. Leer lassen, wenn die Kessel-Logik die Pumpe schon schaltet (zum Beispiel der Agitator von PID_HERMS). |
| AutoMode | `Yes`: PID beim Start ein und am Ende aus. |
| TimerStart | `Sofort` oder `Bei Soll`. `Bei Soll` wartet das Toleranzband ab, damit der Timer nach dem Kochen nicht schon bei 99 °C losläuft. |
| Tolerance | Halbe Breite des Bands in Grad. `0.5` bedeutet Soll ± 0.5. |
| HeaterOff | `Yes`: am Ende Sollwert 0 und PID aus. `No`: der PID läuft mit dem Sollwert weiter. |

Am laufenden Schritt gibt es die Aktionen **Timer starten** und **5 Minuten dazugeben**.

Beispiel für einen Hop-Stand bei 80 °C über 20 Minuten, nachdem die Würze noch kocht:

- Timer `20`
- Temp `80`
- Kettle: der Kessel mit HLT-Heizung und PID
- Sensor: Würzefühler, oder leer wenn der Kessel diesen Fühler schon nutzt
- Pump: Whirlpool-Pumpe
- AutoMode `Yes`
- TimerStart `Bei Soll`
- Tolerance `0.5`
- HeaterOff `Yes`

Beispiel, wenn direkt bei Kochhitze gewhirlt und diese Temperatur gehalten werden soll: Temp auf die Kochtemperatur, TimerStart `Sofort`. Bei PIDBoil muss diese Temperatur unter der Kochschwelle liegen, sonst geht die Logik auf feste Kochleistung statt auf PID.

## Tests

Die Regelstrecke ist ohne CraftBeerPi geprüft: Timer-Band, kein Heizen über dem Sollwert, kein Integral-Windup an der Null, und ein simuliertes Halten nach dem Aufheizen und nach dem Kochen.

```bash
python3 -m pytest
```

## Lizenz

GPL-3.0, dieselbe Lizenz wie CraftBeerPi 4.
