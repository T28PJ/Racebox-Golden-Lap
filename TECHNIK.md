# Racebox Golden Lap — das Innenleben

Alles, was man zum **Benutzen** nicht braucht und zum **Ändern** schon:
wie die Daten geholt werden, wie die Schnittstelle von racebox.pro
aussieht, woher die Runden kommen, was aus welchem Grund verworfen wird,
was die Zusammenfassung für andere Werkzeuge enthält, und wie geprüft
wird.

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
`redirect_to`. Kein CSRF-Token. Ob es geklappt hat, sagt der Statuscode
nicht — die Seite antwortet auch mit 200, wenn sie nur wieder das Formular
zeigt.

**Seit September 2026 steckt ein Cloudflare-Turnstile im Formular.** Das
Skript von `challenges.cloudflare.com` löst im Browser eine Prüfung und
hängt beim Abschicken ein Feld `cf-turnstile-response` an, das im
statischen HTML nicht steht; der Server prüft es bei Cloudflare nach.
Fehlt es, kommt 200 mit dem Formular, ohne Fehlerwort, und die
Sessionliste leitet den Unangemeldeten mit 302 nach `/webapp/login` um.
Ein Werkzeug ohne Browser besteht diese Prüfung nicht, und sie zu umgehen
ist keine Option. Der Weg daran vorbei ist die **Sitzung aus dem Browser**:
Der Browser besteht das Captcha, das Werkzeug führt dessen Sitzung fort.
Getragen wird sie vom Cookie `racebox`; er kommt aus der Datei `sitzung`
oder aus `RACEBOX_SITZUNG` und wird an jede Anfrage gehängt. Ob er noch
gilt, zeigt die Sessionliste selbst: Eine Umleitung oder das Formular
heißt abgelaufen, und das Werkzeug rät dann zur Sitzung, nicht zum
Passwort. Der Weg über das Formular bleibt im Code, `--zugang` erzwingt
ihn — falls das Captcha wieder verschwindet. Wie lange eine Sitzung gilt,
ist nicht nachgemessen; das sagt erst der erste Ablauf.

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

**Eine Antwort ohne Session-Link und ohne Fahrzeugauswahl ist keine
Sessionliste.** Ein Konto ohne Sessions hat noch die Auswahlfelder; eine
Startseite, ein Fehlertext oder ein 200er mit null Bytes haben keines von
beidem. Das Werkzeug bricht dann ab, statt „0 Sessions" zu melden, und
fasst dabei zusammen, was geantwortet hat: je Antwort Statuscode,
Rumpfgröße, ob eine Weiterleitung kam, dazu Servername und Form der
Antwort mit Wert — und alle übrigen Kopfzeilen, Kekse voran, nur mit
Namen. Welche Werte gezeigt werden, steht in `SICHTBARE_KOPFZEILEN`; alles
andere bleibt draußen, damit sich die Zusammenfassung weitergeben lässt,
ohne dass ein Sitzungsmerkmal oder eine Kennung mitgeht.

**Leitet die Sessionliste um (302), wurde die Anmeldung nicht
angenommen.** Nur ein Unangemeldeter wird von dort weggeschickt. Das
Werkzeug meldet dann den Pfad des Ziels ohne Parameter und wertet die
Antwort auf das Anmeldeformular aus, wieder ohne Werte: Titel, jedes
Formular mit Methode, Pfad und Feldnamen, die Hosts eingebundener Skripte,
dazu die Merkmale Anmeldeformular, Token-Feld, Captcha und Fehlerwörter im
sichtbaren Text. Daran sieht man, ob ein falsches Passwort vorliegt oder
racebox.pro das Verfahren umgebaut hat — ein CSRF-Token oder ein Captcha
im Formular kann kein Passwort beheben. Kommt statt HTML Zeichensalat,
steht das da: Dann ist die Antwort komprimiert angekommen und nicht
entpackt worden.

Der Anlass: Im September 2026 antwortete racebox.pro auf den Login mit
200 und einer Seite von rund 9 kB ohne Weiterleitung, die Sessionliste
danach mit 302 und leerem Rumpf. Ausgegeben wurde „angemeldet als“ und
„0 Sessions“, beides geschlossen, nichts davon beobachtet — die
Anmeldeprüfung suchte nur ein Passwortfeld, und ein leerer 302 hat keines.
Erst auf einem Raspberry Pi, dann genauso auf dem Rechner, auf dem der
Lauf über 259 Sessions gelungen war. Die Auswertung der Login-Seite zeigte
das Turnstile-Skript und ein sonst unverändertes Formular; die Anmeldung
im Browser ging. Seitdem läuft das Werkzeug über die Sitzung aus dem
Browser, siehe oben.

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

## Die Zusammenfassung

Dieses Kapitel beschreibt eine Schnittstelle und steht deshalb für sich:
Wer mit den Daten von Golden Lap weiterrechnet, braucht nur dieses
Kapitel, nicht den Rest der Datei und nicht den Code.

Golden Lap schreibt bei jedem Lauf `golden-lap-summary.json` in seinen
Cache-Ordner (Vorgabe: `cache/` neben dem Skript). Darin steht, was
Golden Lap weiß und entschieden hat: welche Sessions und Runden es gibt,
was davon zählt und was nicht — jeweils mit Grund —, und wo jede Runde in
den Rohdaten liegt. Messpunkte stehen darin nicht. Die liegen in den
Originalexporten von racebox.pro, einer CSV-Datei je Session, und die
Zusammenfassung zeigt dorthin.

Die Arbeitsteilung: **Die Zusammenfassung sagt, was gilt; die CSV-Exporte
enthalten die Messung.** Wer Runden vergleichen will, sucht sie in der
Zusammenfassung aus und schneidet sie dann aus dem Export. Keine Regel
von Golden Lap muss dafür nachgebaut werden — das Urteil steht da.

Golden Lap misst und rechnet nichts hinzu: keine Distanzachse, keine
Interpolation, keine Glättung. Was hier steht, ist gemessen oder aus
einer Messung entschieden.

### Was gilt

- **Anzeigefilter gelten nicht.** Strecken, die Golden Lap in der Anzeige
  ausblendet, und Fahrzeuge außerhalb seines Fahrzeugfilters stehen darin
  wie alle anderen. Beides regelt, was man sieht, nicht was gefahren
  wurde.
- **Bei jedem Lauf neu**, vor jeder Ansicht, auch ohne Netz. Geschrieben
  wird erst daneben, dann umbenannt — ein Leser findet nie eine halbe
  Datei.
- **Versionen.** `version` steigt, wenn ein Leser die Datei nicht mehr
  versteht: Ein Feld entfällt, wird umbenannt oder bedeutet etwas
  anderes, ein Grund ändert seinen Sinn. Ein neues Feld oder ein neuer
  Grund lässt sie stehen. Ein Leser übergeht also, was er nicht kennt, und
  behandelt einen unbekannten Grund wie jeden anderen: als „zählt nicht".
- **Zahlen.** Zeiten in Sekunden als JSON-Zahl, nie als Text. Tage und
  Zeitpunkte im ISO-Format; `datum` und `startzeit` sind Ortszeit der
  Strecke, `datum_utc` ist UTC.
- **`null` heißt „nicht bekannt" oder „trifft nicht zu"**, nie null
  Sekunden oder null Punkte. Eine leere Liste dagegen heißt: geprüft,
  nichts gefunden.

### Aufbau

```
format            "racebox-golden-lap-summary"
version           1
erzeugt           Zeitpunkt in UTC
rohdaten_ordner   Ordner der CSV-Exporte, relativ zur Zusammenfassung
regeln            die Grenzen, nach denen geurteilt wurde (siehe unten)
sessions          jede Session, chronologisch
auswertungen      je Strecke und Streckenkonfiguration, darin je Fahrzeug
```

`regeln` nennt die Zahlen hinter den Urteilen, damit ein Leser sie
anzeigen kann, ohne sie zu kennen: `sektor_schwelle` (Anteil am Median,
unter dem eine Sektorzeit verworfen wird), `probe_grenze` (Sekunden, ab
denen eine Rundengrenze auffällt), `ortsgrenze` (Meter um die Mitte der
Aufzeichnung, außerhalb derer ein Punkt keinen Fix hat) und
`fixerholung` (Sekunden vor und nach einem solchen Punkt, die mit
verworfen werden).

### Eine Session

Was racebox.pro über sie sagt: `id` (24 Hexzeichen), `strecke`,
`konfiguration`, `konfig_id`, `fahrzeug`, `fahrzeug_id`, `datum`,
`startzeit`, `datum_utc`, `turn` (der wievielte Turn des Tages),
`quelle` (woher Golden Lap die Runden hat: `json+csv` aus dem Export,
`json` aus der JSON-Antwort, `csv` über den alten Weg, `export` allein
aus dem abgelegten Export, ohne racebox.pro) und `cache_version`. Bei
`export` fehlt, was nur racebox.pro weiß: `fahrzeug` ist `ohne Fahrzeug`,
`fahrzeug_id` und `konfig_id` sind `null`, `datum` ist der Tag in UTC und
`startzeit` trägt den Zusatz ` UTC`. Dazu das Urteil:

| `status` | heißt |
|---|---|
| `gewertet` | ihre Runden gehen in die Auswertung ein |
| `abweichende_einteilung` | RaceBox hat die Splitpunkte der Strecke seitdem verschoben. Es gilt die Einteilung der neuesten Session; alte und neue Sektorzeiten sind nicht vergleichbar |
| `ohne_einteilung` | auf dieser Strecke gibt es keine einzige vollständige Runde, aus der sich die Einteilung ablesen ließe |
| `dublette` | dieselbe Fahrt liegt unter einer zweiten Kennung (gleicher Tag, Startzeit, Turn, Fahrzeug). `dublette_von` nennt die behaltene Fassung — die mit den meisten Runden —, `dublettenvergleich` hält die Exporte beider gegeneinander: `gleich`, `abweichend`, `nur_in_behaltener`, `nur_in_dieser`, `nummer_verschoben`, `exporte_fehlen` |

Unter `rohdaten` steht der Export:

| Feld | heißt |
|---|---|
| `datei` | Pfad zum CSV-Export, relativ zur Zusammenfassung, mit `/` getrennt |
| `vorhanden` | ob die Datei da ist |
| `lap_spalte` | ob die Spalte `Lap` des Exports dieselben Rundennummern trägt wie `runden` |
| `grund` | warum nicht, sonst `null` |
| `runden_probe` | größte Abweichung zwischen Lap-Spalte und Rundenzeiten dieser Session, Sekunden |
| `verworfene_punkte` | Messpunkte, die keine Messung sind, siehe unten |

| `grund` | heißt |
|---|---|
| `kein_export` | die Datei fehlt |
| `runden_aus_json` | die Runden kamen aus der JSON-Antwort, weil der Export keine Rundenzeilen hatte. Das JSON zählt die Einfahrrunde mit, seine Nummern sind nicht die der Lap-Spalte |
| `export_unvollstaendig` | der Export hat keine Messpunkte, oder ihm fehlt eine der Spalten `Time`, `Latitude`, `Longitude`, `Speed`, `Lap` |
| `nicht_ausgewertet` | Golden Lap hat den Export nicht gelesen, weil die Session keine Telemetrie im Cache hat (alter CSV-Weg). Über die Datei sagt das nichts |

**`verworfene_punkte`** ist eine Liste von Spannen: `record_von`,
`record_bis` (Werte der Spalte `Record`, beide eingeschlossen), `punkte`,
`grund`. Einziger Grund bisher ist `ohne_fix`: Der Empfänger hatte seine
Position verloren. Er schreibt dann `0/0` als Position — oder eine, die
weiter als `ortsgrenze` von der Mitte der Aufzeichnung liegt — und liefert
danach noch eine Weile unmögliche Geschwindigkeiten bei tadelloser
Position. An einer echten Session standen so 222 km/h auf einer Strecke,
auf der dasselbe Gerät in drei Jahren nie über 118 kam. Die Spanne
umfasst deshalb die Punkte ohne Position und alles, was bis
`fixerholung` Sekunden davor und danach liegt. Die Regel urteilt nach der
Position, nie nach dem Tempo — eine Obergrenze für die Geschwindigkeit
gibt es nicht. Die Liste gilt unabhängig von `lap_spalte`. `null` heißt: nicht
bestimmt — bei `kein_export`, `export_unvollstaendig` und
`nicht_ausgewertet`, und wenn der Export keine Spalte `Record` hat.

### Eine Runde

| Feld | heißt |
|---|---|
| `nr` | Rundennummer; bei `lap_spalte` der Wert in der Spalte `Lap` |
| `zeit` | Rundenzeit, gemessen |
| `stimmig` | ob ihre Sektoren ihre Rundenzeit ergeben (auf 0,05 s) |
| `vollstaendig` | jeder Sektor der geltenden Einteilung ist gemessen und nicht verworfen; `null` außerhalb gewerteter Sessions |
| `teilrunde` | nicht jeder Sektor gemessen — Ein- und Ausfahrt, verpasste Splitüberfahrt; `null` außerhalb gewerteter Sessions |
| `zaehlt` | sie kommt als Bestrunde in Frage und geht in Streuung und Rangliste ein |
| `grund` | warum nicht, sonst `null` |
| `sektoren` | siehe unten |
| `rohdaten` | wo sie im Export liegt, siehe unten |

| `grund` | heißt |
|---|---|
| `teilrunde` | nicht jeder Sektor gemessen |
| `sektor_verworfen` | als ganze Runde gemessen, aber ein Sektor fiel unter `sektor_schwelle` mal den Median |
| `session_nicht_gewertet` | der `status` der Session schließt sie aus |

Eine unstimmige Runde kann zählen: Ihre Rundenzeit ist gemessen, nur die
Aufteilung auf die Sektoren nicht. Ihre Sektoren zählen dann nicht.

**`rohdaten` einer Runde** steht nur bei `lap_spalte`, sonst `null`:
`lap` (der Wert in der Spalte `Lap`), `record_von` und `record_bis`
(erster und letzter `Record` mit diesem Wert), `punkte` (wie viele),
`abweichung` und `auffaellig`. `abweichung` sagt, wie viele Sekunden die
Punkte der Lap-Spalte mehr umfassen als die Rundenzeit — mit Vorzeichen.
Üblich sind wenige Hundertstel, eine Abtastung. `auffaellig` heißt: Der
Betrag liegt über `probe_grenze`. Dann stimmt die Rundenzeit, aber nicht
die Zuordnung der Messpunkte: Verpasst die Box eine Splitüberfahrt am
Rundenanfang, wandert die Grenze in die Nachbarrunde, und zwei
benachbarte Runden zeigen gleich große, entgegengesetzte Abweichungen.
Wer Runden übereinanderlegt, sollte eine auffällige Runde nicht nehmen.
Eine Runde ohne einen einzigen Punkt hat `record_von` und `record_bis`
`null` und ist auffällig.

### Ein Sektor

| Feld | heißt |
|---|---|
| `position` | Stelle in der Sektorliste von Golden Lap, 1-basiert |
| `nummer` | die Nummer, unter der RaceBox und Golden Lap den Sektor zeigen |
| `zeit` | gemessene Zeit, `null` wenn keine |
| `gerechnet` | die Zeit ist nicht gemessen, sondern Rundenzeit minus übrige Sektoren (nur bei Runden aus dem JSON) |
| `zaehlt` | sie kann eine Sektorbestzeit werden |
| `grund` | warum nicht, sonst `null` |

Maßgeblich für einen Leser ist `nummer`; `position` weicht nur bei
Sessions ab, die über den alten CSV-Weg kamen oder allein aus dem Export
(`quelle` `csv` oder `export`).

| `grund` | heißt |
|---|---|
| `nicht_gemessen` | an dieser Stelle steht keine Zeit |
| `randsektor` | am Rand der Aufzeichnung verworfen: Die Aufnahme begann mitten im Sektor oder endete vor dem Ziel |
| `unbekannt` | keine Zeit, aber ob verworfen oder nie gemessen, ist bei diesem älteren Eintrag nicht mehr zu sagen |
| `gerechnet_in_teilrunde` | gerechnete Zeit in einer Teilrunde — die Differenz zu einer Rundenzeit, von der niemand weiß, was sie umfasst |
| `unter_halbem_median` | kürzer als `sektor_schwelle` mal Median dieser Stelle; eine verpasste Splitüberfahrt hat Zeit in den Nachbarsektor verschoben. Der Median steht in der Auswertung |
| `runde_unstimmig` | die Sektoren der Runde ergeben nicht ihre Rundenzeit |
| `ausserhalb_einteilung` | die Stelle gehört nicht zur geltenden Einteilung |
| `session_nicht_gewertet` | der `status` der Session schließt sie aus |

### Eine Auswertung

Je Strecke und Konfiguration: `strecke`, `konfiguration`, `layout_nr`
(nummeriert, wenn eine Strecke mehrere Konfigurationen hat), `konfig_id`
der geltenden Einteilung, `layout` (die geltenden Positionen, `null` ohne
Einteilung) und `sessions_abweichend`. Darin je Fahrzeug — Sektoren
verschiedener Fahrzeuge werden nie gemischt:

| Feld | heißt |
|---|---|
| `sessions` | die gewerteten Sessions dieses Fahrzeugs |
| `mediane` | je Sektor `median` und `grenze`, an denen `unter_halbem_median` gemessen wurde |
| `aussortiert` | wie viele Sektorzeiten darunter lagen |
| `bestrunde` | die schnellste zählende Runde, als Verweis |
| `top_runden` | die drei schnellsten, als Verweise |
| `streuung` | Mittel aus zweit- und drittbester minus Bestrunde |
| `golden_lap` | Summe der Sektorbestzeiten über alle Sessions, mit `teile`: je Sektor die Runde, aus der er stammt, und ob sie eine `teilrunde` war |
| `golden_lap_ohne_teilrunden` | dasselbe nur aus vollständigen Runden; `zeit` `null`, wenn zu einem Sektor keine vorliegt |
| `delta` | Golden Lap minus Bestrunde |
| `unglaubwuerdig` | Golden Lap mehr als fünfzehn Prozent unter der Bestrunde |
| `je_turn`, `je_tag` | Bestrunde und theoretische Runde innerhalb einer Session bzw. eines Tages |

Ein Verweis ist `{session, nr, zeit}`: die Kennung der Session und die
Rundennummer darin.

### Die CSV-Exporte

Je Session eine Datei `<id>_bikemode.csv`, so wie racebox.pro sie im
Browser exportiert: Zeitformat UTC, Geschwindigkeit in km/h, Höhe in
Metern, Motorradmodus an, Zeilenende LF, UTF-8. Die Datei hat zwei Teile.

**Der Kopfblock**, Zeile für Zeile `Schlüssel,Wert`:

```
Format,RaceBox CSV
Data Source,RaceBox 1000000001
Date UTC,2026-08-10T17:16:45+00:00
Session Index,1
Track,Talkurs
Configuration,Grand Prix
Laps,3
Best Lap Time,89.500
Lap 1, 16.000, sectors, 0,0,0,0,16.000
Lap 2, 90.000, sectors, 30.000,25.000,20.000,0,15.000
```

Die Rundenzeilen führen nur abgeschlossene Runden. Die Zahl der
Sektorfelder ist fest; unbenutzte stehen als `0`, auch mittendrin, und
das letzte Feld ist immer das Stück vom letzten Splitpunkt ins Ziel. Wer
die Zusammenfassung liest, braucht diese Zeilen nicht — dort stehen die
Sektoren schon richtig nummeriert und beurteilt. Vorsicht bei `Time` im
Kopf, falls vorhanden: Das ist UTC im 12-Stunden-Format, nicht die
Startzeit.

**Nach einer Leerzeile die Messpunkte**, eingeleitet von der
Spaltenzeile:

```
Record,Time,Latitude,Longitude,Altitude,Speed,GForceX,GForceZ,Lap,LeanAngle,...
1,2026-08-25T13:00:00.000Z,50.5,13.6,262.4,36.00,0.1,1.0,0,-4.4,...
```

Die Spalten werden über ihren Namen in dieser Zeile gefunden, nie über
ihre Stelle. Golden Lap selbst liest nur `Record`, `Time`, `Latitude`,
`Longitude`, `Speed` und `Lap`; was die übrigen bedeuten, ist nicht
nachgemessen.

| Spalte | heißt |
|---|---|
| `Record` | laufende Nummer des Messpunkts. Auf sie beziehen sich alle `record_von` und `record_bis` |
| `Time` | Zeitpunkt in UTC, `2026-08-25T13:36:33.480Z` |
| `Latitude`, `Longitude` | Position in Grad; `0/0` heißt kein Fix |
| `Speed` | km/h, aus der Dopplerverschiebung gemessen — ruhiger als der Abstand zweier Positionen |
| `Lap` | zu welcher Runde der Punkt gehört, die Nummer aus dem Kopfblock; `0` heißt zu keiner (Boxengasse, Ein- und Ausfahrt, Stehen) |

Die Box misst mit 25 Hz. Setzt sie aus, fehlen Zeilen, und `Time` springt;
ob `Record` dann ebenfalls springt, ist nicht nachgemessen. Eine Lücke
also an `Time` erkennen, nicht an `Record`.

**Zahlen immer mit Punkt als Dezimaltrennzeichen lesen**, nie über die
Ländereinstellung des Rechners: Auf einem deutschen System wird aus
`15.701` sonst 15701, und der Fehler fällt erst bei der Auswertung auf.

## Was die Zusammenfassung im Cache braucht

Zwei Dinge wusste der Cache vorher nicht, und beide ohne neuen Download:

- **Die Grenzen je Runde und die verworfenen Messpunkte.**
  `runden_aus_export` rechnete beides schon — die Grenzen für die Probe,
  die Punkte für die Kilometer — und behielt nur Summen. Jetzt stehen sie
  in `kennzahlen.runden_grenzen` und `kennzahlen.verworfene_punkte`;
  `RUNDEN_VERSION` ist deshalb 4, und jeder Export wird einmal neu von der
  Platte gelesen.
- **Welche Randsektoren verworfen wurden**, in `randsektoren` je Runde.
  Nur Runden aus dem JSON haben welche. `CACHE_VERSION` bleibt stehen — ein
  neuer Download für Sessions, die meist gar nicht betroffen sind, wäre
  teurer als die ehrliche Antwort `unbekannt`.

## Wenn nur die Exporte da sind

Jeder Lauf schaut vor allem anderen in `csv-exports`: Ein Export, zu dem
der Cache keinen Eintrag hat, wird gelesen und als Session abgelegt —
auch mit `--nur-cache`, dafür ist es da. Wer keine Sitzung aus dem
Browser hat, kommt so trotzdem zu Golden Lap und Zusammenfassung.

- **Die Kennung steht nur im Dateinamen.** Im Export selbst kommt sie
  nicht vor. Übernommen wird deshalb nur, was `<24 Hexzeichen>_bikemode.csv`
  heißt und mit einem RaceBox-Kopf beginnt; jede andere CSV-Datei wird mit
  Grund genannt und liegen gelassen.
- **Der Eintrag ist einer des alten CSV-Wegs ohne Sessionseite**:
  Runden und Sektoren aus dem Kopfblock, Tag und Startzeit in UTC, kein
  Fahrzeug, Fassung 1. Das nächste Holen ersetzt ihn deshalb durch den
  vollständigen; ein Eintrag aus dem Netz wird umgekehrt nie durch einen
  Export ersetzt.
- **Was `runden_aus_export` über die Datei sagt, steht unter `export`,
  nicht unter `kennzahlen`.** Die Statistik hält die Kilometer des Exports
  gegen die der Telemetrie; stünden beide aus derselben Datei da, bestätigte
  der Export sich selbst. Die Session zählt in der Statistik deshalb als
  eine ohne Telemetrie. Neu gelesen wird der Export nur, wenn
  `RUNDEN_VERSION` steigt.

## Testen

```sh
python3 selbsttest.py
```

724 Zusicherungen. Ein echter HTTP-Server auf 127.0.0.1 spielt racebox.pro
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
  `python3 mutationen.py`. Es baut 195 Fehler ein, die ein Mensch wirklich
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
Muster nachziehen lässt — und zu jeder benannten Antwort Statuscode und
Kopfzeilen in `<name>.kopfzeilen`, auch bei einem 403 oder 500. Die
Antwort auf den Login selbst liegt als `login.kopfzeilen` und `login.html`
daneben; dort steht, ob der 302 kam, welche Kekse gesetzt wurden und wer
überhaupt geantwortet hat. Die Kekse darin sind Sitzungsmerkmale — vor dem
Weitergeben schwärzen.

**Was RaceBox falsch misst, misst auch dieses Werkzeug falsch.** Die beiden
Quellen stammen aus derselben Datenbank; sie gegeneinander zu halten prüft
die Rechnung, nicht die Wirklichkeit.
