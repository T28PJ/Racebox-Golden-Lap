#!/usr/bin/env python3
#
# Racebox Golden Lap -- Copyright (C) 2026 T28PJ
#
# Dieses Programm ist freie Software: Du darfst es weitergeben und
# veraendern unter den Bedingungen der GNU General Public License,
# Version 3, wie von der Free Software Foundation veroeffentlicht.
# Weitergegeben wird es in der Hoffnung, dass es nuetzlich ist, aber
# OHNE JEDE GEWAEHRLEISTUNG. Einzelheiten stehen in der Datei LICENSE
# und unter <https://www.gnu.org/licenses/>.
"""Mutationstest: veraendert rb-golden-lap.py absichtlich, erwartet Rot.

Ein Test, der nie rot war, prueft nichts. Dieses Werkzeug baut je einen
Fehler ein, den ein Mensch wirklich machen koennte, laesst den Selbsttest
darauf los und meldet, wenn er trotzdem gruen bleibt -- dann ist die
betreffende Zusicherung blind.

    python3 mutationen.py                  alle
    python3 mutationen.py statistik        nur die, deren Name das enthaelt
    python3 mutationen.py -j 8             mit acht gleichzeitig

Wer eine Pruefung ergaenzt, braucht die passende Mutation und nicht alle
uebrigen. Der Filter ist deshalb kein Luxus, sondern der Normalfall: Vier
neue Mutationen kosten damit eine Minute statt einer Viertelstunde.

Gelaufen wird gleichzeitig. Jede Mutation bekommt ihren eigenen Ordner und
ihren eigenen Prozess, die stoeren einander nicht -- und der Selbsttest
wartet die meiste Zeit auf Sockets und Zeitgrenzen, nicht auf Rechenzeit.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HIER = os.path.dirname(os.path.abspath(__file__))

# Vier Kerne sind das Uebliche, und der Selbsttest ist zur Haelfte Warten.
GLEICHZEITIG = 4

# (Name, was ersetzt wird, wodurch)
MUTATIONEN = [
    ('theoretische Runde ignoriert Teilrunden',
     'beste = bester_sektor(alle_runden, pos, nur_vollstaendig)',
     'beste = bester_sektor(alle_runden, pos, True)'),
    ('Download laeuft bis zum Ende durch',
     'while nur_bis not in puffer and len(puffer) < 256 * 1024:',
     'while len(puffer) < 100 * 1024 * 1024:'),
    ('Fahrzeuge werden zusammengeworfen',
     "session.get('fahrzeug', 'ohne Fahrzeug'), []).append(session)",
     "'alle', []).append(session)"),
    ('Teilrunden gar nicht erst exportieren',
     "'includeEntryExit': '1',", ''),
    ('Layout der aeltesten statt der neuesten Session',
     'nach_datum = sorted(sessions, key=sortier_schluessel, reverse=True)',
     'nach_datum = sorted(sessions, key=sortier_schluessel)'),
    ('Zeitformat rechnet Minuten nicht heraus',
     "s = '%d:%05.2f' % (minuten, rest)", "s = '%.2f' % kurz"),
    ('Hundertstel werden gerundet statt gekuerzt',
     'return int(genau * 100 + schub) / 100.0',
     'return round(genau, 2)'),
    ('Zahlen locale-abhaengig lesen',
     'return float(text.strip())',
     "return float(text.strip().replace('.', ''))"),
    ('eine Strecke steht wieder im Quelltext',
     'AUSBLENDEN_VORGABE = []', "AUSBLENDEN_VORGABE = ['Uebungsplatz*']"),
    ('aufgenommene Muster landen nicht in der Datei',
     "f.write('\\n'.join(dazu) + '\\n')", 'pass'),
    ('Ausblendliste greift nicht',
     'return any(fnmatch.fnmatch(strecke.lower(), m.lower()) for m in muster)',
     'return False'),
    ('Sektoren nach Feldposition statt durchgezaehlt anzeigen',
     'return layout.index(pos) + 1', 'return pos'),
    ('Datenordner wieder im Benutzerprofil',
     "or os.path.dirname(os.path.abspath(__file__)))",
     "or os.path.join(os.path.expanduser('~'), '.racebox-sektoren'))"),
    ('Umgebungsvariable fuer den Datenordner wird nicht gelesen',
     "return (os.environ.get('RB_GOLDEN_LAP_DIR')",
     "return (os.environ.get('EGAL')"),
    ('Verbindung wird nicht offen gehalten',
     'if antwort.will_close:', 'if True:'),
    ('alles nacheinander statt parallel',
     'with ThreadPoolExecutor(max_workers=gleichzeitig) as arbeiter:',
     'with ThreadPoolExecutor(max_workers=1) as arbeiter:'),
    ('erste Adresse bekommt die volle Zeitgrenze',
     'dose.settimeout(min(zeitgrenze, VERBINDUNGSGRENZE))',
     'dose.settimeout(zeitgrenze)'),
    ('der JSON-Weg wird gar nicht erst versucht',
     'if not csv_weg:', 'if False:'),
    ('Schlusssektor wird nicht ausgerechnet',
     'if sektoren[-1] == 0 and rest > 0:\n            sektoren[-1] = rest',
     'if False:\n            sektoren[-1] = rest'),
    ('mitgeliefertes Schlussstueck wird ueberschrieben',
     'if sektoren[-1] == 0 and rest > 0:', 'if rest > 0:'),
    ('unstimmige Runden liefern doch Sektorbestzeiten',
     "if pos in r['sektoren'] and r['stimmig']\n               and (r['vollstaendig'] or not nur_vollstaendig)]",
     "if pos in r['sektoren']\n               and (r['vollstaendig'] or not nur_vollstaendig)]"),
    ('eine unglaubwuerdige Golden Lap wird verschwiegen',
     "'unglaubwuerdig': bool(theo and best and theo < best['zeit'] * 0.85),",
     "'unglaubwuerdig': False,"),
    ('Randsektor am Anfang zaehlt mit',
     'if beginn < rate:            # weniger als eine Sekunde nach dem Start',
     'if False:'),
    ('Randsektor am Ende zaehlt mit',
     "if linien is not None and len(runden) > linien:", 'if False:'),
    ('Kennzahlen ohne Fahrschwelle',
     'FAHRSCHWELLE = 5.0        # km/h', 'FAHRSCHWELLE = 0.0'),
    ('Fahrzeugfilter greift nicht',
     'beschraenkt = [i for i in fehlend if i in erlaubt]',
     'beschraenkt = list(fehlend)'),
    ('--seit haelt nicht an',
     "if seit and all(e['datum'] < seit for e in eintraege if e):",
     'if False:'),
    ('blanke Zahlen werden wieder als Sektorzeiten gelesen',
     'if not isinstance(sektor, dict):\n                continue',
     'if not isinstance(sektor, dict):\n                sektor = {\'time\': sektor}'),
    ('eine unlesbare Session reisst den Lauf mit',
     'except Exception as e:\n            with schloss:',
     'except ValueError as e:\n            with schloss:'),
    ('Strg+C an der Auswahl wirft einen Traceback',
     'except (EOFError, KeyboardInterrupt):', 'except EOFError:'),
    ('Median ist der Mittelwert',
     'return (geordnet[mitte - 1] + geordnet[mitte]) / 2.0',
     'return sum(geordnet) / len(geordnet)'),
    ('Fahrzeugfilter greift in der Anzeige nicht',
     'if ist_ausgeblendet(z[\'fahrzeug\'], muster):', 'if True:'),
    ('beim Start wird immer geholt',
     "return not antwort.strip().lower().startswith('n')", 'return True'),
    ('Streuung wird nicht gerechnet',
     "'streuung': (mittelwert([r['zeit'] for r in top[1:]]) - top[0]['zeit'])",
     "'streuung': (None"),
    ('Streuung nur aus der drittbesten Runde',
     "mittelwert([r['zeit'] for r in top[1:]])", "top[-1]['zeit']"),
    ('Streckenname nicht in Grossbuchstaben',
     "% (eintrag_nr, kurzname(e).upper()))", '% (eintrag_nr, kurzname(e)))'),
    ('Beschriftung im Detailkopf wieder von Hand ausgezaehlt',
     "            zeile('%d. beste' % rang, runde['zeit'], herkunft(runde))",
     "            melde('  %d. beste      %s   %s'\n"
     "                  % (rang, zeit_text(runde['zeit'], 10), "
     "herkunft(runde)))"),
    ('doppelte Sessions bleiben doppelt',
     'vorher = nach_lauf.get(schluessel)', 'vorher = None'),
    ('von zwei Fassungen wird die kuerzere gezaehlt',
     "(vorher, session), key=lambda x: -len(x.get('runden', [])))",
     "(vorher, session), key=lambda x: len(x.get('runden', [])))"),
    ('der Exportvergleich ordnet ueber die Rundennummer',
     "if abs(k['zeit'] - lang_runde['zeit']) < 0.0005]",
     "if k['nr'] == lang_runde['nr']]"),
    ('abweichende Sektoren im Zweitexport fallen nicht auf',
     "if lang_runde['sektoren'] != kurz_runde['sektoren']:",
     'if False:'),
    ('ein fehlender Originalexport wird uebergangen',
     "eintrag['fehlend'].append(pfad)", 'pass'),
    ('gerechneter Schlusssektor zaehlt auch in Teilrunden',
     'if not vollstaendig and abgeleitet in werte:', 'if False:'),
    ('gerechneter Schlusssektor wird nicht markiert',
     'abgeleitet = len(sektoren)', 'abgeleitet = None'),
    ('Luecke aus den aussortierten statt den urspruenglichen Werten',
     "'luecke': round(runde['zeit'] - sum(sektoren), 3),",
     "'luecke': round(runde['zeit'] - sum(werte.values()), 3),"),
    ('stimmig wird aus dem bereinigten Feld nachgerechnet',
     "stimmig = runde.get('stimmig', True)",
     "stimmig = runde.get('stimmig', abs(sum(sektoren) - runde['zeit']) < 0.051)"),
    ('der CSV-Abgleich vergleicht die Sektoren nicht',
     'if abs(aus_c - aus_j) > 0.0005:', 'if False:'),
    ('der Originalexport wird nicht abgelegt',
     'os.replace(vorlaeufig, pfad)\n    return pfad', 'return pfad'),
    ('der Abgleich vergleicht gegen die bereinigten Runden',
     'aus_json = runden_aus_meta(meta, len(konfig.get(\'splitLines\') or []))',
     'aus_json = session_aus_json(sid, daten)[\'runden\']'),
    ('Abgleich ordnet ueber die Position statt ueber die Rundenzeit',
     "passend = [j for j in offen\n                   if abs(j['zeit'] - csv_runde['zeit']) < 0.0005]",
     'passend = offen[:1]'),
    ('Runden kommen wieder aus dem JSON',
     'if csv_runden:\n        runden = csv_runden_umrechnen(csv_runden, splits)',
     'if False:\n        runden = csv_runden_umrechnen(csv_runden, splits)'),
    ('CSV-Felder werden nicht auf Sektorpositionen umgerechnet',
     'dicht = zwischen + [feld[-1] if feld else 0.0]',
     'dicht = list(feld)'),
    ('unmoegliche Sektorzeiten bleiben drin',
     'schwelle = median * SEKTOR_SCHWELLE', 'schwelle = 0'),
    ('die Schwelle urteilt wieder ueber die Fahrleistung',
     'SEKTOR_SCHWELLE = 0.5', 'SEKTOR_SCHWELLE = 0.95'),
    ('die Fahrzeugauswahl waehlt nicht aus',
     'return fahrzeuge[int(wahl) - 1]', 'return None'),
    ('die Detailansicht zeigt trotz Auswahl alle Fahrzeuge',
     'if nur is not None and z is not nur:', 'if False:'),
    ('`q` wird nirgends als Ausgang erkannt',
     '    return ENDE if antwort.strip().lower() in AUSGANG else antwort',
     '    return antwort'),
    ('der Ausgang steht nicht in der Auswahlzeile',
     "'s = Statistik, Enter oder q = Ende: '",
     "'s = Statistik, Enter = Ende: '"),
    ('`q` an der Startfrage beendet nicht',
     "    if antwort is ENDE:\n        raise SystemExit('Beendet.')",
     "    if False:\n        raise SystemExit('Beendet.')"),
    ('die Startfrage nennt ihren Ausgang nicht',
     "'Neue Sessions von racebox.pro holen? [J/n, q = beenden] '",
     "'Neue Sessions von racebox.pro holen? [J/n] '"),
    ('`q` in der Fahrzeugauswahl beendet statt zurueckzugehen',
     '        return ENDE\n    if wahl is None:',
     '        return None\n    if wahl is None:'),
    ('die Fahrzeugauswahl nennt ihren Ausgang nicht',
     "'Fahrzeug waehlen (Nummer, Enter = alle, q = zurueck): '",
     "'Fahrzeug waehlen (Nummer, Enter = alle): '"),
    ('das Anhalten nach einer Ansicht kennt keinen Ausgang',
     '    return None if antwort is None or antwort is ENDE else antwort',
     '    return antwort'),
    ('nach dem Zurueck wird die Detailansicht doch gezeigt',
     '        if gewaehlt is ENDE:\n            continue',
     '        if False:\n            continue'),
    ('die Zugangsfrage nennt ihren Ausgang nicht',
     "'RaceBox-E-Mail (leer = abbrechen): '", "'RaceBox-E-Mail: '"),
    ('die Lap-Spalte wird nicht ausgewertet',
     '                    if runde > 0:\n'
     '                        meter += v / 3.6 * dt',
     '                    if True:\n'
     '                        meter += v / 3.6 * dt'),
    ('Rundenkilometer ohne Fahrschwelle',
     '                if v > FAHRSCHWELLE:\n'
     '                    meter_alle += v / 3.6 * dt',
     '                if True:\n'
     '                    meter_alle += v / 3.6 * dt'),
    ('die Probe gegen die Rundenzeiten wird nicht gerechnet',
     '    probe = max((abs(wanduhr.get(nr, 0.0) - soll) for nr, soll in '
     'kopfrunden),\n                default=0.0)',
     '    probe = 0.0'),
    ('ein Export ohne Lap-Spalte liefert doch Zahlen',
     '                    if not gebraucht <= set(spalten):',
     '                    if False:'),
    ('die Rundenkilometer werden nicht in den Cache geschrieben',
     '        cache_schreiben(session, cache_ordner)\n        ergaenzt += 1',
     '        ergaenzt += 1'),
    ('ein Export ohne Lap-Spalte wird jedes Mal neu gelesen',
     "        session['kennzahlen'].update(werte or {'meter_runden': None})",
     "        session['kennzahlen'].update(werte or {})"),
    ('Rundenkilometer werden nicht summiert',
     '                summe[feld] += k.get(feld) or 0',
     '                summe[feld] = k.get(feld) or 0'),
    ('fehlende Rundenaufteilungen werden verschwiegen',
     "            summe['ohne_runden_km'] += 1", '            pass'),
    ('die Probe ist die letzte statt der groessten',
     "            if (k.get('runden_probe') or 0.0) > summe['runden_probe']:",
     "            if True:"),
    ('der Anteil wird nicht ins Verhaeltnis gesetzt',
     "        s = '%.0f %%' % (100.0 * teil / ganz)",
     "        s = '%.0f %%' % teil"),
    ('die Tabelle zeigt wieder die Kilometer gesamt',
     "                 km_text(z['meter'], stellen=0),",
     "                 km_text(z['meter_gesamt'], stellen=0),"),
    ('die Gegenprobe zwischen den Quellen meldet sich nicht',
     "    if g['mit_runden_km'] and unterschied > g['mit_runden_km']:",
     '    if False:'),
    ('die Probe der Rundengrenzen meldet sich nicht',
     "    if g['sessions_probe']:\n        # Benannt wird die auffaelligste",
     "    if False:\n        # Benannt wird die auffaelligste"),
    ('die auffaellige Session wird nicht benannt',
     "                summe['probe_session'] = '%s Turn %s' % (\n"
     "                    session.get('datum', '?'), session.get('turn', '?'))",
     "                summe['probe_session'] = None"),
    ('Sektoren aus Teilrunden werden nicht gezaehlt',
     "        'aus_teilrunden': sum(1 for _, r in teile "
     "if not r['vollstaendig']),",
     "        'aus_teilrunden': 0,"),
    ('die Golden Lap aus einer Teilrunde wird nicht markiert',
     "            golden = '(TR) ' + golden", '            pass'),
    ('die Marke verschiebt die Spalte',
     "            melde('      %-22s%7d %10s %9s %11s %12s%8s%s'",
     "            melde('      %-22s%7d %10s %9s %11s %11s %8s%s'"),
    ('die Marke wird unter der Uebersicht nicht erklaert',
     "    teilrunden = [z for e in strecken for z in e['fahrzeuge']\n"
     "                  if z['aus_teilrunden']]",
     '    teilrunden = []'),
    ('die Detailansicht nennt den Grund fuer die Marke nicht',
     "                  '%d Sektorbestzeit(en) aus einer Ein-/Ausfahrrunde'\n"
     "                  % z['aus_teilrunden'])",
     "                  '')"),
    ('die Golden Lap ohne Teilrunden ist dieselbe Zahl',
     "                zeile('Golden Lap', z['theo_streng'],",
     "                zeile('Golden Lap', z['theo'],"),
    ('der Sektor ohne Teilrunde wird nicht danebengestellt',
     "            streng = z['streng_je_sektor'].get(pos)",
     '            streng = None'),
    ('die Sektorbestzeit ohne Teilrunde nimmt doch Teilrunden',
     "        'streng_je_sektor': {pos: bester_sektor(alle, pos, True)",
     "        'streng_je_sektor': {pos: bester_sektor(alle, pos, False)"),
    ('die Kilometerspalten bekommen wieder eine Nachkommastelle',
     "                 km_text(z['meter'], stellen=0),\n"
     "                 km_text(z['meter_runden'], stellen=0),",
     "                 km_text(z['meter']), km_text(z['meter_runden']),"),
    ('der Tabellenkopf nennt keine Einheiten',
     "    melde('    %-19s%6s%7s%8s%10s%12s%8s%13s'\n"
     "          % ('', '', '', '', 'km', 'km', 'km/h', 'Grad'))",
     '    pass'),
    ('ein Punkt ohne Position zaehlt mit',
     '    ohne = [i for i, (_, b, l) in enumerate(punkte) '
     'if not am_ort(b, l, mitte)]',
     '    ohne = []'),
    ('nach einem Ausfall gilt sofort wieder alles',
     '        for richtung in (-1, 1):', '        for richtung in ():'),
    ('die Mitte der Aufzeichnung ist der Mittelwert statt des Medians',
     '    return breiten[mitte], laengen[mitte]',
     '    return (sum(breiten) / len(breiten),\n'
     '            sum(laengen) / len(laengen))'),
    ('die Ortsgrenze greift nicht',
     'ORTSGRENZE = 100000        # Meter',
     'ORTSGRENZE = 100000000        # Meter'),
    ('verworfene Messpunkte werden als gemessen gezaehlt',
     "        'punkte': len(zeilen) - len(ungueltig),",
     "        'punkte': len(zeilen),"),
    ('verworfene Messpunkte werden verschwiegen',
     "    if g['punkte_ohne_ort']:\n        melde('  %d von %d Messpunkten "
     "verworfen -- dort hatte der '",
     "    if False:\n        melde('  %d von %d Messpunkten "
     "verworfen -- dort hatte der '"),
    ('der Export uebergeht den Ausfall nicht',
     '        if nr in ungueltig:\n            vorige = None',
     '        if False:\n            vorige = None'),
    ('der Rundenschnitt rechnet mit der gesamten Fahrzeit',
     "            summe['meter_runden'] / summe['fahrzeit_runden'] * 3.6, 2)",
     "            summe['meter_runden'] / summe['fahrzeit'] * 3.6, 2)"),
    ('die Hoechstwerte zeigen wieder die langsamste Geschwindigkeit',
     "          % ('Hoechstwerte', wert_text(g['v_max'], stellen=0),",
     "          % ('Hoechstwerte', wert_text(g['v_min'], stellen=0),"),
    ('auffaellige Rundenproben werden nicht gezaehlt',
     "            if (k.get('runden_probe') or 0.0) > PROBE_GRENZE:\n"
     "                summe['sessions_probe'] += 1",
     "            if False:\n                summe['sessions_probe'] += 1"),
    ('die Gegenprobe vergleicht ueber verschiedene Mengen',
     "    unterschied = abs(g['meter_export'] - g['meter_beide'])",
     "    unterschied = abs(g['meter_export'] - g['meter'])"),
    ('die Rundenprobe zieht die eigenen Auslassungen ab',
     '        if vorige_uhr is not None and jetzt > vorige_uhr:',
     '        if vorige_uhr is not None and 0 < jetzt - vorige_uhr < 5:'),
    ('alte Rundenzahlen bleiben nach einer Korrektur stehen',
     "             and s['kennzahlen'].get('runden_version', 0) "
     "< RUNDEN_VERSION]",
     "             and 'meter_runden' not in s['kennzahlen']]"),
    ('die Kilometer im Block bekommen wieder eine Nachkommastelle',
     "    zeile('aufgezeichnet', km_text(g['meter_gesamt'], stellen=0),",
     "    zeile('aufgezeichnet', km_text(g['meter_gesamt']),"),
    ('die Geschwindigkeiten bekommen wieder eine Nachkommastelle',
     "        return 'Schnitt %s km/h' % wert_text(v, 3, 0)",
     "        return 'Schnitt %s km/h' % wert_text(v, 5, 1)"),
    ('Anfragen ohne Zeitgrenze',
     'dose.settimeout(zeitgrenze)\n            return dose',
     'dose.settimeout(None)\n            return dose'),
    ('Kennzahlen werden nicht summiert, sondern ueberschrieben',
     "                     'punkte', 'punkte_ohne_ort'):\n"
     '            summe[feld] += k.get(feld) or 0',
     "                     'punkte', 'punkte_ohne_ort'):\n"
     '            summe[feld] = k.get(feld) or 0'),
    ('die Runden werden nicht gezaehlt',
     "        summe['runden'] += len(session.get('runden', []))",
     "        summe['runden'] += 1"),
    ('der Schnitt rechnet die Standzeit mit',
     "summe['v_schnitt'] = round(summe['meter'] / summe['fahrzeit'] * 3.6, 2)",
     "summe['v_schnitt'] = round(summe['meter_gesamt']\n"
     "                                   / summe['gesamtzeit'] * 3.6, 2)"),
    ('Sessions ohne Telemetrie werden verschwiegen',
     "summe['ohne_kennzahlen'] += 1", "summe['ohne_kennzahlen'] = 0"),
    ('die niedrigste Geschwindigkeit ist die zuletzt gesehene',
     "summe['v_min'] = (k['v_min'] if summe['v_min'] is None\n"
     "                              else min(summe['v_min'], k['v_min']))",
     "summe['v_min'] = k['v_min']"),
    ('die groesste Schraeglage ist die zuletzt gesehene',
     'summe[feld] = max(summe[feld], k.get(feld) or 0.0)',
     'summe[feld] = k.get(feld) or 0.0'),
    ('in der Statistik zaehlen doppelte Sessions zweimal',
     'sessions, doppelte = doppelte_entfernen(sessions)', 'doppelte = []'),
    ('die Statistik ignoriert die Ausblendliste',
     "            ausgeblendet.setdefault(name, []).append(session)\n"
     "        else:\n            behalten.append(session)",
     "            behalten.append(session)\n"
     "        else:\n            behalten.append(session)"),
    ('ein Fahrzeugmuster ohne Treffer blendet alles aus',
     '        if passend:\n            behalten = passend',
     '        if True:\n            behalten = passend'),
    ('Kilometer bleiben Meter',
     "'%.*f' % (stellen, meter / 1000.0)", "'%.*f' % (stellen, meter)"),
    ('ohne jede Aufteilung stehen null Kilometer in Runden',
     "    if not summe['mit_runden_km']:\n"
     "        summe['meter_runden'] = None",
     "    if False:\n        summe['meter_runden'] = None"),
    ('das Stundenformat rechnet die Stunden nicht heraus',
     "s = '%d:%02d:%02d' % (ganz // 3600, ganz % 3600 // 60, ganz % 60)",
     "s = '%d:%02d' % (ganz // 60, ganz % 60)"),
    ('die Statistik nummeriert Layouts nicht durch',
     '        if len(konfigs) > 1:\n'
     '            for nr, konfig in enumerate(sorted(konfigs), 1):',
     '        if False:\n'
     '            for nr, konfig in enumerate(sorted(konfigs), 1):'),
    ('die Layoutnummer steht unerklaert da',
     "    mehrere = [z for z in stat['je_strecke'] if z.get('layout_nr')]",
     '    mehrere = []'),
    ('ein Cache ohne Telemetrie nennt keinen Ausweg',
     "        melde('  Keine der %d Session(s) hat Telemetrie -- ein Lauf "
     "mit '\n              'Netz holt sie.' % g['sessions'])",
     "        melde('  Keine der %d Session(s) hat Telemetrie.'\n"
     "              % g['sessions'])"),
    ('--strecke wirkt neben --statistik nicht',
     "            stat = statistik_bauen(\n"
     "                [s for s in sessions\n"
     "                 if (s.get('strecke') or '(ohne Strecke)')\n"
     "                 == gewaehlt['strecke']], muster, fahrzeugmuster)",
     '            pass'),
    ('die Statistik steht nicht in der Eingabezeile',
     "'Auswahl -- Nummer oder Name = Strecke, '\n"
     "                     's = Statistik, Enter oder q = Ende: '",
     "'Auswahl -- Nummer oder Name = Strecke, '\n"
     "                     'Enter oder q = Ende: '"),
]


def pruefen(mutation):
    """Eine Mutation einbauen und den Selbsttest darauf loslassen.

    Return (Name, wie, blind). `wie` ist, was in der Zeile steht.
    """
    name, alt, neu = mutation
    ordner = tempfile.mkdtemp()
    try:
        for datei in ('rb-golden-lap.py', 'selbsttest.py'):
            shutil.copy(os.path.join(HIER, datei), ordner)
        pfad = os.path.join(ordner, 'rb-golden-lap.py')
        with open(pfad, encoding='utf-8') as f:
            text = f.read()
        if alt not in text:
            return name, '??', True
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(text.replace(alt, neu, 1))
        try:
            lauf = subprocess.run(
                [sys.executable, 'selbsttest.py'], cwd=ordner,
                capture_output=True, text=True, timeout=120)
            if lauf.returncode != 0:
                return name, 'rot', False
        except subprocess.TimeoutExpired:
            # Auch das ist Rot: Der Selbsttest kommt nicht mehr zum Ende.
            return name, 'rot (haengt)', False
        return name, 'BLIND', True
    finally:
        shutil.rmtree(ordner)


def argumente(argv):
    """Filter und Zahl der gleichzeitigen Laeufe aus der Kommandozeile."""
    filter_, gleichzeitig, i = '', GLEICHZEITIG, 0
    while i < len(argv):
        if argv[i] in ('-j', '--gleichzeitig') and i + 1 < len(argv):
            gleichzeitig = max(1, int(argv[i + 1]))
            i += 2
        else:
            filter_ = argv[i]
            i += 1
    return filter_, gleichzeitig


def main(argv=None):
    filter_, gleichzeitig = argumente(argv if argv is not None
                                      else sys.argv[1:])
    gewaehlt = [m for m in MUTATIONEN
                if not filter_ or filter_.lower() in m[0].lower()]
    if not gewaehlt:
        print('Keine Mutation passt zu %r. Vorhanden sind:' % filter_)
        for name, _, _ in MUTATIONEN:
            print('  %s' % name)
        return 1
    if filter_:
        print('%d von %d Mutationen, gefiltert nach %r'
              % (len(gewaehlt), len(MUTATIONEN), filter_))

    # Die Reihenfolge der Ausgabe ist die der Liste und nicht die des
    # Fertigwerdens: Zwei Laeufe hintereinander sollen dasselbe zeigen.
    with ThreadPoolExecutor(max_workers=gleichzeitig) as arbeiter:
        ergebnisse = list(arbeiter.map(pruefen, gewaehlt))

    blind = []
    for name, wie, ist_blind in ergebnisse:
        if wie == '??':
            print('  ??     %-52s Muster nicht mehr im Code' % name)
        elif ist_blind:
            print('  BLIND  %-52s bleibt gruen!' % name)
        else:
            print('  %-6s %s' % (wie, name))
        if ist_blind:
            blind.append(name)

    print()
    if blind:
        print('%d von %d Mutationen bleiben unbemerkt:'
              % (len(blind), len(gewaehlt)))
        for name in blind:
            print('  %s' % name)
        return 1
    print('Alle %d Mutationen werden erkannt.' % len(gewaehlt))
    return 0


if __name__ == '__main__':
    sys.exit(main())
