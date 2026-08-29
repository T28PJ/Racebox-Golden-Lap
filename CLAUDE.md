# Racebox Golden Lap

Ein Werkzeug: die beste theoretische Runde aus einem RaceBox-Konto, über
Turns und Fahrtage hinweg. Was es tut und wie man es bedient, steht in
`README.md`. Wie die Schnittstelle von racebox.pro aussieht, woher die
Runden kommen und was aus welchem Grund verworfen wird, steht in
`TECHNIK.md` — vor Änderungen dort lesen.

| Datei | Inhalt |
|---|---|
| `rb-golden-lap.py` | das Werkzeug — eine Datei, nur Standardbibliothek |
| `selbsttest.py` | Tests gegen ein nachgebautes racebox.pro auf 127.0.0.1 |
| `mutationen.py` | prüft die Tests selbst: eingebaute Fehler müssen rot werden |
| `README.md` | was das Werkzeug tut und wie man es bedient |
| `TECHNIK.md` | die Schnittstelle, die Rundenquelle, was verworfen wird und warum |
| `IDEEN.md` | was noch nicht gebaut ist, aber gedacht — vor neuen Vorhaben lesen |

## Feste Regeln

- **Sprache:** Deutsch. „du", nie „ihr". „Es ergibt Sinn", nie „es macht
  Sinn". Code und Kommentare ebenfalls deutsch.
- **Nur Standardbibliothek.** Das Werkzeug muss auf einem Windows-Rechner
  ohne Installation laufen. Keine Abhängigkeiten, auch keine bequemen.
- **`python3 selbsttest.py` entscheidet, ob committet wird** — nicht der
  Blick auf eine Ausgabe. In einer Kette immer davorhängen
  (`python3 selbsttest.py && git commit …`), nie danebenstellen.
- **Ausgenommen ist, was kein Verhalten haben kann:** Kommentare,
  Docstrings, Markdown. Dort kann genau eine Sache schiefgehen — ein
  kaputtes Anführungszeichen, sodass die Datei nicht mehr übersetzt — und
  die prüft man in Millisekunden statt in vierzehn Sekunden:

  ```sh
  python3 -c "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" rb-golden-lap.py
  ```

  Die Ausnahme gilt für den **Diff**, nicht für die Absicht. Steht im selben
  Commit auch nur eine Zeile Code, entscheidet wieder der Selbsttest. Ein
  Testlauf, den niemand abwartet, weil er sich nie lohnt, sichert nichts.
- **Jeden neuen Check einmal absichtlich rot laufen lassen** — und die
  passende Mutation in `mutationen.py` hinterlegen, damit er rot bleibt.
  Ein Test, der nie rot war, prüft nichts.
- **Der volle Mutationslauf läuft nur auf ausdrückliche Ansage.** Er kostet
  Minuten und bremst damit genau das, was er absichern soll. Hinterlegt wird
  die Mutation immer, gelaufen wird sie, wenn du es sagst — und wer eine
  Prüfung ergänzt, filtert ohnehin auf seine eigene:
  `python3 mutationen.py statistik`. Was noch ungeprüft ist, gehört gesagt.
- **Rechenzeit, einmaliges Neuladen und Speicherplatz sind keine
  Argumente.** Ein paar Sekunden, ein Download, ein paar Megabyte auf der
  Platte — dafür wird keine Rechnung verbogen und keine Fallunterscheidung
  eingebaut. Was zählt, ist die richtige Zahl und der lesbare Weg dorthin.
  Teuer ist nur, was sich bei *jedem* Lauf wiederholt, ohne dass es jemand
  braucht.
- **Keine Schwelle, die etwas über den Fahrer behauptet.** „So schnell
  fährt niemand" ist keine Begründung, sondern eine Annahme — und sie
  verwirft irgendwann eine echte Bestzeit. Wo Daten unbrauchbar sind, muss
  sich das an der Rechnung zeigen lassen: eine Summe, die nicht aufgeht,
  ein Wert, der nicht gemessen, sondern abgeleitet ist. Was sich so nicht
  begründen lässt, wird gemeldet und nicht stillschweigend verworfen.
- **Keine selbst angelegten Strecken zum Prüfen benutzen.** Wer in der
  RaceBox-App eine eigene Strecke zeichnet, bekommt Eigenheiten, die es auf
  keiner Rennstrecke aus der Datenbank gibt — fehlende Splitpunkte,
  seltsame Zielgeraden, Runden, die es so nicht gibt. Was daran gemessen
  wird, ist ein Sonderfall und kein Beleg. Belege kommen von
  Datenbankstrecken; ein Sonderfall darf eine Regel bestätigen, aber nie
  begründen.
- **Keine Eigennamen im Quelltext.** Welche Strecke ein Übungsplatz ist,
  welches Motorrad jemand fährt, wie sein Konto heißt — das gehört in eine
  Datei neben dem Skript, nicht in eine Vorgabe. Das Werkzeug soll für
  jeden taugen, nicht nur für den, der es gebaut hat.
- **Daten liegen neben dem Skript**, nicht im Benutzerprofil. Wer das
  Werkzeug in einen Ordner legt, sucht seine Daten auch dort.
- **Keine Anfrage ohne Zeitgrenze.** Eine stehende Verbindung ist sonst
  von einem Absturz nicht zu unterscheiden.
- **Nichts gegen das echte racebox.pro laufen lassen, um zu probieren.**
  Das Konto gehört einem Menschen, und jede Anfrage ist eine echte. Zum
  Ausprobieren gibt es den nachgebauten Server im Selbsttest.
- **Zahlen nie locale-abhängig lesen.** racebox.pro schreibt `15.701`; auf
  einem deutschen System machte ein Locale-Parser daraus 15701. Der Fehler
  fällt erst bei der Auswertung auf, und dann falsch herum.
- **Beobachtung und Schlussfolgerung nicht vermischen.** Was gemessen wurde
  (eine Sektorzeit aus einer Ausfahrrunde) und was daraus folgt (sie zählt
  mit) sind zwei Aussagen. Die Ausgabe muss beides zeigen, nicht nur die
  zweite.
- **Commit-Nachrichten:** Deutsch, Betreff eine Zeile ohne Punkt am Ende.
  Dann Leerzeile, dann ganze Sätze: was das Problem war, warum diese
  Lösung — und **was geprüft wurde**, mit Zahl. Betreff und Text **ohne
  Umlaute** (`ue`, `ae`, `oe`, `ss`); die Dateien im Repo dagegen mit.
