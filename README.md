# Racebox Golden Lap

Die beste theoretische Runde aus deinem RaceBox-Konto — über Turns und
Fahrtage hinweg, je Strecke und Fahrzeug.

Die RaceBox-App und racebox.pro zeigen Sektorzeiten immer nur **innerhalb**
einer Session. Was fehlt, ist die Frage danach, was eigentlich drin liegt:
Sektor 1 aus dem Vormittagsturn, Sektor 2 vom letzten Fahrtag, Sektor 3 aus
der Runde, in der du dich im dritten verbremst hast. Genau das rechnet
dieses Werkzeug.

Eine Datei, nur Standardbibliothek, läuft unter Windows, Linux und macOS.

```
3 Strecke(n), 6 Turn(s), 19 Runde(n)

  #   Strecke / Fahrzeug       Runden  Bestrunde  Streuung   Turn-Theo   Golden Lap   Delta
  ----------------------------------------------------------------------------------------
  1   TALKURS (1)
      Yamaha MT-07               12    1:27.54     +0.27     1:27.42      1:27.42   -0.12
      Suzuki SV650                2    1:34.53     +1.01     1:34.53 (TR) 1:34.53   +0.00
  ----------------------------------------------------------------------------------------
  2   BERGRING NORD
      Yamaha MT-07                3    1:52.30     +0.57     1:52.16      1:52.16   -0.14
  ----------------------------------------------------------------------------------------
  3   TALKURS (2)
      Yamaha MT-07                2      54.94     +0.65       54.94        54.94   +0.00
  ----------------------------------------------------------------------------------------

  Layouts: Talkurs (1) = Grand Prix,  Talkurs (2) = Sprint
  Ausgeblendet: Kartbahn Sued (1) -- mit --alle wieder sichtbar

Auswahl -- Nummer oder Name = Strecke, s = Statistik, Enter oder q = Ende: 1
```

Fünf Zahlen, und jede beantwortet eine andere Frage:

| Spalte | was sie sagt |
|---|---|
| **Bestrunde** | die schnellste Runde, die du wirklich gefahren bist |
| **Streuung** | wie weit die drittbeste davon weg ist — klein heißt, du kannst die Zeit |
| **Turn-Theo** | die beste theoretische Runde innerhalb *eines* Turns |
| **Golden Lap** | die besten Sektoren über alle Turns und Fahrtage — die Obergrenze |
| **Delta** | was zwischen Bestrunde und Golden Lap liegt |

## Loslegen

Es braucht Python 3 (ab 3.6) und sonst nichts.

```sh
python3 rb-golden-lap.py            # Linux, macOS
python.exe .\rb-golden-lap.py       # Windows
```

Beim ersten Start werden E-Mail und Passwort des RaceBox-Kontos abgefragt.

**Der erste Lauf holt alles**, danach nur noch den Zuwachs — eine
aufgezeichnete Session ändert sich nicht mehr. Wie lange das dauert, sagt
das Werkzeug vorher an. Bei jedem weiteren Start fragt es zuerst, ob
überhaupt geholt werden soll; wer nur nachsehen will, antwortet `n` und
rechnet aus dem Cache.

**Wieder herauskommen:** Jede Eingabezeile nennt ihren Ausgang. `q` oder
`ende` beendet den Lauf, in der Fahrzeugauswahl führt es zurück zur
Übersicht. Strg+C funktioniert auch, ist aber die Notbremse und nicht die
Tür. Nur bei der Frage nach dem Passwort gibt es kein `q` — dort wäre es
ein zulässiges Passwort; der Ausgang ist die leere Eingabe.

## Die Schalter

| Schalter | wofür |
|---|---|
| *(ohne)* | Übersicht, dann Strecke wählen |
| `--strecke Talkurs` | direkt diese Strecke, ohne Menü (Nummer oder Namensteil) |
| `--statistik` | alles Gefahrene: Kilometer, Stunden, Geschwindigkeiten |
| `--nur-cache` | rechnen ohne Netz |
| `--neu` | alle Sessions noch einmal holen |
| `--alle` | auch ausgeblendete Strecken und Fahrzeuge zeigen |
| `--ausblenden "Kartbahn*"` | diese Strecke künftig weglassen |
| `--fahrzeug "Yamaha*"` | nur Sessions dieses Fahrzeugs |
| `--seit 2026-01-01` | nur Sessions ab diesem Tag holen |
| `--turns 20` | mehr Turns in der Detailansicht (Vorgabe 3) |
| `--zugang` | E-Mail und Passwort neu setzen |
| `--gleichzeitig 8` | mehr Sessions gleichzeitig holen (Vorgabe 5) |
| `--ipv4` | nur IPv4 verwenden, falls IPv6 im Netz nicht trägt |
| `--zeitgrenze 120` | länger als 30 s je Anfrage warten |
| `--verpasste-splits` | wo RaceBox Splitüberfahrten verpasst hat |
| `--doppelte` | die Exporte doppelt abgelegter Sessions vergleichen |
| `--csv` | über den alten CSV-Weg holen statt über JSON |
| `--ohne-csv` | Runden aus dem JSON nehmen statt aus dem Export |
| `--cache ordner` | anderer Cache-Ordner |
| `--diagnose ordner` | die geholten Seiten als Rohabzug ablegen |

## Was die Detailansicht zeigt

Nach der Auswahl einer Strecke — und, wenn es mehrere gibt, eines
Fahrzeugs:

```
========================================================================
Talkurs / Grand Prix -- Yamaha MT-07
========================================================================
3 Turns an 2 Tagen, 12 Runden (9 vollstaendig), 4 Sektoren

  Bestrunde         1:27.54   2026-08-02 Turn 1 (11:20) Runde 1
  2. beste          1:27.65   2026-08-02 Turn 1 (11:20) Runde 3
  3. beste          1:27.98   2026-08-02 Turn 1 (11:20) Runde 2
  Turn-Theo         1:27.42   bester einzelner Turn: 2026-08-02 Turn 1 (11:20)
  Golden Lap        1:27.42   -0.12 gegenueber der Bestrunde

  Woraus die Golden Lap besteht
    Sektor 1      28.84   2026-08-02 Turn 1 (11:20) Runde 1
    Sektor 2      23.71   2026-08-02 Turn 1 (11:20) Runde 3
    Sektor 3      19.58   2026-08-02 Turn 1 (11:20) Runde 1
    Sektor 4      15.29   2026-08-02 Turn 1 (11:20) Runde 3

  Die drei besten je Sektor
    Sektor 1   1.     28.84   2026-08-02 Turn 1 (11:20) Runde 1
               2.     28.91   2026-08-02 Turn 1 (11:20) Runde 2
               3.     28.97   2026-06-14 Turn 2 (14:35) Runde 3
    ...

  Theoretisch beste Runde je Turn (3 von 3)
    2026-08-02 Turn 1  (11:20)    3 Runde(n)  Best   1:27.54   Theo   1:27.42     -0.12
    2026-06-14 Turn 2  (14:35)    4 Runde(n)  Best   1:28.11   Theo   1:27.90     -0.21

  Theoretisch beste Runde je Fahrtag
    2026-08-02   1 Turn(s)    3 Runde(n)  Best   1:27.54   Theo   1:27.42     -0.12
```

**Die drei besten je Sektor** zeigen, ob eine Bestzeit ein Ausreißer war
oder ob du sie dreimal gefahren bist. **Je Turn und je Fahrtag** ist die
schärfere Frage als die Golden Lap: Wer Sektoren aus vier Fahrtagen
zusammensetzt, bekommt eine Zahl, die nie jemand fährt — die Turn-Zeile
sagt, was an einem Nachmittag möglich war.

## Alles Gefahrene

Die zweite Frage neben der schnellsten Runde: Was ist da eigentlich
zusammengekommen? `--statistik` beantwortet sie, im Menü führt `s` dorthin,
und `--strecke` schränkt auch hier ein.

```
==================================================================================
Alles Gefahrene
==================================================================================
41 Turns an 9 Fahrtagen, 384 Runden auf 6 Strecken mit 2 Fahrzeugen

  Insgesamt
    aufgezeichnet      1843 km   14:22:08 h  1293120 Messpunkte
    gefahren           1837 km   11:48:31 h  Schnitt 156 km/h
    ohne in/out        1471 km    9:02:11 h  Schnitt 163 km/h   80 % der km
    gestanden                     2:33:37 h
    Hoechstwerte        242 km/h   Schraeglage 52.4 links / 54.1 rechts

  Je Fahrzeug
    Fahrzeug             Tage  Turns  Runden  gefahren ohne in/out   v-max  Schraeglage
                                                    km          km    km/h         Grad
    ------------------------------------------------------------------------------
    Yamaha MT-07            7     33     312      1498        1210     242  52.4 / 54.1
    Suzuki SV650            2      8      72       339         261     208  44.0 / 45.6

  Je Strecke
    ...
```

Dieselbe Dreiteilung wie die Sektorauswertung: **insgesamt**, **je
Fahrzeug**, **je Strecke**. Anders als dort hängt hier nichts am Layout —
gezählt wird alles Aufgezeichnete, auch Sessions, die für die
Sektorrechnung ausscheiden. Die Rundenzahlen der beiden Ansichten müssen
deshalb nicht übereinstimmen.

Drei Zahlen brauchen ein Wort dazu:

- **Gefahren heißt schneller als 5 km/h.** Darunter steht das Motorrad in
  der Box, rollt an oder wartet auf die Freigabe. Die Schwelle steht in der
  Ausgabe, nicht nur im Quelltext.
- **Gefahren gegen `ohne in/out`** ist der interessante Bruch: 60 bis 93
  Prozent der Kilometer liegen in gezählten Runden, der Rest ist Aus- und
  Einfahrrunde. Auf einer 4-km-Strecke sind das schnell zwei Rundenlängen
  je Turn. Kilometer und Zeit stehen dabei in derselben Zeile und auf
  derselben Grundlage — der Schnitt daneben lässt sich aus ihnen
  nachrechnen.
- **Der Schnitt ist nicht der Mittelwert der Schnitte** — ein Turn von zwei
  Minuten zählte darin so viel wie einer von zwanzig. Gerechnet wird die
  Summe der Meter durch die Summe der Fahrzeit.

Unter der Tabelle steht nur, was du wissen musst — **jede Fußnote genau
eine Zeile**: die Fahrschwelle, wie viele Messpunkte verworfen wurden,
welche Sessions doppelt lagen, welche Strecken nicht mitzählen, wo die
Daten liegen. Passt eine Erklärung nicht in eine Zeile, steht sie in
[`TECHNIK.md`](TECHNIK.md).

**Zwei Gegenproben laufen bei jedem Aufruf mit** — ob die Rundengrenzen zu
den Rundenzeiten passen, und ob Telemetrie und Export dieselbe Strecke
nennen. Sie melden sich nur, wenn etwas nicht stimmt. Eine Zeile „alles in
Ordnung" bei jedem Lauf erzieht dazu, den ganzen Block zu überspringen —
und dann übersieht man das eine Mal, an dem etwas zu sagen wäre.

## Nur die eigenen Fahrzeuge

Wer sein RaceBox-Konto mit anderen teilt, hat schnell acht Motorräder in
der Übersicht. Die Datei `fahrzeuge` neben dem Skript sagt, welche
auftauchen sollen — ein Muster je Zeile, `*` als Platzhalter. Steht dort
nichts, werden alle gezeigt; `--alle` hebt den Filter für einen Lauf auf.

## Teststrecken ausblenden

**Ab Werk wird nichts ausgeblendet** — welche Strecke ein Übungsplatz ist,
weiß nur, wer dort gefahren ist. Wer eine loswerden will:

```sh
python3 rb-golden-lap.py --ausblenden "Kartbahn*"
```

Das schreibt das Muster in die Datei `ausblenden` neben dem Skript, wo es
sich auch von Hand bearbeiten lässt — ein Muster je Zeile, `*` als
Platzhalter, `#` ist Kommentar. Ausgeblendete Strecken werden unter der
Übersicht **benannt**, verschwinden also nicht spurlos; `--alle` zeigt sie
wieder.

## Wo die Daten liegen

**Neben dem Skript**, im selben Ordner — nicht in einem versteckten Ordner
im Benutzerprofil, den man ein halbes Jahr später nicht wiederfindet:

```
C:\Rennstrecke\Golden Lap\
    rb-golden-lap.py
    zugang                                 <- E-Mail und Passwort
    cache\
        a1b2c3d4e5f60718293a4b5c.json      <- eine Datei je Session
    csv-exports\                           <- die Originalexporte
        a1b2c3d4e5f60718293a4b5c_bikemode.csv
```

Die Übersicht nennt den Ordner in ihrer letzten Zeile.
`RB_GOLDEN_LAP_DIR` verlegt beides, `--cache` nur den Cache. Löschen ist
gefahrlos — beim nächsten Start ist alles wieder da.

**Das Passwort steht im Klartext in `zugang`.** Die Datei wird mit den
Rechten `0600` angelegt — unter Linux und macOS heißt das: nur du darfst
lesen. **Unter Windows greift das nicht**, dort setzt Python keine ACL; was
schützt, ist allein der Ordner. Wer nichts gespeichert haben will, setzt
stattdessen `RACEBOX_EMAIL` und `RACEBOX_PASSWORT` als Umgebungsvariablen —
dann wird nichts geschrieben und nichts gelesen.

## Was die Zahlen ehrlich hält

**Teilrunden zählen mit — aber nur, was sie wirklich gemessen haben.** Ein-
und Ausfahrrunden messen nicht die ganze Runde, aber die Sektoren, die sie
messen, liegen zwischen denselben zwei Splitpunkten wie sonst auch.

Steckt in der Golden Lap eine solche Sektorzeit, steht `(TR)` links vor der
Zahl — links, damit die Spalte stehen bleibt. Die Detailansicht nennt den
Grund und stellt dieselbe Rechnung ohne die Teilrunden daneben:

```
  Golden Lap (TR)   1:26.00   2 Sektorbestzeit(en) aus einer Ein-/Ausfahrrunde
  Golden Lap        1:27.50   +1.50 ohne diese, -0.80 gegenueber der Bestrunde

    Sektor 1      28.00   2026-08-10 Turn 2 (20:05) Runde 3   Teilrunde
                  28.50   2026-08-10 Turn 2 (20:05) Runde 2   ohne Teilrunde
```

**Eine Golden Lap mehr als fünfzehn Prozent unter der Bestrunde** bekommt
ein `(!)`. Dann geht wahrscheinlich eine Sektorzeit ein, die dort nicht
hingehört; die Detailansicht zeigt, welche.

**Sessions, die doppelt im Konto liegen, zählen einmal.** Gleicher Tag,
gleiche Startzeit, gleicher Turn, gleiches Fahrzeug, zwei Kennungen — das
kommt vor. Behalten wird die Fassung mit den meisten Runden; `--doppelte`
hält beide Exporte Runde für Runde gegeneinander.

**Sektoren verschiedener Fahrzeuge werden nicht gemischt.** Eine Runde, die
den Sektor des einen Motorrads an den des anderen reiht, fährt niemand.

**Verschiebt RaceBox die Splitpunkte einer Strecke**, sind alte und neue
Sektorzeiten nicht vergleichbar. Es gilt die Einteilung der neuesten
Session; die abweichenden werden nicht mitgerechnet, aber benannt.

## Wie es innen aussieht

Wie die Daten geholt werden, wie die Schnittstelle von racebox.pro
aussieht, woher die Runden kommen und was aus welchem Grund verworfen wird
— das steht in [`TECHNIK.md`](TECHNIK.md). Für die Benutzung braucht man es
nicht; wer etwas ändern will, liest es vorher.
