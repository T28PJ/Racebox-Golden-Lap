# Racebox Golden Lap — das Innenleben

Alles, was man zum **Benutzen** nicht braucht und zum **Ändern** schon:
wie die Daten geholt werden, wie die Schnittstelle von racebox.pro
aussieht, woher die Runden kommen, was aus welchem Grund verworfen wird,
und wie geprüft wird.

Was das Werkzeug tut und wie man es bedient, steht in
[`README.md`](README.md).

Jede Zahl hier ist nachgemessen und nicht vermutet. Wo eine Grenze steht,
steht daneben, woran sie belegt ist — eine Schwelle ohne Beleg verwirft
irgendwann eine echte Bestzeit.

## Wie die Daten geholt werden

**Zwei Anfragen je Session.** Die Sessionseite lädt ihre Daten selbst nach
— `data-fetch-url` zeigt auf `/webapp/session/<id>/json`, und dort stehen
Strecke, Konfiguration, Fahrzeug, Turn, Ortszeit und die Telemetrie in
*einer* Antwort. Die zweite Anfrage ist der CSV-Export, aus dem die Runden
kommen (siehe [Woher die Runden kommen](#woher-die-runden-kommen)); er wird
vollständig geholt und abgelegt.

**Die Verbindung bleibt offen.** 259 Sessions kosten über 500 Anfragen.
Jede mit neuem TCP- und TLS-Handschlag zu bezahlen ist der Unterschied
zwischen Minuten und Stunden.

**Fünf Sessions gleichzeitig**, jede mit eigener Verbindung; `--gleichzeitig`
ändert die Zahl. Dazu eine Notbremse beim Verbindungsaufbau: Löst ein
Rechner eine IPv6-Adresse auf, die sein Netz nicht erreicht, wartet Windows
dort rund 21 Sekunden. Jede Adresse bekommt deshalb nur fünf Sekunden,
danach ist die nächste dran; `--ipv4` überspringt IPv6 ganz.

Am Ende jedes Laufs steht, wo die Zeit hingegangen ist — Aufbau, Warten,
Laden.

## Die Schnittstelle von racebox.pro

Nachgemessen, nicht dokumentiert und nicht zugesichert — sie kann sich
jederzeit ändern. Vier Punkte, die man beim Ändern kennen muss:

**Die Kennung entscheidet, ob überhaupt etwas geht.** Anfragen mit
`User-Agent: Python-urllib/…` bekommen **403 Forbidden** — und nur die.
`curl`, ein eigener Name und Browser kommen durch.

**Anmelden** ist ein POST auf `/webapp/login` mit `email`, `password`,
`redirect_to`. Kein Captcha, kein CSRF-Token. Ob es geklappt hat, sagt der
Statuscode nicht — die Seite antwortet auch mit 200, wenn sie nur wieder
das Formular zeigt.

**Nach erfolgreicher Anmeldung kommt eine 302-Weiterleitung** — und ihr
wird bewusst *nicht* gefolgt. Am Windows-Rechner lief der zweite Sprung
dieser Kette in einen Verbindungsversuch, der nie zurückkam (`WinError
10060`). Gebraucht wird sie nicht: Das Sitzungsmerkmal steht im 302 selbst.
Nebenbei ist das die Probe darauf, *ob* die Anmeldung geklappt hat — ein
falsches Passwort antwortet mit 200 und wieder dem Formular.

**Jede Anfrage hat eine Zeitgrenze von 30 Sekunden.** Ohne sie wartet
urllib unbegrenzt, und eine stehende Verbindung ist von einem Absturz nicht
zu unterscheiden.

**Die Sessionliste** liegt unter
`/webapp/sessions?type=track&tid=all&vid=all&uid=own`, Folgeseiten mit
`&page=N`. Gelesen wird sie über ein Suchmuster auf
`/webapp/session/<24 Hexzeichen>` statt über die Seitenstruktur — das
überlebt eine Umgestaltung der Kacheln.

**Der JSON-Endpunkt** `/webapp/session/<id>/json`: Unter `session.meta`
stehen `track`, `vehicle`, `indexInTheDay` (der Turn),
`dateTimeStartedLocal`, `laps` mit ihren `sectors`, dazu `bestLapTime`,
`maxSpeed`, `minSpeed` und `maxG`; unter `session.data` die Telemetrie.
Drei Feinheiten:

- **Der letzte Sektor wird nicht mitgeliefert.** `sectors` enthält nur die
  Stücke zwischen den Splitlinien; das Schlussstück ins Ziel ist die
  Rundenzeit minus der Summe der übrigen.
- **`laps` listet nur, was zwischen zwei Linienüberfahrten liegt.**
- **Zwei Sektoren an den Rändern sind trotzdem keine Messung.** Beginnt die
  erste Runde bei Record 0, lief ihr erstes Stück ab dem Aufnahmestart und
  nicht ab einer Linie — erkennbar an `sensorRecordIndex` minus Rundenzeit
  mal Abtastrate. Und liegt eine Runde mehr vor als `lapEvents`
  Überfahrten, endete die letzte am Ende der Aufzeichnung. Beide werden
  verworfen: Wer zwei Sekunden vor einem Splitpunkt auf Aufnahme drückt,
  bekäme sonst einen Sektorrekord geschenkt, den niemand gefahren ist.

**Der CSV-Export** ist ein POST auf `/webapp/session/<id>/export/csv` mit
acht Feldern. `extendedHeader` und `addLapSectorEventsInHeader` bringen den
Kopfblock mit den Runden- und Sektorzeiten überhaupt erst hervor:

```
Track,Talkurs
Best Lap Time,29.648
Lap 1, 33.821, sectors, 9.01,6.759,9.764,0,8.288
Lap 2, 29.648, sectors, 7.358,6.174,9.026,0,7.090

Record,Time,Latitude,Longitude,Altitude,Speed,GForceX,GForceZ,Lap,LeanAngle,...
```

Die Zahl der Sektorfelder ist fest, unbenutzte stehen als `0` — auch
mittendrin: **Das letzte Feld ist für das Schlussstück reserviert**, die
Zwischensektoren füllen von links auf. Eine Strecke mit drei Splitlinien
belegt also die Felder 1, 2, 3 und 5.

Zwei Fallen: **`newLineFormat=cr` heißt in der Oberfläche „Linux/Mac" und
liefert LF**, nicht CR. Und `includeEntryExit` ändert **an der Zahl der
Rundenzeilen nichts** — der Export führt auch damit nur abgeschlossene
Runden.

Die Datenzeilen tragen eine Spalte `Lap`: Sie sagt je Messpunkt, zu welcher
gezählten Runde er gehört, `0` heißt zu keiner. Daraus kommen die
Kilometer in Runden.

**Im CSV-Kopf steht kein Fahrzeug.** Auf dem CSV-Weg kommt es deshalb aus
dem `vid`-Filter der Sessionliste. Über JSON erübrigt sich das; für
`--fahrzeug` wird der Filter trotzdem gebraucht, weil man sonst erst
*nachdem* man eine Session geholt hat weiß, welches Fahrzeug sie hatte.

## Woher die Runden kommen

Aus dem CSV-Export und nicht aus dem JSON, obwohl beide aus derselben
Datenbank stammen: **Das CSV liefert jede Sektorzeit, einschließlich des
Schlussstücks ins Ziel.** Im JSON fehlt genau diese eine Zahl und müsste
als Rundenzeit minus Summe der übrigen gebildet werden — bei einer
abgeschlossenen Runde geht das auf, bei einer Ein- oder Ausfahrrunde nicht.
Genau daran hing einmal eine Golden Lap, die sechs Sekunden zu schnell war.

Dazu führt das CSV **nur abgeschlossene Runden**; das JSON führt zwei mehr
je Session — die Ein- und die Ausfahrrunde.

Beide Quellen wurden über 451 Runden gegeneinander gehalten: **keine
einzige Abweichung.** Der Lauf vergleicht sie weiterhin bei jeder Session
— zugeordnet über die Rundenzeit und nicht über die Position, sonst wäre
der Versatz um die Einfahrrunde eine Abweichung.

`--ohne-csv` spart die zweite Anfrage. Enthält ein Export keine
Rundenzeilen, geschieht dasselbe von allein.

## Was verworfen wird, und warum

Drei Dinge werden aussortiert. Keines davon urteilt über den Fahrer —
jedes ist an den Daten belegt, und jedes wird gezählt und gemeldet.

**Unstimmige Runden**, deren Sektoren nicht ihre Rundenzeit ergeben. Dann
fehlt eine Splitüberfahrt, und niemand weiß, welcher Teil der Runde in
welcher Zahl steckt. `--verpasste-splits` zeigt, wo sie herkommen: Immer
derselbe Sektor deutet auf einen Splitpunkt, an dem das GPS schlecht
sieht; einzelne Fahrtage eher auf Wetter oder Montage.

Der Schalter hieß einmal `--muster`. Das sagte, wonach die Ansicht sucht
(nach einem Muster), aber nicht, worin — wer den Namen las, wusste danach
nicht, ob er den Schalter braucht.

**Nicht zu verwechseln mit einer verschobenen Rundengrenze.** Verpasst die
Box eine Splitüberfahrt *am Rundenanfang*, bleibt die Sektorsumme richtig
— sie wandert nur zwischen zwei Runden. Solche Fälle findet
`--verpasste-splits` deshalb nicht; sie fallen der Rundenprobe der
Statistik auf, und die benennt die Session.

**Unmögliche Sektorzeiten.** Eine verpasste Splitüberfahrt lässt die
Rundenzeit richtig, verschiebt aber Zeit aus einem Sektor in den
benachbarten. Am echten Konto standen so 0,27 Sekunden für einen Sektor,
in dem sonst 15 stehen — arithmetisch einwandfrei, als Messung unbrauchbar.
Sektorzeiten unter der **Hälfte des Medians** derselben Position werden
deshalb aussortiert. Die Grenze urteilt nicht über den Fahrer: Die beste
Sektorzeit einer Strecke liegt bei neunzig bis fünfundneunzig Prozent des
Medians, also mit riesigem Abstand darüber.

**Messpunkte ohne Positionsfix.** In der Statistik stand einmal eine
Höchstgeschwindigkeit von 222 km/h auf einer Strecke, auf der dasselbe
Gerät in drei Jahren und 130 Sessions nie über 118 kam. Was dahintersteckt:

```
08:31:37.760    77.10 km/h    51.1234560 /  12.9876540   <- normal
08:31:37.800     0.00 km/h     0.0609906 /  -0.0000055   <- Fix weg, 0/0
08:31:37.880     0.00 km/h    51.1340000 /  12.9570000   <- 2,3 km daneben
08:31:37.920   221.99 km/h    51.1234410 /  12.9876100   <- Position wieder korrekt
08:31:37.960     0.00 km/h    51.1234270 /  12.9875830   <- Position korrekt, Tempo 0
```

Der Empfänger verliert kurz seine Position und schreibt sie als `0/0`. Er
fängt sich schnell wieder, aber seine **Geschwindigkeit** braucht länger.
Verworfen wird deshalb nach der Position: Ein Messpunkt, dessen Ort nicht
zur Aufzeichnung gehört, ist keine Messung — und was in der Sekunde davor
und danach liegt, auch nicht. Der Ausreißer selbst hat eine tadellose
Position; erkennbar ist er nur daran, dass er unmittelbar auf einen Ausfall
folgt. In zwei beschädigten Sessions lagen **alle sieben** unmöglichen
Werte innerhalb von 0,16 Sekunden nach einem Punkt ohne Fix.

Das ist ausdrücklich **keine Obergrenze**. Eine Schranke bei etwa 130 km/h
hätte dieselben Daten kaputtgemacht:

| Session | Strecke | vorher | jetzt | verworfen |
|---|---|---|---|---|
| A | kurze, langsame | 221,99 | **86,68** | 234 von 16 633 |
| B | dieselbe | 182,99 | **90,38** | 300 von 15 230 |
| C | dieselbe | 118,66 | 118,66 | 0 |
| D | lange, schnelle | 217,85 | **217,85** | 0 |
| E | andere schnelle | 201,04 | **201,04** | 0 |

Auf den schnellen Strecken bleibt jede Zahl stehen, und in gesunden
Sessions wird kein einziger Punkt angefasst.

**Eine verpasste Splitueberfahrt verschiebt auch die Rundengrenze.** In
einer Session von 259 lagen die Rundenzeiten im Kopfblock und die
`Lap`-Spalte der Datenzeilen neun Sekunden auseinander — gleich groß und
entgegengesetzt bei zwei aufeinanderfolgenden Runden:

```
Lap 12, 60.903, sectors, 5.702,19.663,17.368,0,18.170
Lap 13, 58.647, sectors,     0,24.891,17.022,0,16.734
                             ^ fehlt
```

Runde 13 hat keinen ersten Sektor; seine Zeit steckt im zweiten, und die
Rundengrenze liegt an zwei verschiedenen Stellen. An den Kilometern ändert
das nichts — die neun Sekunden wandern zwischen zwei gezählten Runden hin
und her, beide zählen. Die Statistik meldet, wie viele Sessions so
auffallen, damit ein Einzelfall nicht wie ein Systemfehler aussieht.

**Sektoren als blanke Zahlen** kommen ebenfalls vor: In einer Runde standen
im JSON `1.00` und `2.00`, während der Export `24.89` und `17.02` auswies —
das waren die Indizes. Was sich nicht eindeutig als Zeit ausweist, wird
übergangen.

## Testen

```sh
python3 selbsttest.py
```

564 Zusicherungen. Ein echter HTTP-Server auf 127.0.0.1 spielt racebox.pro
— mit Anmeldung, Cookies, Blättern, Fahrzeugfilter und einem 5 MB großen
Export. Geprüft wird beobachtbares Verhalten: welche Felder rausgehen, was
im Cache landet, was bei Fehlern passiert.

Drei Dinge beim Ändern:

- **Die acht Exportfelder stehen im Test ausgeschrieben**, nicht aus der
  Konstante erzeugt. Eine Schleife über `EXPORT_FELDER` verglich sie mit
  sich selbst und bliebe grün, egal was drinsteht.
- **Der Test biegt `BASIS` auf eine tote Adresse um.** Bleibt beim Ändern
  eine echte Adresse stehen, scheitert er, statt heimlich ins Netz zu gehen.
- **Neue Prüfungen einmal absichtlich rot laufen lassen** — dafür gibt es
  `python3 mutationen.py`. Es baut 122 Fehler ein, die ein Mensch wirklich
  machen könnte, und meldet jeden, der unbemerkt bleibt. Der volle Lauf
  kostet Minuten; gefiltert geht es schneller:
  `python3 mutationen.py statistik`, `-j 8` ändert die Nebenläufigkeit.

## Grenzen

**Am echten racebox.pro gelaufen ist der ganze Weg**: Anmeldung, Blättern
über alle Seiten, JSON und Export von 259 Sessions, Fahrzeugtrennung über
sieben Motorräder.

Was das **nicht** heißt: dass die Schnittstelle so bleibt. Sie ist
nachgemessen und nicht dokumentiert, racebox.pro schuldet uns nichts.
Ändert sich das HTML des Fahrzeug-Auswahlfelds, findet die Zuordnung nichts
mehr — dann rechnet das Werkzeug ohne Fahrzeugtrennung weiter und sagt das,
statt zu raten. `--diagnose <ordner>` legt die Seiten ab, damit sich das
Muster nachziehen lässt.

**Was RaceBox falsch misst, misst auch dieses Werkzeug falsch.** Die beiden
Quellen stammen aus derselben Datenbank; sie gegeneinander zu halten prüft
die Rechnung, nicht die Wirklichkeit.
