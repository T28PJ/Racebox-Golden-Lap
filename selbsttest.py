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
"""Selbsttest fuer rb-golden-lap.py.

Zwei Teile: die Rechnung gegen von Hand nachgerechnete Zahlen, und der Weg
uebers Netz gegen ein nachgebautes racebox.pro auf 127.0.0.1. Geprueft wird
beobachtbares Verhalten -- welche Felder rausgehen, was im Cache landet,
was bei Fehlern passiert.

    python3 selbsttest.py
"""

import re
import io
import contextlib
import builtins
import calendar
import socket
import time
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Der Dateiname hat Bindestriche und ist damit kein gueltiger Modulname --
# geladen wird deshalb ueber den Pfad.
import importlib.util

_PFAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'rb-golden-lap.py')
_SPEC = importlib.util.spec_from_file_location('rb_golden_lap', _PFAD)
S = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(S)

GRUEN, ROT = 0, []


def pruefe(bedingung, was):
    global GRUEN
    if bedingung:
        GRUEN += 1
    else:
        ROT.append(was)
        print('  ROT: %s' % was)


def still(f, *args, **kwargs):
    """Aufrufen, ohne dass die Fortschrittsmeldungen den Lauf zumuellen."""
    with contextlib.redirect_stdout(io.StringIO()):
        return f(*args, **kwargs)


def gleich(ist, soll, was):
    pruefe(ist == soll, '%s -- ist %r, soll %r' % (was, ist, soll))


def nahe(ist, soll, was, toleranz=0.0005):
    pruefe(ist is not None and abs(ist - soll) < toleranz,
           '%s -- ist %r, soll %r' % (was, ist, soll))


# --- Testdaten ------------------------------------------------------------
#
# Vier Sektoren an den Positionen 1, 2, 3 und 5 -- Position 4 bleibt leer,
# genau wie in den echten Exporten. Die Zahlen sind rund, damit sich jede
# erwartete Summe im Kopf nachrechnen laesst.

KOPF_S1 = """\
Format,RaceBox CSV
Data Source,RaceBox 1000000001
Date UTC,2026-08-10T17:16:45+00:00
Date,10/08/2026
Time,05/16/45
Session Index,1
Session Type,Track
Track,Talkurs
Configuration,
Laps,3
Best Lap Time,89.500
Lap 1, 16.000, sectors, 0,0,0,0,16.000
Lap 2, 90.000, sectors, 30.000,25.000,20.000,0,15.000
Lap 3, 89.500, sectors, 29.000,26.000,20.000,0,14.500
"""

KOPF_S2 = """\
Format,RaceBox CSV
Date UTC,2026-08-10T18:05:00+00:00
Session Index,2
Session Type,Track
Track,Talkurs
Configuration,
Laps,3
Best Lap Time,88.300
Lap 1, 17.000, sectors, 0,0,0,0,17.000
Lap 2, 88.300, sectors, 28.500,25.500,19.500,0,14.800
Lap 3, 52.000, sectors, 28.000,24.000,0,0,0
"""

KOPF_S3 = """\
Format,RaceBox CSV
Date UTC,2026-07-01T08:00:00+00:00
Session Index,1
Session Type,Track
Track,Talkurs
Configuration,
Laps,1
Best Lap Time,110.000
Lap 1, 110.000, sectors, 35.000,30.000,25.000,0,20.000
"""

# Bergring Nord: dieselbe Strecke, aber andere Sektoreinteilung. Die
# aeltere Session muss als abweichend erkannt und herausgehalten werden.
KOPF_S4 = """\
Format,RaceBox CSV
Date UTC,2026-06-01T09:00:00+00:00
Session Index,1
Session Type,Track
Track,Bergring Nord
Configuration,
Laps,1
Best Lap Time,60.000
Lap 1, 60.000, sectors, 20.000,20.000,20.000
"""

KOPF_S5 = """\
Format,RaceBox CSV
Date UTC,2026-06-15T09:00:00+00:00
Session Index,1
Session Type,Track
Track,Bergring Nord
Configuration,
Laps,2
Best Lap Time,59.000
Lap 1, 59.000, sectors, 15.000,15.000,15.000,14.000
Lap 2, 61.000, sectors, 16.000,15.500,15.500,14.000
"""

KOPF_S6 = """\
Format,RaceBox CSV
Date UTC,2026-08-11T09:00:00+00:00
Session Index,1
Session Type,Track
Track,Uebungsplatz Nord
Configuration,
Laps,1
Best Lap Time,37.013
Lap 1, 37.013, sectors, 12.326,9.467,7.406,0,7.814
"""

KOPF_S7 = """\
Format,RaceBox CSV
Date UTC,2026-08-12T09:00:00+00:00
Session Index,1
Session Type,Track
Track,Kartbahn Sued
Configuration,
Laps,1
Best Lap Time,40.000
Lap 1, 40.000, sectors, 10.000,10.000,10.000,0,10.000
"""

def json_aus_kopf(sid, kopf, angaben, fahrzeug, linienereignisse,
                  beginnt_bei_null=False, sektoren_als_zahlen=False):
    """Aus einem CSV-Kopf die JSON-Antwort bauen, die racebox.pro liefert.

    Beide Wege zeigen dieselbe Session -- der Test haelt sie deshalb aus
    einer Quelle zusammen, statt zwei Fassungen von Hand zu pflegen. Der
    Unterschied liegt in der Form: Das JSON kennt die Zahl der Splitlinien
    und `lapEvents`, das CSV fuellt stattdessen Felder mit Nullen auf.
    """
    k = S.kopf_lesen(kopf)
    turn, datum, zeit = angaben
    tag, monat, jahr = datum.split('/')
    ortszeit = calendar.timegm(time.strptime(
        '%s-%s-%s %s' % (jahr, monat, tag, zeit), '%Y-%m-%d %H:%M'))

    # Aus den CSV-Feldern zurueck auf Splitpunkte: Das letzte belegte Feld
    # ist das Schlussstueck, die davor sind die Zwischensektoren.
    felder = max(len(r['sektoren']) for r in k['runden'])
    belegt = sorted({i for r in k['runden']
                     for i, w in enumerate(r['sektoren']) if w > 0})
    splits = max(len(belegt) - 1, 0)
    # 25 Hz, und die erste Runde beginnt normalerweise an einer Linie --
    # also nicht bei Record 0, sondern nach einer Ausfahrt aus der Box.
    rate = 25.0
    stand = 0.0 if beginnt_bei_null else 500.0
    runden = []
    for r in k['runden']:
        sektoren = []
        for nr, i in enumerate(belegt[:-1]):
            if r['sektoren'][i] > 0:
                sektoren.append(r['sektoren'][i] if sektoren_als_zahlen
                                else {'time': r['sektoren'][i],
                                      'sectorIndex': nr})
        stand += r['zeit'] * rate
        runden.append({'time': r['zeit'], 'index': r['nr'] - 1,
                       'sectors': sektoren,
                       'sensorRecordIndex': int(round(stand))})
    marke, modell = (fahrzeug or ' ').split(' ', 1)
    return {'session': {'meta': {
        'sessionType': 1,
        'indexInTheDay': turn,
        'dateTimeStartedLocal': ortszeit,
        'dateTimeStartedUTC': ortszeit - 7200,
        'deviceSerialNumber': 1000000001,
        'bestLapTime': float(k.get('Best Lap Time', 0) or 0),
        'duration': round(stand / rate, 1),
        'records': int(round(stand)),
        'maxSpeed': 180.5, 'minSpeed': 44.8, 'maxG': 1.2,
        'lapEvents': linienereignisse,
        'laps': runden,
        'track': {'name': k.get('Track', ''), 'configuration': {
            'id': 'k' + k.get('Track', '')[:8].replace(' ', ''),
            'name': k.get('Configuration', ''),
            'splitLines': [[0, 0, 0, 0]] * splits}},
        'vehicle': ({'id': 'v' * 24, 'make': marke, 'model': modell}
                    if fahrzeug else {}),
    }, 'data': {
        'dataColumns': ['iTOW', 'Latitude', 'Longitude', 'Altitude', 'Speed',
                        'Heading', 'GForceX', 'GForceY', 'GForceZ',
                        'LeanAngle'],
        # Vier Punkte im Sekundentakt: 36, 3, 72 und 108 km/h. Der zweite
        # liegt unter der Fahrschwelle -- dieses Stueck ist Rollen, keine
        # Fahrt, und darf weder in die Kilometer noch in den Schnitt.
        'data': [[1000, 51.0, 13.0, 100.0, 36.0, 0, 0, 0, 1, 0.0],
                 [2000, 51.0, 13.0, 100.0, 3.0, 0, 0, 0, 1, 10.0],
                 [3000, 51.0, 13.0, 100.0, 72.0, 0, 0, 0, 1, -40.0],
                 [4000, 51.0, 13.0, 100.0, 108.0, 0, 0, 0, 1, 30.0]],
    }}}


# id -> (Kopfblock, Sessionseite-Angaben, Fahrzeug)
# id -> (Kopfblock, (Turn, Tag, Ortszeit), Fahrzeug, Linienueberfahrten).
# Die letzte Zahl entscheidet, ob die letzte Runde eine Ausfahrrunde ist:
# Liegt eine Runde mehr vor als Ueberfahrten, endete sie nicht am Ziel.
TESTSESSIONS = {
    'a' * 24: (KOPF_S1, (1, '10/08/2026', '19:16'), 'v1', 3),
    'b' * 24: (KOPF_S2, (2, '10/08/2026', '20:05'), 'v1', 2),
    'c' * 24: (KOPF_S3, (1, '01/07/2026', '10:00'), 'v2', 1),
    'd' * 24: (KOPF_S4, (1, '01/06/2026', '11:00'), 'v1', 1),
    'e' * 24: (KOPF_S5, (1, '15/06/2026', '11:00'), 'v1', 2),
    'f' * 24: (KOPF_S6, (1, '11/08/2026', '12:00'), 'v1', 1),
    '0' * 24: (KOPF_S7, (1, '12/08/2026', '12:00'), None, 1),
}

FAHRZEUGE = {'v1' * 12: 'Yamaha MT-07', 'v2' * 12: 'Suzuki SV650'}
FZG_KURZ = {'v1': 'v1' * 12, 'v2': 'v2' * 12}


def sessions_bauen():
    """Die Testsessions als Cache-Eintraege, ohne Netz."""
    raus = []
    for sid, (kopf, (turn, datum, zeit), fzg, _) in sorted(TESTSESSIONS.items()):
        tag, monat, jahr = datum.split('/')
        seite = {'turn': turn, 'datum': '%s-%s-%s' % (jahr, monat, tag),
                 'startzeit': zeit}
        name = FAHRZEUGE.get(FZG_KURZ.get(fzg, ''), None)
        raus.append(S.session_bauen(sid, kopf, seite, name))
    return raus


# --- Teil 1: die Rechnung -------------------------------------------------

def test_json_umsetzer():
    """Der JSON-Weg muss dieselbe Session ergeben wie der CSV-Weg."""
    d = json_aus_kopf('a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'),
                      'Yamaha MT-07', 3)
    e = S.session_aus_json('a' * 24, d)
    gleich(e['version'], S.CACHE_VERSION,
           'Eintraege des JSON-Wegs tragen die aktuelle Fassung')
    gleich(e['strecke'], 'Talkurs', 'Strecke')
    gleich(e['splits'], 3, 'drei Splitlinien, also vier Sektoren')
    gleich(e['turn'], 1, 'Turn aus indexInTheDay')
    gleich(e['datum'], '2026-08-10', 'Tag in Ortszeit')
    gleich(e['startzeit'], '19:16', 'Startzeit in Ortszeit')
    gleich(e['fahrzeug'], 'Yamaha MT-07',
           'das Fahrzeug steht im JSON und braucht keine zweite Anfrage')
    pruefe(bool(e['konfig_id']), 'die Konfiguration hat eine Kennung')
    gleich(len(e['runden']), 3, 'drei Runden')

    # Der Schlusssektor wird nicht mitgeliefert -- er ist die Rundenzeit
    # minus der Summe der uebrigen.
    gleich(e['runden'][1]['sektoren'], [30.0, 25.0, 20.0, 15.0],
           'Schlusssektor aus Rundenzeit minus Zwischensektoren')

    # Liefert eine Antwort das Schlussstueck schon mit, darf es nicht durch
    # die Differenz -- also durch null -- ersetzt werden.
    schon_dabei = {'session': {'meta': {
        'laps': [{'time': 90.0, 'sectors': [
            {'time': 30.0, 'sectorIndex': 0}, {'time': 25.0, 'sectorIndex': 1},
            {'time': 20.0, 'sectorIndex': 2}, {'time': 15.0, 'sectorIndex': 3},
        ]}],
        'track': {'name': 'Talkurs', 'configuration': {
            'id': 'k1', 'splitLines': [[0]] * 3}}}}}
    voll = S.session_aus_json('a' * 24, schon_dabei)
    gleich(voll['runden'][0]['sektoren'], [30.0, 25.0, 20.0, 15.0],
           'ein mitgeliefertes Schlussstueck bleibt stehen')
    gleich(voll['runden'][0]['stimmig'], True, 'und die Runde stimmt')

    # Haerter: Das Schlussstueck ist dabei, aber ein Split dazwischen fehlt.
    # Dann ist die Differenz gross -- und gehoert trotzdem nicht ans Ende.
    mit_luecke = {'session': {'meta': {
        'laps': [{'time': 90.0, 'sectors': [
            {'time': 30.0, 'sectorIndex': 0}, {'time': 15.0, 'sectorIndex': 3},
        ]}],
        'track': {'name': 'Talkurs', 'configuration': {
            'id': 'k1', 'splitLines': [[0]] * 3}}}}}
    luecke = S.session_aus_json('a' * 24, mit_luecke)
    gleich(luecke['runden'][0]['sektoren'], [30.0, 0.0, 0.0, 15.0],
           'ein mitgeliefertes Schlussstueck wird nicht von der Differenz '
           'ueberschrieben, auch wenn dazwischen etwas fehlt')
    gleich(luecke['runden'][0]['stimmig'], False,
           'und die Runde gilt als unstimmig -- 45 Sekunden fehlen')
    gleich(e['runden'][0]['sektoren'], [0.0, 0.0, 0.0, 16.0],
           'eine Runde ab einem Splitpunkt bringt nur den Schlusssektor')
    # Anders als im CSV ohne Luecke: Das JSON kennt die Zahl der Splits.
    gleich(len(e['runden'][0]['sektoren']), 4,
           'vier Sektoren, keine aufgefuellte Null wie im CSV')

    # Und die Rechnung darauf muss dieselbe Zahl ergeben wie ueber CSV.
    ueber_json, ueber_csv = [], []
    for sid, (kopf, angaben, fzg, ereignisse) in sorted(TESTSESSIONS.items()):
        name = FAHRZEUGE.get(FZG_KURZ.get(fzg or '', ''), None)
        ueber_json.append(S.session_aus_json(sid, json_aus_kopf(
            sid, kopf, angaben, name or '', ereignisse)))
        tag, monat, jahr = angaben[1].split('/')
        ueber_csv.append(S.session_bauen(sid, kopf, {
            'turn': angaben[0], 'datum': '%s-%s-%s' % (jahr, monat, tag),
            'startzeit': angaben[2]}, name))
    for weg, sessions in (('json', ueber_json), ('csv', ueber_csv)):
        strecken, _, _ = S.strecken_bauen(sessions, [])
        talkurs = [e for e in strecken if e['strecke'] == 'Talkurs'][0]
        z = [f for f in talkurs['fahrzeuge']
             if f['fahrzeug'] == 'Yamaha MT-07'][0]
        nahe(z['best']['zeit'], 88.3, 'Bestrunde ueber %s' % weg)
        nahe(z['theo'], 86.0, 'theoretische Runde ueber %s' % weg)
        nahe(z['theo_streng'], 87.5, 'strenge theoretische Runde ueber %s' % weg)


def test_randsektoren():
    """Sektoren an den Raendern, die keine Messung zwischen zwei Linien sind.

    Beide Faelle sind an echten Daten belegt: Eine Session, deren erste
    Runde bei Record 0 beginnt, und eine, deren letzte Runde nicht am Ziel
    endet.
    """
    normal = S.session_aus_json('a' * 24, json_aus_kopf(
        'a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'), 'Yamaha MT-07', 3))
    gleich(normal['runden'][0]['sektoren'][-1], 16.0,
           'beginnt die erste Runde an einer Linie, ist ihr Sektor eine '
           'Messung und bleibt')
    gleich(normal['runden'][-1]['sektoren'][-1], 14.5,
           'endet die letzte Runde am Ziel, bleibt ihr Schlusssektor')

    rand = S.session_aus_json('a' * 24, json_aus_kopf(
        'a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'), 'Yamaha MT-07', 2,
        beginnt_bei_null=True))
    gleich(rand['runden'][0]['sektoren'], [0.0, 0.0, 0.0, 0.0],
           'beginnt sie bei Record 0, lief ihr erstes Stueck ab dem '
           'Aufnahmestart und zaehlt nicht')
    gleich(rand['runden'][-1]['sektoren'][-1], 0.0,
           'liegt eine Runde mehr vor als Ueberfahrten, endete die letzte '
           'nicht am Ziel')
    gleich(rand['runden'][-1]['sektoren'][:3], [29.0, 26.0, 20.0],
           'ihre uebrigen Sektoren sind trotzdem gemessen worden')


def test_ungewohnte_formen():
    """Was das Werkzeug nicht kennt, darf es nicht umwerfen."""
    als_zahlen = S.session_aus_json('a' * 24, json_aus_kopf(
        'a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'), 'Yamaha MT-07', 3,
        sektoren_als_zahlen=True))
    # Am echten Konto stand eine Runde mit den Sektoren 1.00 und 2.00 im
    # JSON, waehrend der Export 24.89 und 17.02 nannte: Das waren die
    # Indizes. Blanke Zahlen weisen sich nicht als Zeiten aus.
    gleich([r['sektoren'][0] for r in als_zahlen['runden']], [0.0, 0.0, 0.0],
           'blanke Zahlen werden nicht als Sektorzeiten gelesen')
    pruefe(all(r['zeit'] > 0 for r in als_zahlen['runden']),
           'die Rundenzeiten bleiben davon unberuehrt')

    kaputt = {'session': {'meta': {'laps': [
        {'time': 90.0, 'sectors': [{'time': 30.0, 'sectorIndex': 0}]},
        'das ist keine Runde',
        {'sectors': []},
    ], 'track': {'name': 'Talkurs', 'configuration': {
        'id': 'k1', 'splitLines': [[0, 0, 0, 0]]}}}}}
    e = S.session_aus_json('a' * 24, kaputt)
    gleich(len(e['runden']), 1,
           'was keine Runde ist, wird uebergangen statt mitgezaehlt')
    gleich(e['runden'][0]['sektoren'], [30.0, 60.0],
           'die brauchbare Runde wird trotzdem richtig gerechnet -- und '
           'ohne Angabe zu Dauer und Recordzahl wird kein Randsektor '
           'verworfen, weil sich nicht feststellen laesst, ob es einer ist')

    fehler = None
    try:
        S.session_aus_json('a' * 24, {'session': {}})
    except S.NichtVerfuegbar as e:
        fehler = str(e)
    pruefe(fehler is not None,
           'eine Antwort ohne Kopfdaten faellt auf den CSV-Weg zurueck, '
           'statt eine leere Session zu erfinden')


def test_unglaubwuerdige_golden_lap():
    """Eine Golden Lap weit unter der Bestrunde ist ein Fehler, kein Fund."""
    sessions = sessions_bauen()
    strecken, _, _ = S.strecken_bauen(sessions, MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    pruefe(not z['unglaubwuerdig'],
           'acht Prozent unter der Bestrunde sind plausibel')

    # Eine Runde mit einem unmoeglich kurzen Sektor unterschieben.
    verdorben = [dict(s) for s in sessions]
    for s in verdorben:
        if s['strecke'] == 'Talkurs' and s['fahrzeug'] == 'Yamaha MT-07':
            # Zwei Runden, die je einen Sektor knapp ueber der
            # Aussortierschwelle haben und sonst hoffnungslos langsam
            # sind. Jede fuer sich ist unauffaellig -- zusammengesetzt
            # ergeben sie eine Golden Lap, die niemand faehrt.
            s['runden'] = list(s['runden']) + [
                {'nr': 98, 'zeit': 202.5, 'stimmig': True,
                 'sektoren': [22.5, 60.0, 60.0, 0, 60.0]},
                {'nr': 99, 'zeit': 106.5, 'stimmig': True,
                 'sektoren': [60.0, 19.5, 15.5, 0, 11.5]}]
            break
    strecken, _, _ = S.strecken_bauen(verdorben, MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    pruefe(z['unglaubwuerdig'],
           'eine Golden Lap weit unter der Bestrunde wird als fraglich '
           'erkannt')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_uebersicht(strecken, {})
    pruefe('(!)' in puffer.getvalue()
           and 'fuenfzehn Prozent' in puffer.getvalue(),
           'und in der Uebersicht benannt statt stillschweigend angezeigt')

    # Und eine Runde, deren Sektoren nicht ihre Rundenzeit ergeben, liefert
    # gar keine Sektorbestzeit.
    unstimmig = [dict(s) for s in sessions]
    for s in unstimmig:
        if s['strecke'] == 'Talkurs' and s['fahrzeug'] == 'Yamaha MT-07':
            # Plausible Einzelwerte, aber sie ergeben zusammen nicht die
            # Rundenzeit -- sonst greift schon der Plausibilitaetsfilter,
            # und der Test prueft nicht mehr, was er soll.
            s['runden'] = list(s['runden']) + [{
                'nr': 98, 'zeit': 90.0, 'stimmig': False,
                'sektoren': [26.0, 24.0, 19.0, 0, 14.0]}]
            break
    strecken, _, _ = S.strecken_bauen(unstimmig, MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    nahe(z['theo'], 86.0,
         'eine unstimmige Runde geht nicht in die Golden Lap ein')


def test_gerechneter_schlusssektor():
    """Aus Teilrunden zaehlt nur, was gemessen wurde.

    RaceBox liefert das Schlussstueck ins Ziel nicht mit -- es ist die
    Rundenzeit minus der Summe der uebrigen. Bei einer vollstaendigen
    Runde geht die Rechnung auf, bei einer Teilrunde nicht: Dort traegt
    diese eine Zahl die ganze Unsicherheit. Am echten Konto stand so eine
    Sektorbestzeit von 10.89 gegen sonst 15.93 an derselben Stelle -- und
    verkuerzte die Golden Lap um sechs Sekunden.
    """
    daten = {'session': {'meta': {
        'sessionType': 1, 'indexInTheDay': 1,
        'dateTimeStartedLocal': 0, 'dateTimeStartedUTC': 0,
        # 25 Hz, und die erste Runde beginnt bei Record 750 -- also an
        # einer Linie und nicht beim Aufnahmestart.
        'duration': 200.0, 'records': 5000, 'lapEvents': 2,
        'vehicle': {'make': 'Yamaha', 'model': 'MT-07'},
        'track': {'name': 'Talkurs', 'configuration': {
            'id': 'k1', 'splitLines': [[0]] * 3}},
        'laps': [
            # Eine vollstaendige Runde -- der Schlusssektor ist gerechnet,
            # aber die Summe schliesst.
            {'time': 90.0, 'sensorRecordIndex': 3000, 'sectors': [
                {'time': 30.0, 'sectorIndex': 0},
                {'time': 25.0, 'sectorIndex': 1},
                {'time': 20.0, 'sectorIndex': 2}]},
            # Eine Teilrunde: Sektor 3 fehlt, und der gerechnete Rest
            # waere mit 0.5 Sekunden ein Sektorrekord, den es nicht gibt.
            {'time': 52.5, 'sensorRecordIndex': 4312, 'sectors': [
                {'time': 28.0, 'sectorIndex': 0},
                {'time': 24.0, 'sectorIndex': 1}]},
        ]}}}
    e = S.session_aus_json('a' * 24, daten)
    gleich(e['runden'][0]['abgeleitet'], 4,
           'der Schlusssektor ist die gerechnete Zahl')
    gleich(e['runden'][1]['sektoren'], [28.0, 24.0, 0.0, 0.5],
           'auch in der Teilrunde steht er zunaechst drin')

    alle = S.runden([e], (1, 2, 3, 4))
    gleich(alle[0]['sektoren'], {1: 30.0, 2: 25.0, 3: 20.0, 4: 15.0},
           'aus der vollstaendigen Runde zaehlt er mit')
    gleich(alle[1]['sektoren'], {1: 28.0, 2: 24.0},
           'aus der Teilrunde nicht -- die gemessenen Sektoren aber schon')
    theo, _ = S.theoretische_runde(alle, (1, 2, 3, 4))
    nahe(theo, 87.0, 'die Golden Lap bleibt damit 28 + 24 + 20 + 15')


def test_stimmig_wird_nicht_nachgerechnet():
    """Aus einem bereinigten Eintrag laesst sich `stimmig` nicht herleiten.

    Am echten Konto wurden dadurch zwei Runden je Turn als unstimmig
    gemeldet -- die Ein- und die Ausfahrrunde, deren Randsektor beim Holen
    absichtlich verworfen worden war. Die Summe geht danach nicht mehr auf,
    und das ist kein Mangel der Daten, sondern Absicht.
    """
    session = {'id': 'a' * 24, 'datum': '2026-08-10', 'turn': 1,
               'startzeit': '19:16', 'quelle': 'json',
               'runden': [{'nr': 1, 'zeit': 90.0,
                           'sektoren': [0.0, 25.0, 20.0, 15.0]}]}
    r = S.runden([session], (1, 2, 3, 4))
    gleich(r[0]['stimmig'], True,
           'ohne Angabe gilt die Runde als stimmig, statt aus dem schon '
           'bereinigten Feld erschlossen zu werden')

    session['runden'][0]['stimmig'] = False
    gleich(S.runden([session], (1, 2, 3, 4))[0]['stimmig'], False,
           'die Angabe aus dem Eintrag gilt aber')
    pruefe(S.CACHE_VERSION >= 3,
           'Eintraege ohne diese Angabe werden neu geholt')


def test_verpasste_splits():
    """Die Musteransicht zeigt, wo die unstimmigen Runden herkommen."""
    sessions = [dict(s) for s in sessions_bauen()]
    for s in sessions:
        if s['strecke'] == 'Talkurs' and s['fahrzeug'] == 'Yamaha MT-07':
            # Sektor 2 fehlt, und zehn Sekunden bleiben unerklaert. Der
            # Schlusssektor ist gerechnet und faellt deshalb aus der
            # Wertung -- die Luecke muss trotzdem die urspruengliche sein.
            s['runden'] = list(s['runden']) + [{
                'nr': 9, 'zeit': 95.0, 'stimmig': False, 'abgeleitet': 5,
                'sektoren': [30.0, 0.0, 20.0, 0, 35.0]}]
            break
    strecken, _, _ = S.strecken_bauen(sessions, MUSTER)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_verpasste_splits(strecken)
    text = puffer.getvalue()
    pruefe('unstimmig' in text, 'die Ansicht nennt die unstimmigen Runden')
    pruefe('Sektor 2' in text, 'und welcher Sektor fehlt')
    pruefe('2026-08-10' in text, 'und ueber welche Fahrtage sie sich verteilen')
    pruefe('10.00' in text,
           'die fehlende Zeit wird auf den urspruenglichen Werten gerechnet, '
           'nicht auf den aussortierten')


def test_csv_als_rundenquelle():
    """Die Runden kommen aus dem Export, nicht aus dem JSON.

    Das CSV fuehrt nur die abgeschlossenen Runden -- die Exportmaske sagt
    es woertlich -- und liefert jede Sektorzeit einschliesslich des
    Schlussstuecks. Damit entfaellt beides: das Rechnen des letzten
    Sektors und das Verwerfen der Randsektoren.
    """
    # Drei Splitlinien: Die CSV-Felder 1, 2, 3 und 5 sind belegt, Feld 4
    # bleibt leer. Gerechnet wird mit vier dicht gezaehlten Sektoren.
    csv_runden = S.kopf_lesen(
        'Track,Talkurs\n'
        'Lap 1, 68.650, sectors, 4.592,34.668,14.644,0,14.746\n'
        'Lap 2, 49.942, sectors, 4.383,16.711,14.137,0,14.711\n')['runden']
    umgerechnet = S.csv_runden_umrechnen(csv_runden, 3)
    gleich(umgerechnet[0]['sektoren'], [4.592, 34.668, 14.644, 14.746],
           'das Schlussstueck steht im CSV am Ende und gehoert an Position 4')
    gleich(umgerechnet[0]['abgeleitet'], None,
           'im CSV ist keine Zahl gerechnet')
    gleich(umgerechnet[0]['stimmig'], True, 'und die Summe geht auf')

    # Eine Strecke ohne Splitlinien: nur das Schlussstueck.
    ohne = S.csv_runden_umrechnen(
        [{'nr': 1, 'zeit': 50.0, 'sektoren': [0, 0, 0, 0, 50.0]}], 0)
    gleich(ohne[0]['sektoren'], [50.0], 'ohne Splits bleibt eine Zahl uebrig')

    daten = json_aus_kopf('a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'),
                          'Yamaha MT-07', 3)
    mit = S.session_aus_json('a' * 24, daten, csv_runden=[
        {'nr': 1, 'zeit': 90.0, 'sektoren': [30.0, 25.0, 20.0, 0, 15.0]}])
    gleich(mit['quelle'], 'json+csv', 'der Eintrag sagt, woher die Runden sind')
    gleich(len(mit['runden']), 1, 'und es sind die des Exports')
    ohne_csv = S.session_aus_json('a' * 24, daten)
    gleich(ohne_csv['quelle'], 'json', 'ohne Export bleibt es beim JSON')
    gleich(len(ohne_csv['runden']), 3, 'mit dessen Runden')
    leer = S.session_aus_json('a' * 24, daten, csv_runden=[])
    gleich(len(leer['runden']), 3,
           'ein Export ohne Rundenzeilen faellt auf das JSON zurueck')


def test_unmoegliche_sektorzeiten():
    """Was keine Fahrleistung mehr sein kann, faellt raus.

    Am echten Konto stand 0.27 Sekunden fuer einen Sektor, in dem sonst
    15 Sekunden stehen -- bei 100 km/h sieben Meter. Dahinter steckt eine
    verpasste Splitueberfahrt: Sie verschiebt Zeit in den Nachbarsektor,
    die Rundenzeit bleibt richtig, die Aufteilung nicht. Dieselbe
    Fehlmessung steht im CSV wie im JSON.
    """
    sessions = [dict(s) for s in sessions_bauen()]
    for s in sessions:
        if s['strecke'] == 'Talkurs' and s['fahrzeug'] == 'Yamaha MT-07':
            s['runden'] = list(s['runden']) + [{
                'nr': 99, 'zeit': 90.0, 'stimmig': True, 'abgeleitet': None,
                'sektoren': [0.27, 25.0, 20.0, 0, 44.73]}]
            break
    strecken, _, _ = S.strecken_bauen(sessions, MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    gleich(z['aussortiert'], 1, 'die unmoegliche Zeit wird aussortiert')
    nahe(z['theo'], 86.0, 'und die Golden Lap bleibt, was sie war')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_uebersicht(strecken, {})
    pruefe('aussortiert' in puffer.getvalue()
           and 'Splitueberfahrt' in puffer.getvalue(),
           'gesagt wird es samt Grund, nicht stillschweigend getan')

    # Die Schwelle darf keine Fahrleistung treffen: Die beste Sektorzeit
    # liegt bei neunzig bis fuenfundneunzig Prozent des Medians.
    pruefe(S.SEKTOR_SCHWELLE <= 0.5,
           'die Schwelle liegt weit unterhalb jeder Fahrleistung')
    knapp = [dict(s) for s in sessions_bauen()]
    for s in knapp:
        if s['strecke'] == 'Talkurs' and s['fahrzeug'] == 'Yamaha MT-07':
            # Zwei Sekunden schneller als je gefahren -- und damit noch
            # lange keine Fehlmessung.
            s['runden'] = list(s['runden']) + [{
                'nr': 98, 'zeit': 88.0, 'stimmig': True, 'abgeleitet': None,
                'sektoren': [26.5, 25.0, 20.0, 0, 16.5]}]
            break
    strecken, _, _ = S.strecken_bauen(knapp, MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    gleich(z['aussortiert'], 0, 'eine schnelle Runde bleibt unangetastet')
    nahe(z['theo'], 84.5, 'und geht in die Golden Lap ein')


def test_median_und_streuung():
    """Der Median haelt gegen Ausreisser, die Streuung misst die Konstanz."""
    gleich(S.mittelwert([1, 2, 3, 4, 100]), 3, 'Median bei ungerader Zahl')
    # Bewusst schief: Der Mittelwert waere 26.5 und damit vom Ausreisser
    # gezogen -- genau das soll der Median nicht sein.
    gleich(S.mittelwert([1, 2, 3, 100]), 2.5, 'Median bei gerader Zahl')
    gleich(S.mittelwert([]), None, 'ohne Werte kein Median')

    # Die Streuung ist der Mittelwert aus zweit- und drittbester Runde,
    # gemessen an der besten: (1:29.50 + 1:30.00) / 2 - 1:28.30.
    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    nahe(z['streuung'], 1.45, 'Streuung aus zweit- und drittbester Runde')
    einzeln = [f for f in strecken[0]['fahrzeuge']
               if f['fahrzeug'] == 'Suzuki SV650'][0]
    gleich(einzeln['streuung'], None,
           'mit einer einzigen Runde gibt es keine Streuung')


def test_doppelte_sessions():
    """Dieselbe Aufzeichnung unter zwei Kennungen zaehlt einmal.

    Am echten Konto gibt es das: gleicher Tag, gleiche Startzeit, gleicher
    Turn, gleiches Fahrzeug, zwei Kennungen. Dann steht dieselbe Runde
    zweimal in der Rangliste -- und die zweitbeste Runde ist in Wahrheit
    die beste noch einmal.
    """
    sessions = sessions_bauen()
    # Genommen wird die Session mit der Bestrunde -- an ihr faellt eine
    # Doppelung auf, weil dieselbe Zeit sonst zweimal in der Rangliste
    # steht.
    urbild = [s for s in sessions if s['id'] == 'b' * 24][0]
    zwilling = dict(urbild)
    zwilling['id'] = 'z' * 24
    # Und einer mit weniger Runden -- ein Teilupload derselben Fahrt.
    kurz = dict(urbild)
    kurz['id'] = 'y' * 24
    kurz['runden'] = urbild['runden'][:1]
    bereinigt, paare = S.doppelte_entfernen(sessions + [zwilling, kurz])
    gleich(len(paare), 2, 'beide Zwillinge werden erkannt')
    gleich(len(bereinigt), len(sessions), 'und faellt raus')
    behalten = [x for x in bereinigt if x['datum'] == urbild['datum']
                and x['turn'] == urbild['turn']][0]
    gleich(len(behalten['runden']), len(urbild['runden']),
           'behalten wird die Fassung mit den meisten Runden')
    # Gemerkt wird beides -- ohne die ausgeschiedene Fassung liesse sich
    # nicht mehr nachsehen, ob sie ueberhaupt dasselbe enthaelt.
    kurzes_paar = [p for p in paare if p[1]['id'] == 'y' * 24][0]
    gleich(kurzes_paar[0]['id'], 'b' * 24,
           'im Paar steht die gezaehlte Fassung vorn')

    strecken, _, doppelte = S.strecken_bauen(sessions + [zwilling], MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    zeiten = [r['zeit'] for r in z['top_runden']]
    gleich(len(zeiten), len(set(zeiten)),
           'keine Runde steht zweimal in der Rangliste')
    gleich(len(doppelte), 1, 'strecken_bauen reicht die Paare heraus')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_uebersicht(strecken, {}, doppelte=doppelte)
    pruefe('doppelt' in puffer.getvalue(),
           'dass Sessions doppelt vorlagen, wird gesagt')


def test_doppelte_exporte_vergleichen():
    """Fuehrt die kuerzere Fassung dieselben Zahlen wie die gezaehlte?

    Dass sie weniger Runden hat, macht sie zum Teilupload. Haette sie
    aber *andere* Sektorzeiten, waere sie eine zweite Messung -- und sie
    einfach wegzulassen waere dann ein Datenverlust, kein Aufraeumen.
    Deshalb werden die abgelegten Originalexporte selbst verglichen.
    """
    lang = dict(ID='l' * 24, runden=[1, 2, 3])
    kurz = dict(ID='k' * 24, runden=[1])
    ordner = tempfile.mkdtemp()
    try:
        def ablegen(sid, text):
            with open(os.path.join(ordner, '%s_bikemode.csv' % sid),
                      'w', encoding='utf-8') as f:
                f.write(text)

        # Der Teilupload faengt spaeter an und zaehlt wieder bei eins --
        # dieselbe Runde traegt also eine andere Nummer.
        teil = """\
Format,RaceBox CSV
Track,Talkurs
Lap 1, 90.000, sectors, 30.000,25.000,20.000,0,15.000
Lap 2, 89.500, sectors, 29.000,26.000,20.000,0,14.500
"""
        ablegen(lang['ID'], KOPF_S1)
        ablegen(kurz['ID'], teil)
        paare = [({'id': lang['ID'], 'strecke': 'Talkurs', 'konfiguration': '',
                   'fahrzeug': 'Yamaha MT-07', 'datum': '2026-08-10',
                   'startzeit': '19:16', 'runden': [0, 0, 0]},
                  {'id': kurz['ID'], 'runden': [0, 0]})]
        b = S.doppelte_vergleichen(paare, ordner)[0]
        gleich(len(b['fehlend']), 0, 'beide Exporte liegen vor')
        gleich(b['gleich'], 2, 'zwei Runden stehen in beiden, Sektor fuer '
                               'Sektor gleich')
        gleich(len(b['abweichend']), 0, 'und keine weicht ab')
        gleich(len(b['nur_lang']), 1,
               'die Einfahrrunde steht nur in der gezaehlten Fassung')
        gleich(len(b['nur_kurz']), 0,
               'der Teilupload bringt keine eigene Runde mit')
        gleich(len(b['verschoben']), 2,
               'die Nummern sind verschoben -- ueber sie zuzuordnen ginge '
               'schief')
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.zeige_doppelte([b])
        text = puffer.getvalue()
        pruefe('Teilupload' in text, 'das Ergebnis wird benannt')

        # Jetzt eine echte Abweichung: dieselbe Rundenzeit, andere Sektoren.
        anders = teil.replace('30.000,25.000', '31.000,24.000')
        ablegen(kurz['ID'], anders)
        b = S.doppelte_vergleichen(paare, ordner)[0]
        gleich(len(b['abweichend']), 1,
               'eine andere Aufteilung derselben Rundenzeit faellt auf')
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.zeige_doppelte([b])
        pruefe('nicht deckungsgleich' in puffer.getvalue(),
               'und wird als solche gemeldet')

        # Und fehlt ein Export, wird das gesagt statt geraten.
        os.unlink(os.path.join(ordner, '%s_bikemode.csv' % kurz['ID']))
        b = S.doppelte_vergleichen(paare, ordner)[0]
        gleich(len(b['fehlend']), 1, 'ein fehlender Export wird gemeldet')
        gleich(b['gleich'], 0, 'und nichts verglichen')
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.zeige_doppelte([b])
        pruefe('fehlt' in puffer.getvalue(),
               'die Ausgabe sagt, dass der Export fehlt')
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.zeige_doppelte([])
        pruefe('Keine' in puffer.getvalue(),
               'ohne Doppelte sagt die Ausgabe genau das')
    finally:
        shutil.rmtree(ordner)


def test_turn_theo_nutzt_dieselben_runden():
    """Je Turn und je Fahrtag muessen dieselben Runden sehen wie das Ganze.

    Am echten Konto stand eine Turn-Theo von 24.85 gegen eine Bestrunde
    von 1:03 -- weil die Rechnung je Turn ihre Runden neu aufbaute und
    damit an der Pruefung vorbeilief, die fuer die Golden Lap schon
    gelaufen war.
    """
    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    z = [f for f in strecken[0]['fahrzeuge']
         if f['fahrzeug'] == 'Yamaha MT-07'][0]
    aus_turns = [t['runden'] for t in z['je_turn']]
    gleich(sum(aus_turns), z['anzahl_runden'],
           'die Turns zusammen ergeben genau die Runden des Ganzen')
    gleich(sum(t['runden'] for t in z['je_tag']), z['anzahl_runden'],
           'die Fahrtage ebenso')
    for t in z['je_turn']:
        pruefe(t['theo'] is None or t['theo'] >= z['theo'] - 0.0005,
               'kein Turn ist schneller als die Golden Lap ueber alles')
    for t in z['je_tag']:
        pruefe(t['theo'] is None or t['theo'] >= z['theo'] - 0.0005,
               'und kein Fahrtag')


def test_fahrzeugfilter():
    """Wer sein Konto teilt, will nicht acht Fahrzeuge in der Uebersicht."""
    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    alle = sum(len(e['fahrzeuge']) for e in strecken)
    pruefe(alle > 1, 'ohne Filter sind mehrere Fahrzeuge da')
    versteckt = S.fahrzeuge_filtern(strecken, ['Yamaha*'])
    gleich(versteckt, 1, 'ein Fahrzeug wird versteckt')
    gleich([z['fahrzeug'] for e in strecken for z in e['fahrzeuge']],
           ['Yamaha MT-07', 'Yamaha MT-07'], 'und eines bleibt')

    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    gleich(S.fahrzeuge_filtern(strecken, []), 0,
           'ohne Muster wird nichts versteckt')

    ordner = tempfile.mkdtemp()
    try:
        gleich(S.fahrzeugfilter_lesen(ordner), [],
               'ohne Datei werden alle gezeigt')
        pfad = os.path.join(ordner, 'fahrzeuge')
        pruefe(os.path.exists(pfad), 'die Datei legt sich selbst an')
        pruefe('--alle' in open(pfad, encoding='utf-8').read(),
               'und erklaert sich darin')
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write('# Kommentar\nYamaha*\n')
        gleich(S.fahrzeugfilter_lesen(ordner), ['Yamaha*'], 'sonst gilt sie')
    finally:
        shutil.rmtree(ordner)


def test_startabfrage():
    """Der zweite Lauf soll nicht ungefragt wieder anmelden."""
    ordner = tempfile.mkdtemp()
    echtes_input, echtes_stdin = builtins.input, sys.stdin

    class MitTerminal:
        def isatty(self):
            return True

    try:
        # Ohne Cache gibt es nichts zu fragen.
        pruefe(still(S.soll_holen, ordner), 'ohne Cache wird geholt')

        S.cache_schreiben(sessions_bauen()[0], ordner)
        sys.stdin = MitTerminal()
        for eingabe, erwartet, was in (('', True, 'Enter heisst holen'),
                                       ('j', True, 'j heisst holen'),
                                       ('n', False, 'n heisst nur Cache'),
                                       ('N', False, 'auch gross')):
            builtins.input = lambda *args, e=eingabe: e
            gleich(still(S.soll_holen, ordner), erwartet, was)

        # `q` und Strg+C heissen hier ausdruecklich nicht dasselbe: Die
        # Notbremse sagt "jetzt nicht holen", die Tuer sagt "Programm aus".
        for eingabe in ('q', 'ende', 'Q', ' ende '):
            builtins.input = lambda *args, e=eingabe: e
            try:
                still(S.soll_holen, ordner)
                pruefe(False, '%r an der Startfrage beendet' % eingabe)
            except SystemExit:
                pruefe(True, '%r an der Startfrage beendet' % eingabe)

        def abgebrochen(*args):
            raise KeyboardInterrupt
        builtins.input = abgebrochen
        gleich(still(S.soll_holen, ordner), False,
               'Strg+C an der Frage heisst nur Cache, nicht Absturz')

        gefragt = []

        def merken(frage=''):
            gefragt.append(frage)
            return 'n'
        builtins.input = merken
        still(S.soll_holen, ordner)
        pruefe('q = beenden' in gefragt[0],
               'und die Startfrage nennt ihren Ausgang, statt ihn raten zu '
               'lassen')

        sys.stdin = echtes_stdin
        builtins.input = abgebrochen
        gleich(still(S.soll_holen, ordner), True,
               'ohne Terminal wird nicht gefragt, sondern geholt')
    finally:
        builtins.input, sys.stdin = echtes_input, echtes_stdin
        shutil.rmtree(ordner)


def test_kennzahlen():
    """Kilometer und Geschwindigkeiten aus der Telemetrie derselben Antwort."""
    d = json_aus_kopf('a' * 24, KOPF_S1, (1, '10/08/2026', '19:16'),
                      'Yamaha MT-07', 3)
    k = S.session_aus_json('a' * 24, d)['kennzahlen']
    # Drei Zeitschritte mit 3, 72 und 108 km/h. Der erste liegt unter der
    # Fahrschwelle: 20 + 30 Meter zaehlen, die 0,8 Meter Rollen nicht.
    gleich(k['meter'], 50, 'Strecke aus Geschwindigkeit mal Zeit')
    gleich(k['meter_gesamt'], 51, 'mit dem Rollen sind es ein paar mehr')
    gleich(k['fahrzeit'], 2.0, 'Fahrzeit ohne das Rollen')
    gleich(k['gesamtzeit'], 3.0, 'Gesamtzeit mit')
    gleich(k['v_max'], 108.0, 'Hoechstgeschwindigkeit')
    gleich(k['v_min'], 36.0,
           'niedrigste Geschwindigkeit ueber der Schwelle -- die 3 km/h '
           'sind Rollen und keine langsamste Kurve')
    gleich(k['v_schnitt'], 90.0, 'Durchschnitt aus Strecke durch Fahrzeit')
    gleich(k['schraeglage_links'], 30.0, 'groesste Schraeglage links')
    gleich(k['schraeglage_rechts'], 40.0, 'groesste Schraeglage rechts')
    gleich(k['punkte'], 4, 'Zahl der Messpunkte')
    gleich(S.kennzahlen_rechnen({}, {}), None,
           'ohne Telemetrie gibt es keine Kennzahlen, keine erfundenen')


def test_kopf_lesen():
    k = S.kopf_lesen(KOPF_S1)
    gleich(k['Track'], 'Talkurs', 'Streckenname aus dem Kopf')
    gleich(k['Configuration'], '', 'leere Konfiguration bleibt leer')
    gleich(k['Session Type'], 'Track', 'Sessiontyp')
    gleich(len(k['runden']), 3, 'drei Rundenzeilen')
    gleich(k['runden'][1]['nr'], 2, 'Rundennummer aus "Lap 2"')
    nahe(k['runden'][1]['zeit'], 90.0, 'Rundenzeit')
    gleich(k['runden'][1]['sektoren'], [30.0, 25.0, 20.0, 0.0, 15.0],
           'Sektorfelder mit der Null an Position 4')
    gleich(len(k['runden'][0]['sektoren']), 5,
           'die Einfahrrunde hat gleich viele Felder')
    # Ein Kopf ohne Rundenzeilen darf nicht abstuerzen.
    gleich(S.kopf_lesen('Format,RaceBox CSV\n')['runden'], [],
           'Kopf ohne Runden')


def test_zahlen_und_zeiten():
    nahe(S.zahl(' 15.701 '), 15.701, 'Zahl mit Leerzeichen')
    nahe(S.zahl('0'), 0.0, 'Null')
    # Der Punkt ist Dezimaltrenner, immer. Auf einem deutschen System wuerde
    # ein Locale-Parser aus 15.701 die Zahl 15701 machen.
    pruefe(S.zahl('15.701') < 16, 'Punkt ist Dezimaltrenner, kein Tausender')
    gleich(S.zeit_text(89.5), '1:29.50', 'Zeit ueber einer Minute')
    gleich(S.zeit_text(15.701), '15.70', 'Zeit unter einer Minute')
    gleich(S.zeit_text(60.0), '1:00.00', 'genau eine Minute')
    gleich(S.zeit_text(None), '--', 'fehlende Zeit')
    gleich(S.zeit_text(89.5, 10), '   1:29.50', 'Zeit rechtsbuendig')
    gleich(S.zeit_text(118.299), '1:58.29',
           'Hundertstel werden abgeschnitten, nicht gerundet -- sonst '
           'stuende dort eine Zeit, die niemand gefahren ist')
    gleich(S.delta_text(-1.4519), '-1.45', 'Delta mit Vorzeichen')
    # 6.8 liegt als 6.799999999999997 im Speicher -- ohne Festzurren auf
    # Tausendstel wuerde daraus beim Kuerzen 6.79.
    gleich(S.delta_text(-6.799999999999997), '-6.80',
           'ein Rechenrest darf keine Hundertstel verschlucken')
    gleich(S.zeit_text(89.99999999999), '1:30.00', 'dasselbe bei Zeiten')
    gleich(S.delta_text(0.0), '+0.00', 'Delta null')


def test_layout():
    alle = {s['id']: s for s in sessions_bauen()}
    gleich(S.session_layout(alle['a' * 24]), (1, 2, 3, 5),
           'Layout aus der vollstaendigen Runde, Position 4 leer')
    nur_teil = {'runden': [{'nr': 1, 'zeit': 10.0,
                            'sektoren': [0, 0, 0, 0, 10.0]}]}
    gleich(S.session_layout(nur_teil), None,
           'aus einer einzelnen Teilrunde laesst sich kein Layout ablesen')

    talkurs = [alle['a' * 24], alle['b' * 24], alle['c' * 24]]
    layout, passend, abweichend = S.layout_bestimmen(talkurs)
    gleich(layout, (1, 2, 3, 5), 'Layout der Strecke')
    gleich(len(passend), 3, 'alle drei Sessions passen')
    gleich(abweichend, [], 'keine Abweichler')

    bergring = [alle['d' * 24], alle['e' * 24]]
    layout, passend, abweichend = S.layout_bestimmen(bergring)
    gleich(layout, (1, 2, 3, 4), 'die neuere Einteilung gilt')
    gleich([s['id'] for s in passend], ['e' * 24], 'nur die neuere passt')
    gleich([s['id'] for s in abweichend], ['d' * 24],
           'die aeltere Einteilung wird als Abweichler erkannt')

    # Eine Session mit nur Teilrunden passt, solange ihre Positionen im
    # gueltigen Layout vorkommen.
    teil = dict(alle['a' * 24])
    teil['runden'] = [{'nr': 1, 'zeit': 10.0, 'sektoren': [0, 0, 0, 0, 10.0]}]
    layout, passend, abweichend = S.layout_bestimmen([alle['b' * 24], teil])
    gleich(len(passend), 2, 'reine Teilrunden-Session passt zum Layout')


def test_runden_und_sektoren():
    alle = {s['id']: s for s in sessions_bauen()}
    r = S.runden([alle['a' * 24], alle['b' * 24]], (1, 2, 3, 5))
    gleich(len(r), 6, 'sechs Runden aus zwei Turns')
    gleich([x['vollstaendig'] for x in r],
           [False, True, True, False, True, False],
           'Ein- und Ausfahrrunden sind Teilrunden')
    gleich(r[0]['sektoren'], {5: 16.0},
           'die Einfahrrunde bringt nur Position 5')
    gleich(r[5]['sektoren'], {1: 28.0, 2: 24.0},
           'die Ausfahrrunde bringt Position 1 und 2')
    pruefe(all(x['stimmig'] for x in r),
           'Summe der Sektoren ergibt die Rundenzeit')

    # Erkannt wird das beim Lesen, nicht beim Rechnen: Was im Cache liegt,
    # ist schon bereinigt und taugt dafuer nicht mehr.
    krumm = S.kopf_lesen('Track,Talkurs\n'
                         'Lap 1, 90.000, sectors, 30.000,25.000,20.000,0,10.000\n')
    gleich(krumm['runden'][0]['stimmig'], False,
           'eine Rundenzeit, die nicht zur Summe passt, faellt beim Lesen auf')
    heil = S.kopf_lesen('Track,Talkurs\n'
                        'Lap 1, 90.000, sectors, 30.000,25.000,20.000,0,15.000\n')
    gleich(heil['runden'][0]['stimmig'], True, 'eine stimmige nicht')

    bester = S.bester_sektor(r, 1)
    nahe(bester['sektoren'][1], 28.0, 'schnellster Sektor 1 (aus der Ausfahrrunde)')
    gleich(bester['vollstaendig'], False, 'er stammt aus einer Teilrunde')
    nahe(S.bester_sektor(r, 1, True)['sektoren'][1], 28.5,
         'schnellster Sektor 1 nur aus vollstaendigen Runden')
    rang = S.sektor_rangliste(r, 1)
    gleich([round(x['sektoren'][1], 1) for x in rang], [28.0, 28.5, 29.0],
           'Rangliste Sektor 1, beste zuerst')
    gleich(len(S.sektor_rangliste(r, 5, 3)), 3, 'hoechstens drei je Sektor')


def test_theoretische_runde():
    alle = {s['id']: s for s in sessions_bauen()}
    r = S.runden([alle['a' * 24], alle['b' * 24]], (1, 2, 3, 5))
    theo, teile = S.theoretische_runde(r, (1, 2, 3, 5))
    # 28.0 + 24.0 + 19.5 + 10.0
    nahe(theo, 86.0, 'theoretische Runde mit Teilrunden')
    gleich([p for p, _ in teile], [1, 2, 3, 5], 'ein Teil je Sektor')
    streng, _ = S.theoretische_runde(r, (1, 2, 3, 5), True)
    # 28.5 + 25.0 + 19.5 + 14.5
    nahe(streng, 87.5, 'theoretische Runde nur aus vollstaendigen Runden')
    pruefe(streng > theo, 'ohne Teilrunden ist sie langsamer')
    nahe(S.beste_runde(r)['zeit'], 88.3, 'schnellste vollstaendige Runde')

    # Fehlt zu einer Position jede Messung, gibt es keine Zahl -- und schon
    # gar keine aus zu wenigen Summanden.
    ohne, _ = S.theoretische_runde(r, (1, 2, 3, 4, 5))
    gleich(ohne, None, 'ohne Messung fuer eine Position keine Zahl')


def test_auswerten():
    alle = {s['id']: s for s in sessions_bauen()}
    z = S.auswerten([alle['a' * 24], alle['b' * 24]], (1, 2, 3, 5))
    gleich(z['anzahl_runden'], 6, 'Rundenzahl')
    gleich(z['anzahl_vollstaendig'], 3, 'davon vollstaendig')
    gleich(z['tage'], 1, 'ein Fahrtag')
    nahe(z['best']['zeit'], 88.3, 'Bestrunde')
    nahe(z['theo'], 86.0, 'theoretische Runde')
    nahe(z['theo_streng'], 87.5, 'strenge theoretische Runde')
    pruefe(z['streng_weicht_ab'], 'der Unterschied wird gemeldet')
    nahe(z['delta'], -2.3, 'Delta zur Bestrunde')
    gleich(z['anzahl_unstimmig'], 0, 'keine unstimmige Runde')

    turns = {t['session']['turn']: t for t in z['je_turn']}
    nahe(turns[1]['theo'], 88.5, 'theoretische Runde in Turn 1')
    nahe(turns[2]['theo'], 86.3, 'theoretische Runde in Turn 2')
    nahe(turns[1]['best']['zeit'], 89.5, 'Bestrunde in Turn 1')
    gleich(len(z['je_tag']), 1, 'ein Tageseintrag')
    nahe(z['je_tag'][0]['theo'], 86.0, 'theoretische Runde des Fahrtags')

    # Ein Fahrzeug mit einer einzigen Runde: theoretisch gleich echt.
    z2 = S.auswerten([alle['c' * 24]], (1, 2, 3, 5))
    nahe(z2['theo'], 110.0, 'eine Runde ergibt sich selbst')
    nahe(z2['delta'], 0.0, 'kein Delta')
    pruefe(not z2['streng_weicht_ab'], 'nichts haengt an einer Teilrunde')


def test_strecken_und_fahrzeuge():
    sessions = sessions_bauen()
    strecken, ausgeblendet, _ = S.strecken_bauen(sessions, MUSTER)
    namen = [e['strecke'] for e in strecken]
    gleich(namen, ['Talkurs', 'Bergring Nord'],
           'juengster Fahrtag zuerst, Teststrecken raus')
    gleich(sorted(ausgeblendet), ['Kartbahn Sued', 'Uebungsplatz Nord'],
           'beide Teststrecken ausgeblendet')

    talkurs = strecken[0]
    fahrzeuge = [z['fahrzeug'] for z in talkurs['fahrzeuge']]
    gleich(sorted(fahrzeuge), ['Suzuki SV650', 'Yamaha MT-07'],
           'zwei Fahrzeuge getrennt')
    gleich(fahrzeuge[0], 'Yamaha MT-07', 'das schnellste Fahrzeug zuerst')
    yamaha = [z for z in talkurs['fahrzeuge'] if z['fahrzeug'] == 'Yamaha MT-07'][0]
    suzuki = [z for z in talkurs['fahrzeuge'] if z['fahrzeug'] == 'Suzuki SV650'][0]
    nahe(yamaha['theo'], 86.0, 'Fahrzeug 1 rechnet nur mit eigenen Sektoren')
    nahe(suzuki['theo'], 110.0, 'Fahrzeug 2 ebenso')
    pruefe(all(r['session']['fahrzeug'] == 'Suzuki SV650'
               for r in suzuki['alle_runden']),
           'keine fremde Runde im Fahrzeugblock')

    bergring = strecken[1]
    gleich(len(bergring['abweichend']), 1, 'eine Session mit alter Einteilung')
    gleich(sum(len(z['sessions']) for z in bergring['fahrzeuge']), 1,
           'die abweichende Session wird nicht mitgerechnet')

    alles, keins, _ = S.strecken_bauen(sessions, [])
    gleich(len(alles), 4, 'ohne Filter sind alle Strecken da')
    gleich(alles[0]['strecke'], 'Kartbahn Sued', 'auch dort neueste zuerst')
    gleich(alles[0]['fahrzeuge'][0]['fahrzeug'], 'ohne Fahrzeug',
           'Session ohne Fahrzeug bekommt einen eigenen Block')
    gleich(keins, {}, 'nichts ausgeblendet')


def test_ausblenden():
    pruefe(S.ist_ausgeblendet('Uebungsplatz Nord gross', MUSTER),
           'ein Muster mit * trifft die ganze Familie')
    pruefe(S.ist_ausgeblendet('kartbahn sued', MUSTER),
           'Gross- und Kleinschreibung egal')
    pruefe(not S.ist_ausgeblendet('Talkurs', MUSTER),
           'echte Rennstrecken bleiben')

    # Ab Werk wird nichts ausgeblendet -- das Werkzeug soll auch fuer
    # andere taugen, und deren Uebungsplaetze heissen anders.
    gleich(S.AUSBLENDEN_VORGABE, [], 'keine Strecke steht im Quelltext')

    ordner = tempfile.mkdtemp()
    try:
        gleich(S.ausblenden_lesen(ordner), [],
               'ohne Datei wird nichts ausgeblendet')
        pfad = os.path.join(ordner, 'ausblenden')
        pruefe(os.path.exists(pfad),
               'die Datei wird beim ersten Lesen angelegt -- was dasteht, '
               'findet man')
        inhalt = open(pfad, encoding='utf-8').read()
        pruefe('--ausblenden' in inhalt and 'Platzhalter' in inhalt,
               'und erklaert sich selbst')
        gleich([z for z in inhalt.splitlines()
                if z.strip() and not z.startswith('#')], [],
               'aber ohne eine einzige wirksame Zeile')

        gleich(still(S.ausblenden_ergaenzen, ['Uebungsplatz*'], ordner), ['Uebungsplatz*'],
               'ein Muster laesst sich vom Terminal aus aufnehmen')
        gleich(S.ausblenden_lesen(ordner), ['Uebungsplatz*'],
               'und steht beim naechsten Lesen drin')
        gleich(still(S.ausblenden_ergaenzen, ['Uebungsplatz*'], ordner), ['Uebungsplatz*'],
               'zweimal dasselbe ergibt keine zweite Zeile')
        gleich(still(S.ausblenden_ergaenzen, ['Kartbahn*'], ordner),
               ['Uebungsplatz*', 'Kartbahn*'], 'ein zweites kommt dazu')

        with open(pfad, 'w', encoding='utf-8') as f:
            f.write('# Kommentar\n\n  Talkurs  \n')
        gleich(S.ausblenden_lesen(ordner), ['Talkurs'],
               'Kommentare, Leerzeilen und Leerraum werden uebergangen')
    finally:
        shutil.rmtree(ordner)

    # Ein Ort ohne Schreibrecht ist kein Grund abzubrechen: Dann wird eben
    # nichts ausgeblendet.
    handle, datei = tempfile.mkstemp()
    os.close(handle)
    try:
        gleich(S.ausblenden_lesen(os.path.join(datei, 'geht-nicht')), [],
               'wo sich nichts anlegen laesst, wird nichts ausgeblendet')
    finally:
        os.unlink(datei)


def test_fahrzeug_waehlen():
    """Nach der Strecke fragen, welches Fahrzeug -- Enter zeigt alle."""
    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    talkurs = strecken[0]
    pruefe(len(talkurs['fahrzeuge']) > 1, 'die Teststrecke hat mehrere')
    echtes_input = builtins.input
    try:
        builtins.input = lambda *args: '2'
        gewaehlt = still(S.fahrzeug_waehlen, talkurs)
        gleich(gewaehlt, talkurs['fahrzeuge'][1], 'die Nummer waehlt aus')
        for eingabe, was in (('', 'Enter zeigt alle'),
                             ('9', 'eine Nummer ausserhalb zeigt alle'),
                             ('bla', 'und alles Unverstandene ebenso')):
            builtins.input = lambda *args, e=eingabe: e
            gleich(still(S.fahrzeug_waehlen, talkurs), None, was)

        gefragt = []

        def merken(frage=''):
            gefragt.append(frage)
            return 'q'
        builtins.input = merken
        gleich(still(S.fahrzeug_waehlen, talkurs), S.ENDE,
               '`q` heisst hier zurueck und nicht "alle zeigen"')
        pruefe('q = zurueck' in gefragt[0],
               'und die Zeile sagt genau das -- als einzige Stelle, an der '
               'der Ausgang eine Ebene und nicht den Lauf verlaesst')

        def abgebrochen(*args):
            raise KeyboardInterrupt
        builtins.input = abgebrochen
        gleich(still(S.fahrzeug_waehlen, talkurs), None,
               'Strg+C an der Frage zeigt alle, statt zu werfen')

        # Bei einem einzigen Fahrzeug wird gar nicht erst gefragt.
        eines = strecken[1]
        gleich(len(eines['fahrzeuge']), 1, 'die zweite Strecke hat eines')
        gleich(S.fahrzeug_waehlen(eines), None,
               'dann gibt es nichts zu fragen')
    finally:
        builtins.input = echtes_input

    # Ein `nur`, das gar kein Fahrzeug dieser Strecke ist, zeigt alle und
    # nicht keines. Eine wortlos leere Seite waere ein Fehler, den man an
    # der falschen Stelle sucht.
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(talkurs, nur=S.ENDE)
    pruefe('Woraus die Golden Lap besteht' in puffer.getvalue(),
           'ein unbekanntes `nur` zeigt alle Fahrzeuge, statt wortlos '
           'nichts auszugeben')

    # Und die Detailansicht zeigt dann nur dieses eine.
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(talkurs, nur=talkurs['fahrzeuge'][0])
    text = puffer.getvalue()
    pruefe(talkurs['fahrzeuge'][0]['fahrzeug'] in text, 'das gewaehlte steht da')
    pruefe(talkurs['fahrzeuge'][1]['fahrzeug'] not in text,
           'und das andere nicht')


def test_abbruch():
    """Strg+C ist eine Ansage, kein Absturz."""
    strecken, ausgeblendet, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    echtes_input = builtins.input

    def abgebrochen(*args):
        raise KeyboardInterrupt

    try:
        builtins.input = abgebrochen
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.schleife(strecken, ausgeblendet, 8)
        pruefe(True, 'Strg+C an der Auswahl beendet, ohne zu werfen')
    except KeyboardInterrupt:
        pruefe(False, 'Strg+C an der Auswahl beendet, ohne zu werfen')
    finally:
        builtins.input = echtes_input


def test_sichtbarer_ausgang():
    """Jede Eingabezeile nennt ihren Ausgang, und `q` wirkt an jeder."""
    strecken, ausgeblendet, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    echtes_input = builtins.input
    try:
        gefragt = []

        def antworten(*eingaben):
            folge = iter(eingaben)

            def f(frage=''):
                gefragt.append(frage)
                return next(folge)
            return f

        # Auf der Streckenauswahl beendet `q` den Lauf -- genau wie das
        # Enter, das es schon immer gab.
        for eingabe in ('q', 'ende', 'Q'):
            del gefragt[:]
            builtins.input = antworten(eingabe)
            still(S.schleife, strecken, ausgeblendet, 3)
            pruefe(len(gefragt) == 1,
                   '%r auf der Streckenauswahl beendet sofort' % eingabe)
        pruefe('q = Ende' in gefragt[0],
               'und die Zeile nennt beide Wege hinaus')

        # Nach einer Ansicht ebenso: `q` statt eines geratenen Strg+C.
        del gefragt[:]
        builtins.input = antworten('1', '', 'q')
        still(S.schleife, strecken, ausgeblendet, 3)
        pruefe(any('q = Ende' in f for f in gefragt[1:]),
               'auch das Anhalten nach einer Ansicht nennt seinen Ausgang')
        gleich(len(gefragt), 3,
               'und `q` dort beendet, statt zur Uebersicht zurueckzugehen')

        # `q` in der Fahrzeugauswahl fuehrt zurueck, nicht hinaus: Danach
        # steht die Uebersicht wieder da und fragt erneut.
        del gefragt[:]
        builtins.input = antworten('1', 'q', 'q')
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.schleife(strecken, ausgeblendet, 3)
        gleich(len(gefragt), 3, 'nach dem Zurueck wird wieder gefragt')
        pruefe('Woraus die Golden Lap besteht' not in puffer.getvalue(),
               'und die Detailansicht wird gar nicht erst gezeigt')
    finally:
        builtins.input = echtes_input


def test_zugangsfrage_nennt_ihren_ausgang():
    """Auch die Frage nach dem Zugang sagt, wie man wieder herauskommt.

    `q` waere hier kein Ausgang, sondern ein zulaessiges Passwort. Der
    Ausgang ist deshalb die leere Eingabe -- und genau darum muss er in
    der Zeile stehen und nicht im README.
    """
    import getpass
    echtes_input, echtes_getpass = builtins.input, getpass.getpass
    gefragt = []
    try:
        def merken(frage=''):
            gefragt.append(frage)
            return ''
        builtins.input = merken
        getpass.getpass = merken
        try:
            still(S.zugang_fragen)
            pruefe(False, 'ohne E-Mail bricht die Zugangsfrage ab')
        except SystemExit:
            pruefe(True, 'ohne E-Mail bricht die Zugangsfrage ab')
        pruefe(gefragt and 'leer = abbrechen' in gefragt[0],
               'und die Zeile nennt diesen Ausgang, statt ihn raten zu lassen')
    finally:
        builtins.input, getpass.getpass = echtes_input, echtes_getpass


def test_teilrunde_wird_markiert():
    """Steckt eine Teilrunde in der Golden Lap, steht das dran."""
    strecken, ausgeblendet, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    talkurs = strecken[0]
    yamaha = [z for z in talkurs['fahrzeuge']
              if z['fahrzeug'] == 'Yamaha MT-07'][0]
    suzuki = [z for z in talkurs['fahrzeuge']
              if z['fahrzeug'] == 'Suzuki SV650'][0]
    gleich(yamaha['aus_teilrunden'], 2,
           'zwei Sektorbestzeiten stammen aus einer Ein-/Ausfahrrunde')
    gleich(suzuki['aus_teilrunden'], 0, 'beim anderen Fahrzeug keine')

    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_uebersicht(strecken, ausgeblendet)
    text = puffer.getvalue()
    zeilen = text.splitlines()
    mit = [z for z in zeilen if 'Yamaha MT-07' in z and 'TALKURS' not in z][0]
    ohne = [z for z in zeilen if 'Suzuki SV650' in z][0]
    pruefe('(TR) 1:26.00' in mit,
           'die Uebersicht markiert die Golden Lap mit (TR)')
    pruefe('(TR)' not in ohne, 'und die unbetroffene Zeile nicht')

    # Die Marke steht *links* vor der Zahl, damit die Spalte stehen
    # bleibt. Genau das wird hier nachgemessen: Beide Golden Laps muessen
    # an derselben Stelle enden.
    gleich(mit.rindex('1:26.00') + 7, ohne.rindex('1:50.00') + 7,
           'die Golden Lap steht in beiden Zeilen in derselben Spalte -- '
           'die Marke darf die Tabelle nicht verschieben')

    pruefe('(TR) In dieser Golden Lap steckt' in text,
           'und unter der Tabelle steht, was die Marke bedeutet')
    pruefe(max(len(z) for z in zeilen) < 95,
           'die Uebersicht bleibt schmal genug (%d Zeichen)'
           % max(len(z) for z in zeilen))

    # In der Detailansicht dasselbe -- und darunter die Rechnung ohne die
    # Teilrunden, mit beiden Differenzen.
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(talkurs, nur=yamaha)
    zeilen = puffer.getvalue().splitlines()
    mit = [z for z in zeilen if z.startswith('  Golden Lap (TR)')][0]
    ohne = [z for z in zeilen if z.startswith('  Golden Lap ') and
            '(TR)' not in z][0]
    pruefe('1:26.00' in mit, 'oben die Golden Lap, wie sie gerechnet wird')
    pruefe('2 Sektorbestzeit(en) aus einer Ein-/Ausfahrrunde' in mit,
           'und in derselben Zeile der Grund fuer die Marke')
    pruefe('1:27.50' in ohne, 'darunter dieselbe Rechnung ohne Teilrunden')
    pruefe('+1.50 ohne diese' in ohne,
           'mit der Differenz zur markierten Golden Lap')
    pruefe('-0.80 gegenueber der Bestrunde' in ohne,
           'und der zur wirklich gefahrenen Bestrunde')

    # Und unter jedem Sektor aus einer Teilrunde derselbe Sektor aus einer
    # vollstaendigen Runde. Ohne ihn sagt die Zeile darueber nur, dass
    # etwas nicht sauber ist, aber nicht, was die Alternative kostet.
    daneben = [z for z in zeilen if 'ohne Teilrunde' in z]
    gleich(len(daneben), 2,
           'zu jedem der beiden Teilrunden-Sektoren steht die Zeit aus '
           'einer vollstaendigen Runde darunter')
    pruefe('28.50' in daneben[0] and '25.00' in daneben[1],
           'und zwar die aus der vollstaendigen Runde, nicht dieselbe '
           'noch einmal')
    # 28.50 + 25.00 + 19.50 + 14.50 = 87.50 -- dieselbe Zahl, die oben als
    # Golden Lap ohne Teilrunden steht. Geht das nicht auf, widerspricht
    # sich die Ansicht selbst.
    nahe(yamaha['theo_streng'], 87.5,
         'die vier strengen Sektoren ergeben die Golden Lap ohne '
         'Teilrunden')

    # Ohne Teilrunde in der Golden Lap gibt es auch nichts zu markieren
    # und nichts zu vergleichen.
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(talkurs, nur=suzuki)
    text = puffer.getvalue()
    pruefe('(TR)' not in text, 'ohne Teilrunde keine Marke')
    gleich(len([z for z in text.splitlines()
                if z.startswith('  Golden Lap')]), 1,
           'und nur eine Golden-Lap-Zeile statt zweier')


def test_detail_spalten_stehen_buendig():
    """Die Zeitspalte im Kopf der Detailansicht muss senkrecht stehen.

    Die Beschriftungen sind verschieden lang -- `2. beste` ist ein Zeichen
    kuerzer als `Bestrunde`. Wer die Luecke je Zeile von Hand auszaehlt,
    hat sie irgendwann um eins daneben, und in einer Kommandozeile faellt
    genau das auf.
    """
    strecken, _, _ = S.strecken_bauen(sessions_bauen(), MUSTER)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(strecken[0])
    kopfzeilen = [z for z in puffer.getvalue().splitlines()
                  if z.startswith('  ') and any(
                      z.strip().startswith(w) for w in
                      ('Bestrunde', '2. beste', '3. beste', 'Turn-Theo',
                       'Golden Lap', 'ohne Teilrunden'))]
    pruefe(len(kopfzeilen) >= 4, 'der Kopf der Detailansicht hat Zeilen')
    # Die Zeit ist die erste Zahlengruppe nach der Beschriftung. Gesucht
    # wird, wo sie endet -- rechtsbuendig gesetzt, also muss das Ende
    # ueberall dasselbe sein.
    enden = set()
    for z in kopfzeilen:
        treffer = list(re.finditer(r'\d+[:.]\d[\d.:]*', z))
        pruefe(bool(treffer), 'in %r steht eine Zeit' % z.strip()[:20])
        if treffer:
            enden.add(treffer[0].end())
    gleich(len(enden), 1,
           'die Zeitspalte endet in jeder Kopfzeile an derselben Stelle')


def test_auswahl_und_ausgabe():
    sessions = sessions_bauen()
    strecken, ausgeblendet, _ = S.strecken_bauen(sessions, MUSTER)
    gleich(S.strecke_waehlen(strecken, '1')['strecke'], 'Talkurs',
           'Auswahl ueber die Nummer')
    gleich(S.strecke_waehlen(strecken, 'bergring n')['strecke'],
           'Bergring Nord', 'Auswahl ueber einen Namensteil')
    gleich(S.strecke_waehlen(strecken, '9'), None, 'Nummer ausserhalb')
    gleich(S.strecke_waehlen(strecken, ''), None, 'leere Eingabe')

    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_uebersicht(strecken, ausgeblendet)
    text = puffer.getvalue()
    pruefe('TALKURS' in text and 'Yamaha MT-07' in text,
           'Uebersicht nennt Strecke und Fahrzeug')
    pruefe('  1   TALKURS' in text,
           'der Streckenname steht in Grossbuchstaben -- in einer '
           'Kommandozeile gibt es keine Fettschrift')
    pruefe('1:28.30' in text, 'Uebersicht zeigt die Bestrunde als m:ss.mmm')
    pruefe('1:26.00' in text, 'Uebersicht zeigt die theoretische Runde')
    pruefe('-2.30' in text, 'Uebersicht zeigt das Delta')

    # Je Fahrzeug eine Zeile, mit allen fuenf Zahlen nebeneinander.
    zeilen = text.splitlines()
    zeile = [z for z in zeilen if 'Yamaha MT-07' in z][0]
    for wert, was in (('1:28.30', 'Bestrunde'), ('+1.45', 'Streuung'),
                      ('1:26.30', 'Turn-Theo'), ('1:26.00', 'Golden Lap'),
                      ('-2.30', 'Delta')):
        pruefe(wert in zeile, 'in der Fahrzeugzeile steht die %s' % was)
    pruefe(sum(1 for z in zeilen if z.strip().startswith('---')) >= 3,
           'zwischen den Strecken steht eine Trennlinie')
    pruefe(max(len(z) for z in zeilen) < 95,
           'die Uebersicht bleibt schmal genug fuer eine Kommandozeile '
           '(%d Zeichen)' % max(len(z) for z in zeilen))

    # -- die Detailansicht ------------------------------------------------
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(strecken[0])
    text = puffer.getvalue()
    pruefe('Teilrunde' in text,
           'Sektoren aus Ein- und Ausfahrrunden sind gekennzeichnet')
    pruefe('1:27.50' in text, 'die strenge theoretische Runde steht dabei')
    pruefe('Turn 1 (19:16)' in text, 'Herkunft mit Turn und Ortszeit')
    pruefe('Woraus die Golden Lap besteht' in text
           and 'Die drei besten je Sektor' in text
           and 'je Turn' in text and 'je Fahrtag' in text,
           'alle vier Abschnitte der Detailansicht')
    pruefe('Theoretisch beste Runde je Turn' in text,
           'theoretisch ist die Runde, nicht das Bestsein')
    pruefe('Beste theoretische' not in text, 'die alte Formulierung ist weg')

    # RaceBox laesst Sektorfeld 4 leer -- angezeigt wird trotzdem eine
    # Vierteilung, so wie die App sie zeigt.
    gleich(S.sektor_nummer((1, 2, 3, 5), 5), 4,
           'der Sektor an Position 5 heisst Sektor 4')
    gleich(S.sektor_nummer((1, 2, 3, 5), 1), 1, 'der erste bleibt der erste')
    pruefe('Sektor 4' in text and 'Sektor 5' not in text,
           'die Detailansicht zaehlt Sektoren durch, ohne Luecke')
    pruefe('trotzdem zwischen zwei' in text,
           'die Kennzeichnung Teilrunde wird einmal erklaert')
    pruefe('4 Sektoren' in text, 'die Zahl der Sektoren stimmt')
    pruefe('5 Sektorfelder, belegt sind 1, 2, 3, 5' in text,
           'die Luecke im Sektorfeld wird benannt statt verschwiegen')
    gleich(strecken[0]['felder'], 5, 'fuenf Felder je Runde')
    # Wo keine Luecke ist, ist auch nichts zu erklaeren.
    puffer2 = io.StringIO()
    with contextlib.redirect_stdout(puffer2):
        S.zeige_detail(strecken[1])
    pruefe('Sektorfelder' not in puffer2.getvalue(),
           'eine Strecke ohne Luecke bekommt die Zeile nicht')

    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_detail(strecken[1])
    pruefe('Splitpunkte geaendert' in puffer.getvalue(),
           'die geaenderte Sektoreinteilung wird gemeldet')


def statistik_cache():
    """Vier erfundene Cache-Eintraege mit runden Kennzahlen.

    Von Hand geschrieben und nicht aus den Testsessions abgeleitet: Die
    Summen sollen sich im Kopf nachrechnen lassen, und jede Zahl soll sich
    von jeder anderen unterscheiden. Ein Mittelwert, der zufaellig
    dasselbe ergibt wie eine Summe, prueft nichts.
    """
    def kz(meter, gesamt, fahrzeit, gesamtzeit, v_max, v_min, v_schnitt,
           links, rechts, punkte):
        return {'meter': meter, 'meter_gesamt': gesamt, 'fahrzeit': fahrzeit,
                'gesamtzeit': gesamtzeit, 'v_max': v_max, 'v_min': v_min,
                'v_schnitt': v_schnitt, 'schraeglage_links': links,
                'schraeglage_rechts': rechts, 'punkte': punkte,
                'schwelle': 5.0}

    def eintrag(sid, strecke, fahrzeug, datum, turn, zeiten, kennzahlen):
        return {'version': S.CACHE_VERSION, 'id': sid * 24, 'strecke': strecke,
                'konfiguration': '', 'fahrzeug': fahrzeug, 'datum': datum,
                'startzeit': '10:00', 'turn': turn, 'quelle': 'json+csv',
                'runden': [{'nr': nr, 'zeit': z, 'sektoren': [z],
                            'stimmig': True}
                           for nr, z in enumerate(zeiten, 1)],
                'kennzahlen': kennzahlen}

    return [
        eintrag('a', 'Talkurs', 'Yamaha MT-07', '2026-08-01', 1, [60.0, 62.0],
                kz(10000, 11000, 310.0, 400.0, 200.0, 40.0, 116.13,
                   40.0, 45.0, 10000)),
        eintrag('b', 'Talkurs', 'Yamaha MT-07', '2026-08-01', 2, [61.0],
                kz(5000, 5500, 100.0, 150.0, 210.0, 30.0, 180.0,
                   50.0, 35.0, 5000)),
        eintrag('c', 'Bergring Nord', 'Suzuki SV650', '2026-08-02', 1, [90.0],
                kz(3000, 3600, 90.0, 120.0, 180.0, 50.0, 120.0,
                   20.0, 20.0, 3000)),
        # Ein Eintrag aus dem alten CSV-Weg: Runden ja, Telemetrie nein.
        eintrag('d', 'Bergring Nord', 'Suzuki SV650', '2026-08-03', 1,
                [91.0, 92.0], None),
    ]


def test_zahlen_der_statistik():
    """km, Stunden und Messwerte in der Form, in der sie dastehen."""
    gleich(S.km_text(18000), '18.0', 'Meter werden zu Kilometern')
    gleich(S.km_text(1234), '1.2', 'auf eine Nachkommastelle')
    gleich(S.km_text(None), '-', 'wo nichts gemessen wurde, steht ein Strich')
    gleich(S.km_text(500, 8), '     0.5', 'rechtsbuendig in fester Breite')
    gleich(S.km_text(2910300, 0, 0), '2910',
           'in den Tabellen ohne Nachkomma -- dort sagt die Stelle nichts')

    gleich(S.stunden_text(3661), '1:01:01', 'Stunden, Minuten, Sekunden')
    gleich(S.stunden_text(670), '0:11:10',
           'unter einer Stunde steht die Null trotzdem da -- die Spalte '
           'soll nicht springen')
    gleich(S.stunden_text(360000), '100:00:00',
           'auch dreistellige Stundenzahlen bleiben lesbar')
    gleich(S.stunden_text(None), '-', 'ohne Zeit ein Strich')

    gleich(S.wert_text(129.6), '129.6', 'eine Messzahl mit einer Stelle')
    gleich(S.wert_text(5.0, 0, 0), '5', 'die Fahrschwelle ohne Nachkomma')
    gleich(S.wert_text(None), '-', 'und ohne Wert ein Strich')


def test_statistik_summen():
    """Alles Gefahrene, von Hand nachgerechnet."""
    eintraege = statistik_cache()
    g = S.kennzahlen_summieren(eintraege)

    gleich(g['sessions'], 4, 'vier Turns')
    gleich(g['tage'], 3, 'an drei Fahrtagen -- zwei Turns fielen auf einen')
    gleich(g['runden'], 6, 'sechs Runden')

    gleich(g['meter'], 18000, 'gefahrene Meter, summiert')
    gleich(g['meter_gesamt'], 20100, 'Meter insgesamt, auch das Rollen')
    gleich(g['meter_gesamt'] - g['meter'], 2100,
           'was dazwischen liegt, ist Rollen unter der Fahrschwelle -- eine '
           'eigene Zahl bekommt es nicht mehr, seit die Ansicht '
           '`aufgezeichnet` und `gefahren` untereinander stellt')
    nahe(g['fahrzeit'], 500.0, 'Fahrzeit, summiert')
    nahe(g['gesamtzeit'], 670.0, 'Gesamtzeit, summiert')
    nahe(g['standzeit'], 170.0, 'Standzeit ist die Differenz der beiden')
    gleich(g['punkte'], 18000, 'Messpunkte, summiert')

    gleich(g['v_max'], 210.0,
           'die hoechste Geschwindigkeit ist das Maximum, nicht die letzte')
    gleich(g['v_min'], 30.0,
           'die niedrigste ist das Minimum, nicht die letzte')
    gleich(g['schraeglage_links'], 50.0, 'groesste Schraeglage links')
    gleich(g['schraeglage_rechts'], 45.0, 'groesste Schraeglage rechts')

    # 18000 m / 500 s = 36 m/s = 129.6 km/h. Der Mittelwert der drei
    # Schnitte waere 138.71 -- ein kurzer Turn zaehlte darin so viel wie
    # ein langer. Ueber die Gesamtzeit gerechnet waeren es 108.0; dann
    # faehrt die Standzeit im Schnitt mit.
    nahe(g['v_schnitt'], 129.6,
         'der Schnitt kommt aus der Summe der Meter durch die Summe der '
         'Fahrzeit')

    gleich(g['mit_kennzahlen'], 3, 'drei Sessions haben Telemetrie')
    gleich(g['ohne_kennzahlen'], 1,
           'die vierte nicht -- und das wird gezaehlt, nicht uebergangen')
    gleich(g['schwellen'], [5.0], 'alle nach derselben Fahrschwelle')

    # Ein Cache ganz ohne Telemetrie ist kein Fehler, sondern der alte
    # CSV-Weg. Dann bleiben die Kilometer leer und sagen das auch.
    alt = S.kennzahlen_summieren(sessions_bauen())
    gleich(alt['meter'], 0, 'ohne Telemetrie keine Kilometer')
    gleich(alt['v_schnitt'], None, 'und kein erfundener Schnitt')
    gleich(alt['meter_runden'], None,
           'und keine null Kilometer in Runden -- null hiesse, es sei keine '
           'Runde gefahren worden')
    gleich(alt['ohne_kennzahlen'], len(sessions_bauen()),
           'stattdessen stehen alle Sessions als telemetrielos da')
    pruefe(alt['runden'] > 0, 'ihre Runden zaehlen trotzdem')


def test_statistik_gruppen():
    """Insgesamt, je Fahrzeug, je Strecke -- dieselbe Dreiteilung."""
    stat = S.statistik_bauen(statistik_cache())
    gleich(stat['gesamt']['meter'], 18000, 'die Summe steht oben')

    namen = [z['name'] for z in stat['je_fahrzeug']]
    gleich(namen, ['Yamaha MT-07', 'Suzuki SV650'],
           'wer am meisten gefahren ist, steht oben')
    yamaha = stat['je_fahrzeug'][0]['zahlen']
    gleich(yamaha['meter'], 15000, 'je Fahrzeug wird eigen summiert')
    gleich(yamaha['tage'], 1, 'beide Turns lagen an einem Tag')
    gleich(yamaha['sessions'], 2, 'zwei Turns')
    gleich(yamaha['runden'], 3, 'drei Runden')
    nahe(yamaha['v_schnitt'], 131.71,
         'auch je Fahrzeug ist der Schnitt gewichtet', 0.005)

    # Der Schnitt auf der gezaehlten Runde ist eine eigene Zahl: Im
    # Gesamtschnitt stecken Aus- und Einfahrrunde mit drin.
    eigene = statistik_cache()
    eigene[0]['kennzahlen'].update(meter_runden=9000, fahrzeit_runden=300.0)
    g = S.kennzahlen_summieren(eigene)
    nahe(g['v_schnitt_runden'], 108.0,
         '9000 m in 300 s Rundenfahrzeit sind 108 km/h')
    pruefe(abs(g['v_schnitt_runden'] - g['v_schnitt']) > 1,
           'und das ist nicht derselbe Wert wie der Gesamtschnitt')
    gleich(S.kennzahlen_summieren(statistik_cache())['v_schnitt_runden'],
           None, 'ohne Rundenfahrzeit kein erfundener Rundenschnitt')

    namen = [z['name'] for z in stat['je_strecke']]
    gleich(namen, ['Talkurs', 'Bergring Nord'], 'dasselbe je Strecke')
    gleich(stat['je_strecke'][1]['zahlen']['ohne_kennzahlen'], 1,
           'die Session ohne Telemetrie faellt bei ihrer Strecke auf')

    # Verschiebt RaceBox die Splitpunkte, steht dieselbe Strecke zweimal in
    # der Uebersicht -- als `Talkurs (1)` und `Talkurs (2)`. Die Statistik
    # muss sie genauso benennen: Zwei Ansichten, die dieselbe Strecke
    # verschieden nennen, verwirren mehr als der Sonderfall selbst.
    anders = dict(statistik_cache()[0])
    anders['id'] = 'y' * 24
    anders['konfiguration'] = 'Sprint'
    anders['turn'] = 3
    stat = S.statistik_bauen(statistik_cache() + [anders])
    namen = [z['name'] for z in stat['je_strecke']]
    pruefe('Talkurs (1)' in namen and 'Talkurs (2)' in namen,
           'zwei Layouts derselben Strecke werden durchnummeriert')
    pruefe('Bergring Nord' in namen,
           'eine Strecke mit nur einem Layout bekommt keine Nummer')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(stat)
    ausgabe = puffer.getvalue()
    pruefe('Layouts:' in ausgabe and 'Talkurs (2) = Sprint' in ausgabe
           and 'Talkurs (1) = ohne Namen' in ausgabe,
           'und die Nummern werden erklaert, statt unerklaert dazustehen')

    # Eine doppelt abgelegte Session darf ihre Kilometer nicht zweimal
    # beisteuern. Gleicher Tag, gleiche Startzeit, gleicher Turn, gleiches
    # Fahrzeug -- nur eine andere Kennung und weniger Runden.
    doppelt = dict(statistik_cache()[0])
    doppelt['id'] = 'z' * 24
    doppelt['runden'] = doppelt['runden'][:1]
    doppelt['kennzahlen'] = dict(doppelt['kennzahlen'], meter=9000000,
                                 meter_gesamt=9000000)
    stat = S.statistik_bauen(statistik_cache() + [doppelt])
    gleich(stat['gesamt']['meter'], 18000,
           'die doppelte Fassung zaehlt nicht noch einmal mit')
    gleich(len(stat['doppelte']), 1, 'sie wird aber benannt')

    # Ausgeblendete Strecken zaehlen nicht mit -- und verschwinden nicht
    # spurlos, sondern stehen unter der Tabelle.
    stat = S.statistik_bauen(statistik_cache(), ['Bergring*'])
    gleich(stat['gesamt']['sessions'], 2, 'die Ausblendliste gilt auch hier')
    gleich(stat['gesamt']['meter'], 15000, 'und zwar fuer die Kilometer')
    gleich(sorted(stat['ausgeblendet']), ['Bergring Nord'],
           'was weggelassen wurde, wird benannt')

    stat = S.statistik_bauen(statistik_cache(), (), ['Suzuki*'])
    gleich(stat['gesamt']['meter'], 3000, 'der Fahrzeugfilter gilt auch hier')
    gleich(stat['versteckt'], 1, 'das andere Fahrzeug wird gezaehlt')

    # Ein Muster, das auf nichts passt, ist kein Filter, sondern ein
    # Vertipper. Dann lieber alles zeigen als eine leere Seite.
    stat = S.statistik_bauen(statistik_cache(), (), ['Gibtsnicht*'])
    gleich(stat['gesamt']['sessions'], 4,
           'ein Muster ohne Treffer blendet nicht alles aus')
    gleich(stat['versteckt'], 0, 'und meldet auch nichts als versteckt')


def export_bauen(runde2_kopfzeit='4.000', ausfall=False, luecke=False):
    """Ein Originalexport, wie racebox.pro ihn liefert -- mit Lap-Spalte.

    Elf Datenzeilen im Sekundentakt, damit sich jede Summe im Kopf
    nachrechnen laesst. Runde 1 faehrt vier Sekunden 72 km/h (80 m),
    Runde 2 vier Sekunden 108 km/h (120 m). Davor zwei Sekunden 36 km/h
    ausserhalb jeder Runde (20 m), danach eine Sekunde Schritttempo, die
    unter der Fahrschwelle liegt und deshalb keinen Meter beitraegt.
    """
    # Mit `luecke` setzt die Box mitten in Runde 1 zehn Sekunden aus. Die
    # Rundenzeit im Kopf zaehlt die Wanduhr durch und steht deshalb auf
    # 14 statt auf 4 Sekunden -- genau wie in einer echten Session, in der
    # eine halbstuendige Runde dreimal unterbrochen war.
    kopf = ('Format,RaceBox CSV\n'
            'Track,Talkurs\n'
            'Laps,2\n'
            'Lap 1, %s, sectors, 1.000,1.000,1.000,0,1.000\n'
            'Lap 2, %s, sectors, 1.000,1.000,1.000,0,1.000\n'
            '\n'
            'Record,Time,Latitude,Longitude,Altitude,Speed,GForceX,'
            'GForceZ,Lap,LeanAngle,GyroX,GyroY,GyroZ\n'
            % ('14.000' if luecke else '4.000', runde2_kopfzeit))
    # (Geschwindigkeit, Runde, Position). `ausfall` schiebt zwei Zeilen
    # dazwischen, wie sie ein Empfaenger schreibt, der seinen Fix verliert:
    # erst eine ohne Position, dann eine mit tadelloser Position und einem
    # Wert, den es nie gegeben hat.
    ort, weg = ('50.5', '13.6'), ('0.0', '0.0')
    tempo = [(36.0, 0, ort)] * 3
    if ausfall:
        tempo = [(36.0, 0, ort), (0.0, 0, weg), (900.0, 0, ort),
                 (36.0, 0, ort)]
    tempo += ([(72.0, 1, ort)] * 4 + [(108.0, 2, ort)] * 4
              + [(3.0, 0, ort)])
    zeilen = []
    for nr, (v, runde, (breite, laenge)) in enumerate(tempo):
        # Ab dem sechsten Messpunkt zehn Sekunden spaeter: die Luecke.
        sekunde = nr + (10 if luecke and nr >= 5 else 0)
        zeilen.append('%d,2026-08-25T13:00:%02d.000Z,%s,%s,262.4,'
                      '%.2f,0.1,1.0,%d,-4.4,0,0,0'
                      % (nr + 1, sekunde, breite, laenge, v, runde))
    return kopf + '\n'.join(zeilen) + '\n'


def test_runden_aus_export():
    """Die Lap-Spalte des Originalexports teilt die Kilometer auf."""
    ordner = tempfile.mkdtemp()
    try:
        pfad = os.path.join(ordner, 'x_bikemode.csv')
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(export_bauen())
        w = S.runden_aus_export(pfad)
        gleich(w['meter_runden'], 200,
               'Meter in gezaehlten Runden -- 80 aus Runde 1, 120 aus Runde 2')
        gleich(w['fahrzeit_runden'], 8.0, 'und acht Sekunden dafuer')
        gleich(w['meter_export'], 220,
               'alles Gefahrene laut Export: die 20 Meter ausserhalb kommen '
               'dazu')
        gleich(w['runden_probe'], 0.0,
               'die Dauer aus den Datenzeilen trifft die Rundenzeit im Kopf')

        # Die eine Sekunde Schritttempo liegt unter der Fahrschwelle und
        # zaehlt nirgends mit -- genau wie in der Telemetrie.
        pruefe(w['meter_export'] == w['meter_runden'] + 20,
               'ausserhalb der Runden bleiben genau die 20 Meter uebrig')

        # Sagt die Lap-Spalte etwas anderes als die Rundenzeiten, muss das
        # auffallen. Sonst teilte man Kilometer nach einer Spalte auf, von
        # der man nicht weiss, ob sie zu denselben Runden gehoert.
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(export_bauen('4.100'))
        gleich(S.runden_aus_export(pfad)['runden_probe'], 0.1,
               'eine Rundenzeit, die nicht zur Lap-Spalte passt, faellt auf')

        # Ein Export ohne Lap-Spalte ergibt keine Aufteilung -- und keine
        # erfundene Null.
        ohne = os.path.join(ordner, 'y_bikemode.csv')
        with open(ohne, 'w', encoding='utf-8') as f:
            f.write(export_bauen().replace(',Lap,', ',Runde,'))
        gleich(S.runden_aus_export(ohne), None, 'ohne Lap-Spalte nichts')
        gleich(S.runden_aus_export(os.path.join(ordner, 'gibtsnicht.csv')),
               None, 'und ohne Datei erst recht nichts')

        # Und derselbe Ausfall wie in der Telemetrie: ein Punkt ohne
        # Position, direkt danach ein Wert, den es nie gab.
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(export_bauen(ausfall=True))
        w = S.runden_aus_export(pfad)
        gleich(w['meter_runden'], 200,
               'die gezaehlten Runden bleiben unberuehrt -- der Ausfall lag '
               'davor')
        gleich(w['meter_export'], 200,
               'und die 900 km/h nach dem Ausfall steuern keinen Meter bei')

        # Setzt die Box mitten in einer Runde aus, zaehlt die Rundenzeit im
        # Kopf die Wanduhr durch. Die Probe muss dagegen halten und nicht
        # gegen die gefahrene Zeit -- sonst misst sie die eigenen
        # Auslassungen. An einer echten Session las sie so 419,865 s, waehrend
        # die uebrigen vierzehn Runden auf 0,032 s stimmten.
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(export_bauen(luecke=True))
        w = S.runden_aus_export(pfad)
        gleich(w['runden_probe'], 0.0,
               'eine Aufzeichnungsluecke ist keine Abweichung der Lap-Spalte')
        gleich(w['meter_runden'], 180,
               'die zehn Sekunden Luecke steuern keinen Meter bei -- was '
               'dort gefahren wurde, weiss niemand')
    finally:
        shutil.rmtree(ordner)


def test_punkte_ohne_fix():
    """Verliert der Empfaenger seinen Fix, ist das keine Messung.

    Nachgebaut aus einer echten Aufzeichnung: Die Box schreibt einen Punkt
    ohne Position (0/0), und zwei Messpunkte spaeter steht eine
    Geschwindigkeit da, die es nie gegeben hat -- mit tadelloser Position.
    Der Ausreisser selbst ist also nicht daran zu erkennen, wo er liegt,
    sondern daran, dass er unmittelbar auf einen Ausfall folgt.
    """
    def telemetrie(mit_ausfall):
        ort = (51.0, 13.0)
        weg = (0.0, 0.0)
        # Punkte im Halbsekundentakt, damit sich das Fenster von einer
        # Sekunde in wenigen Zeilen zeigen laesst.
        tempo = [(60.0, ort)] * 4 + [(60.0, ort)] * 5
        if mit_ausfall:
            tempo = ([(60.0, ort)] * 4 + [(0.0, weg)] + [(250.0, ort)]
                     + [(60.0, ort)] * 3)
        return {'dataColumns': ['iTOW', 'Latitude', 'Longitude', 'Speed'],
                'data': [[1000 + nr * 500, b, l, v]
                         for nr, (v, (b, l)) in enumerate(tempo)]}

    # Ohne Ausfall bleibt jede Zahl stehen -- auch eine hohe. Das ist der
    # Punkt: Die Regel urteilt nicht ueber die Geschwindigkeit.
    heil = S.kennzahlen_rechnen({}, telemetrie(False))
    gleich(heil['punkte_ohne_ort'], 0, 'eine heile Aufzeichnung verliert nichts')
    gleich(heil['v_max'], 60.0, 'und behaelt ihre Hoechstgeschwindigkeit')

    kaputt = S.kennzahlen_rechnen({}, telemetrie(True))
    gleich(kaputt['v_max'], 60.0,
           'der Ausreisser nach dem Ausfall zaehlt nicht -- obwohl seine '
           'Position tadellos ist')
    gleich(kaputt['punkte_ohne_ort'], 5,
           'verworfen wird der Punkt ohne Position und was in einer '
           'Sekunde darum liegt')
    gleich(kaputt['punkte'], 4, 'die uebrigen bleiben')

    # Dieselben Daten, aber mit 250 km/h *ohne* Ausfall: Dann steht die
    # Zahl da. Sonst waere es doch eine Obergrenze.
    ohne = {'dataColumns': ['iTOW', 'Latitude', 'Longitude', 'Speed'],
            'data': [[1000 + nr * 500, 51.0, 13.0, v]
                     for nr, v in enumerate([60.0, 60.0, 250.0, 60.0])]}
    gleich(S.kennzahlen_rechnen({}, ohne)['v_max'], 250.0,
           'ohne Ausfall bleibt auch eine hohe Zahl stehen -- geurteilt '
           'wird ueber die Messung, nicht ueber die Fahrt')

    gleich(S.ortsmitte([(51.0, 13.0)] * 5 + [(0.0, 0.0)]), (51.0, 13.0),
           'die Mitte ist der Median -- ein Ausfall zieht sie nicht fort')
    gleich(S.ortsmitte([]), None, 'ohne Punkte keine Mitte')
    pruefe(S.am_ort(51.001, 13.001, (51.0, 13.0)),
           'ein Punkt neben der Strecke gehoert dazu')
    pruefe(not S.am_ort(0.0, 0.0, (51.0, 13.0)),
           'der Nullpunkt des Gradnetzes nicht')
    pruefe(S.am_ort(0.0, 0.0, None), 'ohne Mitte wird nichts verworfen')


def test_runden_km_ergaenzen():
    """Einmal aus dem Export gerechnet, dann steht es im Cache."""
    exporte, cache = tempfile.mkdtemp(), tempfile.mkdtemp()
    try:
        eintraege = statistik_cache()
        for eintrag in eintraege:
            S.cache_schreiben(eintrag, cache)
        # Nur zur ersten Session liegt ein Export.
        with open(os.path.join(exporte, '%s_bikemode.csv' % ('a' * 24)),
                  'w', encoding='utf-8') as f:
            f.write(export_bauen())

        ergaenzt, ohne = still(S.runden_km_ergaenzen, eintraege, exporte, cache)
        gleich(ergaenzt, 1, 'eine Session hatte einen Export')
        gleich(ohne, 2, 'zwei weitere mit Telemetrie hatten keinen')
        gleich(eintraege[0]['kennzahlen']['meter_runden'], 200,
               'ihre Rundenkilometer stehen jetzt in den Kennzahlen')

        # Und zwar dauerhaft: Der naechste Lauf soll die drei Megabyte
        # nicht noch einmal lesen.
        frisch = S.cache_lesen(cache)['a' * 24]
        gleich(frisch['kennzahlen']['meter_runden'], 200,
               'und in der Cache-Datei auf der Platte')
        ergaenzt, _ = still(S.runden_km_ergaenzen, eintraege, exporte, cache)
        gleich(ergaenzt, 0, 'beim zweiten Mal ist nichts mehr zu tun')

        # Aendert sich die Rechnung, muessen die alten Zahlen weg. Das
        # geht ohne Netz -- die Exporte liegen ja da --, deshalb haben die
        # Rundenfelder eine eigene Version neben CACHE_VERSION.
        gleich(eintraege[0]['kennzahlen']['runden_version'],
               S.RUNDEN_VERSION, 'die Rundenfelder tragen ihre Version')
        eintraege[0]['kennzahlen']['runden_version'] = S.RUNDEN_VERSION - 1
        ergaenzt, _ = still(S.runden_km_ergaenzen, eintraege, exporte, cache)
        gleich(ergaenzt, 1,
               'eine aeltere Version wird aus dem Export neu gerechnet, '
               'ohne etwas zu holen')

        # Ein Export ohne Lap-Spalte wird ebenfalls vermerkt -- sonst
        # wuerde er bei jedem Lauf wieder gelesen.
        with open(os.path.join(exporte, '%s_bikemode.csv' % ('b' * 24)),
                  'w', encoding='utf-8') as f:
            f.write(export_bauen().replace(',Lap,', ',Runde,'))
        still(S.runden_km_ergaenzen, eintraege, exporte, cache)
        k = eintraege[1]['kennzahlen']
        pruefe('meter_runden' in k and k['meter_runden'] is None,
               'auch ein Export ohne Lap-Spalte wird vermerkt und nicht '
               'jedes Mal neu gelesen')

        # Ohne Telemetrie gibt es nichts zu ergaenzen: Die Session aus dem
        # alten CSV-Weg hat gar keine Kennzahlen.
        pruefe(eintraege[3]['kennzahlen'] is None,
               'eine Session ohne Kennzahlen bleibt unangetastet')
    finally:
        shutil.rmtree(exporte)
        shutil.rmtree(cache)


def test_rundenkilometer_in_der_statistik():
    """Was die Aufteilung in der Ansicht ausmacht."""
    eintraege = statistik_cache()
    # Zwei der drei Sessions mit Telemetrie bekommen eine Aufteilung.
    # Die groesste Probe steht bewusst *nicht* an letzter Stelle. Sonst
    # waeren "das Maximum" und "der zuletzt gesehene Wert" dieselbe Zahl,
    # und die Zusicherung darauf pruefte nichts.
    eintraege[0]['kennzahlen'].update(meter_runden=8000, fahrzeit_runden=250.0,
                                      meter_export=10000, runden_probe=0.04)
    eintraege[1]['kennzahlen'].update(meter_runden=4000, fahrzeit_runden=80.0,
                                      meter_export=5000, runden_probe=0.02)
    g = S.kennzahlen_summieren(eintraege)
    gleich(g['meter_runden'], 12000, 'Rundenkilometer werden summiert')
    gleich(g['fahrzeit_runden'], 330.0, 'die Fahrzeit darin ebenso')
    gleich(g['meter_export'], 15000, 'und die Gesamtsumme laut Export')
    gleich(g['runden_probe'], 0.04,
           'die Probe ist die groesste Abweichung, nicht die letzte')
    gleich(g['ohne_runden_km'], 1,
           'fuer die dritte Session mit Telemetrie fehlt die Aufteilung')

    gleich(S.anteil_text(12000, 18000), '67 %', 'der Anteil als Prozentzahl')
    gleich(S.anteil_text(None, 18000), '-', 'ohne Zahl kein Anteil')
    gleich(S.anteil_text(1, 0), '-', 'und ohne Ganzes auch nicht')

    stat = S.statistik_bauen(eintraege)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(stat)
    text = puffer.getvalue()
    zeilen = text.splitlines()

    def felder(anfang):
        return [z for z in zeilen if z.strip().startswith(anfang)][0].split()

    # Kilometer, Zeit, Schnitt und Anteil stehen in *einer* Zeile, und der
    # Schnitt geht aus den beiden Zahlen davor hervor: 12000 m in 330 s
    # sind 130.9 km/h. Genau darum ist die Zeit hier die gefahrene Zeit
    # innerhalb der Runden und nicht die Summe der Rundenzeiten -- sonst
    # ginge die Rechnung nicht auf.
    ohne = felder('ohne in/out')
    gleich(ohne[2:6], ['12', 'km', '0:05:30', 'h'],
           'Kilometer und Zeit ohne Aus- und Einfahrrunde in einer Zeile')
    gleich(ohne[6:9], ['Schnitt', '131', 'km/h'],
           'und der Schnitt daneben, aus genau diesen beiden gerechnet')
    gleich(ohne[9:12], ['67', '%', 'der'],
           'dazu der Anteil -- und woran er gemessen ist, steht dabei')
    zeile = [z for z in zeilen if 'Yamaha MT-07' in z][0]
    gleich(zeile.split()[-5], '12',
           'je Fahrzeug ebenso -- beide Sessions gehoeren demselben')
    gleich(zeile.split()[-4], '210',
           'und die Hoechstgeschwindigkeit daneben ohne Nachkomma')
    # Stimmen beide Proben, schweigen sie. Eine Zeile "alles in Ordnung"
    # bei jedem Lauf erzieht dazu, den ganzen Block zu ueberspringen -- und
    # dann uebersieht man das eine Mal, an dem etwas zu sagen waere.
    heil = statistik_cache()
    for eintrag in heil:
        if eintrag['kennzahlen']:
            k = eintrag['kennzahlen']
            k.update(meter_runden=k['meter'] // 2, fahrzeit_runden=100.0,
                     meter_export=k['meter'], runden_probe=0.04)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(S.statistik_bauen(heil))
    sauber = puffer.getvalue()
    pruefe('Gegenprobe' not in sauber and 'Rundengrenzen' not in sauber,
           'stimmen beide Proben, schweigen sie')
    pruefe('0.040 s' not in sauber,
           'und nennen erst recht keine Zahl, die niemanden angeht')

    # Eine einzelne auffaellige Session darf nicht wie ein Systemfehler
    # aussehen. An echten Daten war es eine von 259: Dort hatte RaceBox
    # eine Splitueberfahrt verpasst, und neun Sekunden lagen zwischen zwei
    # gezaehlten Runden -- an den Kilometern aenderte das nichts.
    eintraege[0]['kennzahlen']['runden_probe'] = 9.087
    g = S.kennzahlen_summieren(eintraege)
    gleich(g['sessions_probe'], 1, 'auffaellige Sessions werden gezaehlt')
    nahe(g['runden_probe'], 9.087, 'und die groesste Abweichung bleibt')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(S.statistik_bauen(eintraege))
    auffaellig = puffer.getvalue()
    pruefe('Rundengrenzen weichen in 1 von 2 Sessions ab' in auffaellig
           and '2026-08-01 Turn 1' in auffaellig,
           'schlaegt eine an, nennt sie die Seltenheit und die Session')
    pruefe('--verpasste-splits' not in auffaellig,
           'und verweist nicht auf eine Ansicht, die diesen Fall gar nicht '
           'zeigt -- dort geht die Sektorsumme ja auf')
    pruefe('muessten gleich sein' not in text,
           'dass eine Session gar keine Rundenaufteilung hat, ist keine '
           'Abweichung -- verglichen wird nur ueber die Sessions, die '
           'beide Zahlen haben')

    # Eine echte Abweichung: dieselbe Session, zwei verschiedene Summen.
    auseinander = statistik_cache()
    for eintrag in auseinander:
        if eintrag['kennzahlen']:
            k = eintrag['kennzahlen']
            k.update(meter_runden=k['meter'] // 2, fahrzeit_runden=100.0,
                     meter_export=k['meter'] - 1000, runden_probe=0.01)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(S.statistik_bauen(auseinander))
    pruefe('nennen 18.0 und 15.0 km' in puffer.getvalue(),
           'gehen die beiden Quellen auseinander, stehen beide Zahlen da')

    # Verglichen wird auf der angezeigten Genauigkeit. Beide Quellen runden
    # je Session auf ganze Meter; ueber zweihundert Sessions summiert sich
    # daraus ein Rest, der keiner ist. Frueher meldete das Werkzeug
    # "4686.5 km gegen 4686.5 km -- 0.0 km Unterschied".
    knapp = statistik_cache()
    for eintrag in knapp:
        if eintrag['kennzahlen']:
            k = eintrag['kennzahlen']
            # Groessenordnung eines echten Kontos: 4686 km. Darauf sind
            # ein paar Dutzend Meter nicht zu sehen.
            k.update(meter=1562000, meter_runden=780000, fahrzeit_runden=100.0,
                     meter_export=1562000 - 1, runden_probe=0.01)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(S.statistik_bauen(knapp))
    pruefe('muessten gleich sein' not in puffer.getvalue(),
           'ein Rest von wenigen Metern ist keine Abweichung -- wo dieselbe '
           'Zahl dasteht, ist dieselbe gemeint')
    pruefe('1 von 3 Session(s) ohne Rundenaufteilung' in text,
           'dass die Aufteilung nicht ueberall vorliegt, wird benannt')

    # Verworfene Messpunkte sind eine Aussage ueber die Aufzeichnung und
    # gehoeren in die Ausgabe, nicht in den Quelltext.
    eintraege[0]['kennzahlen']['punkte_ohne_ort'] = 234
    eintraege[1]['kennzahlen']['punkte_ohne_ort'] = 300
    g = S.kennzahlen_summieren(eintraege)
    gleich(g['punkte_ohne_ort'], 534, 'verworfene Punkte werden summiert')
    gleich(g['probe_session'], '2026-08-01 Turn 1',
           'die auffaelligste Session wird benannt -- eine Zahl ohne '
           'Fundstelle laesst sich nicht nachsehen')
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(S.statistik_bauen(eintraege))
    text = puffer.getvalue()
    zeilen = text.splitlines()
    gleich(felder('verworfen')[1:3], ['534', 'Messpunkte'],
           'die verworfenen Messpunkte bekommen eine eigene Zeile')
    pruefe('534 von' in text and 'Messpunkten verworfen' in text,
           'und in der Fusszeile, wie viele es von wie vielen waren')
    pruefe(max(len(z) for z in zeilen) < 88,
           'die Statistik bleibt schmal genug (%d Zeichen)'
           % max(len(z) for z in zeilen))


def test_statistik_aus_dem_json():
    """Die Kennzahlen kommen aus dem Cache, so wie er beim Holen entsteht."""
    eintraege = []
    for sid, (kopf, angaben, fzg, linien) in sorted(TESTSESSIONS.items()):
        name = FAHRZEUGE.get(FZG_KURZ.get(fzg, ''), None)
        eintraege.append(S.session_aus_json(
            sid, json_aus_kopf(sid, kopf, angaben, name, linien)))
    g = S.kennzahlen_summieren(eintraege)
    # Je Session 50 gefahrene und 51 gesamte Meter -- siehe test_kennzahlen.
    gleich(g['mit_kennzahlen'], len(TESTSESSIONS),
           'jeder Eintrag des JSON-Wegs bringt seine Kennzahlen mit')
    gleich(g['meter'], 50 * len(TESTSESSIONS),
           'die Meter werden summiert und nicht ueberschrieben')
    gleich(g['meter_gesamt'], 51 * len(TESTSESSIONS), 'die gesamten auch')
    gleich(g['v_max'], 108.0, 'die Hoechstgeschwindigkeit ist ein Maximum')


def test_statistik_anzeige():
    """Was in der Ansicht steht -- und was daneben erklaert wird."""
    stat = S.statistik_bauen(statistik_cache())
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(stat)
    text = puffer.getvalue()
    zeilen = text.splitlines()

    pruefe('Alles Gefahrene' in text, 'die Ansicht nennt sich beim Namen')
    pruefe('4 Turns an 3 Fahrtagen' in text and '6 Runden' in text,
           'Fahrtage, Turns und Runden stehen im Kopf')
    # Auf die ganze Zeile geprueft und nicht auf ein Teilstueck: `20` faende
    # sich sonst irgendwo, und die Zusicherung pruefte nichts.
    def felder(anfang):
        return [z for z in zeilen if z.strip().startswith(anfang)][0].split()

    gleich(felder('aufgezeichnet')[1:5], ['20', 'km', '0:11:10', 'h'],
           'aufgezeichnet: Kilometer und Zeit nebeneinander')
    gleich(felder('gefahren')[1:5], ['18', 'km', '0:08:20', 'h'],
           'gefahren ebenso, ohne Nachkomma')
    gleich(felder('gestanden')[1:3], ['0:02:50', 'h'],
           'und die Standzeit als eigene Zeile')

    gleich(felder('gefahren')[5:8], ['Schnitt', '130', 'km/h'],
           'der Schnitt steht in der Zeile, aus deren Zahlen er kommt -- '
           '129.6 wird zu 130')
    gleich(felder('Hoechstwerte')[1:8],
           ['210', 'km/h', 'Schraeglage', '50.0', 'links', '/', '45.0'],
           'die Hoechstwerte in einer Zeile; die Schraeglage behaelt ihre '
           'Nachkommastelle -- ein halbes Grad ist dort ein Unterschied')
    pruefe('langsamste' not in text,
           'die langsamste Geschwindigkeit steht nicht mehr da -- ein '
           'Minimum ueber alle Sessions findet immer den einen Halt und '
           'landet auf der Fahrschwelle')
    pruefe('% davon' not in text,
           'und kein `% davon`, das offenlaesst, wovon')

    pruefe('Je Fahrzeug' in text and 'Je Strecke' in text,
           'dieselbe Dreiteilung wie die Sektorauswertung')
    zeile = [z for z in zeilen if 'Yamaha MT-07' in z][0]
    gleich(zeile.split()[-4], '210',
           'in der Fahrzeugzeile steht die Hoechstgeschwindigkeit, ohne '
           'Nachkomma')
    # Von hinten gezaehlt, damit ein Fahrzeugname aus zwei Woertern die
    # Zaehlung nicht verschiebt: v-max, dann Schraeglage als drei Felder.
    felder = zeile.split()
    gleich(felder[-6:-4], ['15', '-'],
           'gefahren ohne Nachkommastelle -- und wo keine Aufteilung '
           'vorliegt, ein Strich statt einer erfundenen Null')
    pruefe('16.5' not in zeile,
           'die Kilometer *gesamt* stehen dort nicht mehr -- der Unterschied '
           'zu `gefahren` ist Boxengasse und steht vollstaendig oben')
    kopf = zeilen[zeilen.index(zeile) - 2]
    gleich(kopf.split(), ['km', 'km', 'km/h', 'Grad'],
           'ueber den Zahlen steht, in welchen Einheiten sie gemeint sind')
    pruefe([z for z in zeilen if 'Bergring Nord' in z],
           'und je Strecke eine eigene Zeile')

    # Die Fahrschwelle ist eine Entscheidung ueber die Zahlen -- sie
    # gehoert in die Ausgabe und nicht nur in den Quelltext.
    pruefe('5 km/h' in text, 'die Fahrschwelle steht dabei')
    # Jede Fussnote genau eine Zeile. Passt sie nicht, gehoert sie nach
    # TECHNIK.md -- wer die Statistik aufruft, will Zahlen sehen und keinen
    # Aufsatz darueber, wie sie zustande kommen.
    fuss = zeilen[[n for n, z in enumerate(zeilen)
                   if z.strip().startswith('Gefahren heisst')][0]:]
    pruefe(all(not z.startswith('      ') for z in fuss),
           'keine Fussnote hat eine Fortsetzungszeile')
    pruefe(max(len(z) for z in fuss) < 88,
           'und keine ist breiter als eine Kommandozeile (%d Zeichen)'
           % max(len(z) for z in fuss))
    pruefe('1 von 4 Session(s) ohne Telemetrie' in text,
           'die Session ohne Telemetrie wird benannt, nicht verschwiegen')
    pruefe('--neu' not in text,
           'zum Nachholen der Telemetrie braucht es kein --neu -- alte '
           'Eintraege holt schon der gewoehnliche Lauf')
    pruefe('ohne in/out' in text,
           'die Spalte heisst nach dem, was sie weglaesst')
    pruefe(max(len(z) for z in zeilen) < 88,
           'die Statistik bleibt schmal genug fuer eine Kommandozeile '
           '(%d Zeichen)' % max(len(z) for z in zeilen))

    # Ganz ohne Telemetrie darf keine erfundene Null dastehen.
    stat = S.statistik_bauen(sessions_bauen(), MUSTER)
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        S.zeige_statistik(stat)
    pruefe('Keine der' in puffer.getvalue()
           and 'Lauf mit Netz' in puffer.getvalue(),
           'ein Cache ohne Telemetrie sagt das und nennt den Ausweg')


def test_statistik_ueber_die_bedienung():
    """--statistik und das `s` im Menue fuehren zu derselben Ansicht."""
    ordner = tempfile.mkdtemp()
    echtes_input, echtes_stdin = builtins.input, sys.stdin
    try:
        for eintrag in statistik_cache():
            S.cache_schreiben(eintrag, ordner)
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.main(['--nur-cache', '--alle', '--statistik', '--cache', ordner])
        text = puffer.getvalue()
        pruefe('Alles Gefahrene' in text and 'Schnitt 130 km/h' in text,
               '--statistik zeigt die Ansicht ohne Umweg ueber das Menue')
        pruefe(ordner in text, 'und nennt, wo die Daten liegen')

        # Ein Schalter, der dasteht, soll wirken. `--strecke` neben
        # `--statistik` stillschweigend zu uebergehen waere die
        # schlechteste der Moeglichkeiten.
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.main(['--nur-cache', '--alle', '--statistik', '--cache', ordner,
                    '--strecke', 'Bergring'])
        text = puffer.getvalue()
        pruefe('Bergring Nord' in text and 'Talkurs' not in text,
               '--strecke schraenkt auch die Statistik ein')
        gefahren = [z for z in text.splitlines()
                    if z.strip().startswith('gefahren')][0].split()
        gleich(gefahren[1:3], ['3', 'km'],
               'und zwar auf die Kilometer dieser einen Strecke')

        # Im Menue steht der Weg dorthin in der Eingabezeile. Ein Ausgang,
        # den man raten muss, ist keiner.
        strecken, ausgeblendet, _ = S.strecken_bauen(statistik_cache())
        stat = S.statistik_bauen(statistik_cache())
        eingaben = iter(['s', '', ''])
        gefragt = []

        def antwort(frage=''):
            gefragt.append(frage)
            return next(eingaben)

        builtins.input = antwort
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            S.schleife(strecken, ausgeblendet, 3, ordner, 0, (), stat)
        text = puffer.getvalue()
        pruefe('s = Statistik' in gefragt[0],
               'die Eingabezeile nennt den Weg zur Statistik')
        pruefe('Alles Gefahrene' in text, 'und `s` fuehrt hin')
        pruefe(text.count('Strecke / Fahrzeug') >= 2,
               'danach steht die Uebersicht wieder da')
    finally:
        builtins.input, sys.stdin = echtes_input, echtes_stdin
        shutil.rmtree(ordner)


# --- Teil 2: der Weg uebers Netz ------------------------------------------
#
# Ein echter HTTP-Server auf 127.0.0.1 spielt racebox.pro. Geprueft wird
# damit, was wirklich rausgeht -- nicht, was eine Attrappe zu wissen
# vorgibt.

# Womit die beiden Uebungsplaetze in den Testdaten weggefiltert werden. Im
# Werkzeug steht das nicht mehr: Welche Strecke ein Uebungsplatz ist, weiss
# nur, wer dort gefahren ist.
MUSTER = ['Uebungsplatz*', 'Kartbahn*']

EMAIL, PASSWORT = 'du@example.com', 'geheim'
# Dorthin leitet die Anmeldung weiter. Am Windows-Rechner war das ein Host,
# der nicht antwortete; hier ist es ein Port, auf dem niemand lauscht.
TOTES_ZIEL = 'http://127.0.0.1:1/nach-dem-anmelden'
JUNK = 5 * 1024 * 1024          # so gross, dass ein Abbruch auffaellt


class Zustand:
    def __init__(self):
        self.export_felder = None
        self.exporte = 0
        self.jsons = 0
        self.kaputt = set()
        self.junk = JUNK          # wie viel Datenzeilen der Export anhaengt
        self.verdreht = set()     # Sessions, deren CSV absichtlich abweicht
        self.bei_null = set()     # Sessions, deren erste Runde bei Record 0 beginnt
        self.ohne_erste = set()   # Sessions, deren CSV die erste Runde nicht listet
        self.kennungen = set()
        self.pfade = []
        self.geschrieben = 0
        self.gesamt = 0


class Griff(BaseHTTPRequestHandler):
    zustand = None
    # Ohne das antwortet die Attrappe in HTTP/1.0 und macht nach jeder
    # Antwort zu -- dann liesse sich am nachgebauten Server nicht pruefen,
    # ob das Werkzeug seine Verbindung offen haelt.
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    # -- Hilfen ------------------------------------------------------------

    def _antwort(self, text, code=200, typ='text/html', kekse=None):
        roh = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(roh)))
        if kekse:
            self.send_header('Set-Cookie', kekse)
        self.end_headers()
        self.wfile.write(roh)

    def _angemeldet(self):
        return 'auth=1' in (self.headers.get('Cookie') or '')

    def _formular(self):
        return ('<html><body><form method="post">'
                '<input name="email"><input name="password" type="password">'
                '</form></body></html>')

    def _felder(self):
        laenge = int(self.headers.get('Content-Length') or 0)
        roh = self.rfile.read(laenge).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(roh).items()}

    def _pruefe_kennung(self):
        kennung = self.headers.get('User-Agent') or ''
        self.zustand.kennungen.add(kennung)
        if kennung.startswith('Python-urllib'):
            self._antwort('nein', 403)
            return False
        return True

    # -- die Seiten --------------------------------------------------------

    def do_GET(self):
        if not self._pruefe_kennung():
            return
        self.zustand.pfade.append(self.path)
        weg = urllib.parse.urlparse(self.path)
        if weg.path == '/webapp/sessions':
            return self._sessions(urllib.parse.parse_qs(weg.query))
        if weg.path.endswith('/json'):
            return self._json(weg.path.split('/')[3])
        if weg.path.startswith('/webapp/session/'):
            return self._sessionseite(weg.path.rsplit('/', 1)[-1])
        self._antwort('nichts', 404)

    def do_POST(self):
        if not self._pruefe_kennung():
            return
        self.zustand.pfade.append(self.path)
        felder = self._felder()
        if self.path == '/webapp/login':
            richtig = (felder.get('email') == EMAIL
                       and felder.get('password') == PASSWORT)
            if not richtig:
                # Falsches Passwort: 200 und wieder das Formular, genau wie
                # beim echten racebox.pro.
                return self._antwort(self._formular(), kekse='auth=0')
            # Richtiges Passwort: 302 auf einen Host, der nie antwortet --
            # Port 1 auf 127.0.0.1 nimmt niemand entgegen. Wer der
            # Weiterleitung folgt, bleibt hier haengen.
            self.send_response(302)
            self.send_header('Location', TOTES_ZIEL)
            self.send_header('Set-Cookie', 'auth=1; Path=/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path.endswith('/export/csv'):
            sid = self.path.split('/')[3]
            return self._export(sid, felder)
        self._antwort('nichts', 404)

    def _sessions(self, frage):
        if not self._angemeldet():
            return self._antwort(self._formular())
        vid = (frage.get('vid') or ['all'])[0]
        seite = int((frage.get('page') or ['1'])[0])
        def wann(eintrag):
            tag, monat, jahr = eintrag[1][1][1].split('/')
            return (jahr, monat, tag, eintrag[1][1][0])

        ids = [sid for sid, (_, _, fzg, _) in
               sorted(TESTSESSIONS.items(), key=wann, reverse=True)
               if vid == 'all' or FZG_KURZ.get(fzg or '') == vid]
        stueck = ids[(seite - 1) * 3:seite * 3]
        auswahl = ''.join('<option value="%s">%s</option>' % (k, v)
                          for k, v in sorted(FAHRZEUGE.items()))
        self._antwort(
            '<html><body>'
            '<select name="tid"><option value="all">All Tracks</option></select>'
            '<select name="vid"><option value="all">All Vehicles</option>%s'
            '</select>'
            '<select name="uid"><option value="own">Only Mine</option></select>'
            '%s</body></html>'
            % (auswahl,
               ''.join('<a href="/webapp/session/%s">x</a>' % s
                       for s in stueck)))

    def _json(self, sid):
        if sid not in TESTSESSIONS:
            return self._antwort('nichts', 404)
        kopf, angaben, fzg, ereignisse = TESTSESSIONS[sid]
        self.zustand.jsons += 1
        if sid in self.zustand.kaputt:
            # Gueltiges JSON in einer Form, mit der niemand gerechnet hat.
            return self._antwort(json.dumps({'session': ['kaputt']}),
                                 typ='application/json')
        self._antwort(json.dumps(json_aus_kopf(
            sid, kopf, angaben, FAHRZEUGE.get(FZG_KURZ.get(fzg or '', ''), ''),
            ereignisse, beginnt_bei_null=sid in self.zustand.bei_null)),
            typ='application/json')

    def _sessionseite(self, sid):
        if sid not in TESTSESSIONS:
            # Eine Seite ohne Ueberschrift -- so saehe ein Umbau der Seite
            # aus. Das Werkzeug darf daran nicht abbrechen.
            return self._antwort('<html><body>umgebaut</body></html>')
        _, (turn, datum, zeit), _, _ = TESTSESSIONS[sid]
        self._antwort('<html><body><h1>#%02d on %s %s</h1></body></html>'
                      % (turn, datum, zeit))

    def _export(self, sid, felder):
        """Der Export -- Kopfblock, dann sehr viele Datenzeilen.

        Mitgezaehlt wird, wie viel davon wirklich abgeflossen ist: Bricht
        der Client nach dem Kopf ab, bleibt der Rest liegen.
        """
        self.zustand.export_felder = felder
        self.zustand.exporte += 1
        kopf, _, _, _ = TESTSESSIONS[sid]
        if sid in self.zustand.ohne_erste:
            # So verhaelt sich das echte racebox.pro: Das CSV listet nur
            # die Runden, die es selbst als Runden zaehlt -- die Ein- und
            # die Ausfahrrunde stehen nur im JSON.
            kopf = '\n'.join(z for z in kopf.splitlines()
                              if not z.startswith('Lap 1,')) + '\n'
        if sid in self.zustand.verdreht:
            # Eine Sektorzeit anders als im JSON -- genau das soll der
            # Abgleich finden.
            kopf = kopf.replace('25.000', '25.400')
        rumpf = (kopf + '\nRecord,Time,Latitude,Longitude\n').encode()
        stueck = b'1,2026-08-10T17:16:45.240Z,51.069,13.711\n' * 200
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv')
        junk = self.zustand.junk
        self.send_header('Content-Length', str(len(rumpf) + junk))
        self.end_headers()
        self.zustand.gesamt = len(rumpf) + junk
        # Je Anfrage zaehlen, nicht ueber alle hinweg: Sonst bliebe die
        # zweite Antwort kuerzer als ihre angekuendigte Laenge, und der
        # Client wartete auf einen Rest, der nie kommt.
        geschrieben = 0
        try:
            self.wfile.write(rumpf)
            geschrieben = len(rumpf)
            while geschrieben < len(rumpf) + junk:
                self.wfile.write(stueck[:len(rumpf) + junk - geschrieben])
                geschrieben += len(stueck)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.zustand.geschrieben = geschrieben


def server_starten():
    zustand = Zustand()
    griff = type('Griff2', (Griff,), {'zustand': zustand})
    server = ThreadingHTTPServer(('127.0.0.1', 0), griff)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    basis = 'http://127.0.0.1:%d' % server.server_address[1]
    return server, basis, zustand


def test_netz():
    server, basis, zustand = server_starten()
    # Bleibt beim Umbiegen eine echte Adresse stehen, soll der Selbsttest
    # scheitern statt heimlich ins Internet zu gehen.
    echte_basis, S.BASIS = S.BASIS, 'http://127.0.0.1:1/verboten'
    try:
        pruefe(basis.startswith('http://127.0.0.1:'),
               'der Test spricht nur mit sich selbst')

        # -- Anmeldung -----------------------------------------------------
        rb = S.RaceBox(basis, zeitgrenze=5)
        begonnen = time.monotonic()
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            html = rb.anmelden(EMAIL, PASSWORT)
        gedauert = time.monotonic() - begonnen
        # Der Kern des Fehlers vom ersten echten Lauf: Wer der Weiterleitung
        # folgt, laeuft in einen Verbindungsversuch, der nie zurueckkommt.
        pruefe(gedauert < 3,
               'die Anmeldung folgt der Weiterleitung nicht und haengt '
               'deshalb nicht (%.1f s)' % gedauert)
        pruefe(TOTES_ZIEL in puffer.getvalue(),
               'wohin weitergeleitet wuerde, steht trotzdem in der Ausgabe')
        gleich(sorted(rb.kekse), ['auth'],
               'das Sitzungsmerkmal aus dem 302 liegt im Keksglas')
        pruefe('name="password"' not in html, 'nach der Anmeldung kein Formular')
        gleich(zustand.kennungen, {S.KENNUNG},
               'es geht genau eine Kennung raus, und nicht die von urllib')
        pruefe(not any(k.startswith('Python-urllib')
                       for k in zustand.kennungen),
               'keine Anfrage mit der gesperrten Vorgabekennung')

        rb2 = S.RaceBox(basis)
        fehler = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rb2.anmelden(EMAIL, 'falsch')
        except S.AnmeldungFehlgeschlagen as e:
            fehler = str(e)
        pruefe(fehler is not None,
               'falsches Passwort wird erkannt, obwohl der Server 200 sagt')
        pruefe('--zugang' in (fehler or ''), 'die Meldung sagt, was zu tun ist')

        # -- Sessionliste --------------------------------------------------
        weg = [p for p in zustand.pfade if p.startswith('/webapp/sessions')][0]
        pruefe('type=track' in weg and 'uid=own' in weg and 'tid=all' in weg,
               'die Liste wird mit den Filtern der Oberflaeche geholt')
        pruefe('page=' not in weg, 'Seite 1 kommt ohne page, wie beim Klick')

        ids = rb.alle_ids()
        gleich(len(ids), len(TESTSESSIONS), 'alle Sessions ueber drei Seiten')
        gleich(len(set(ids)), len(ids), 'keine doppelten IDs')
        pruefe('/webapp/sessions?type=track&tid=all&vid=all&uid=own&page=2'
               in zustand.pfade, 'Seite 2 wird geholt')

        # -- Fahrzeuge -----------------------------------------------------
        gleich(rb.fahrzeuge(html), FAHRZEUGE,
               'Fahrzeuge aus dem Auswahlfeld')
        karte = S.fahrzeugkarte(rb, list(TESTSESSIONS), FAHRZEUGE, melden=False)
        gleich(karte['a' * 24], 'Yamaha MT-07', 'Session dem Fahrzeug zugeordnet')
        gleich(karte['c' * 24], 'Suzuki SV650', 'und die andere dem anderen')
        pruefe('0' * 24 not in karte,
               'eine Session ohne Fahrzeug bleibt ohne Fahrzeug')

        # -- Export --------------------------------------------------------
        vorher = zustand.exporte
        kopf = rb.kopfblock('a' * 24)
        gleich(zustand.exporte, vorher + 1, 'genau ein Export')
        gleich(kopf.rstrip('\n'), KOPF_S1.rstrip('\n'),
               'der Kopfblock kommt vollstaendig an')
        pruefe('Record,' not in kopf, 'die Spaltenzeile gehoert nicht dazu')
        pruefe(zustand.geschrieben < zustand.gesamt,
               'nach dem Kopf wird abgebrochen, statt 5 MB zu laden')

        # Die acht Felder stehen hier ausgeschrieben. Eine Schleife ueber
        # S.EXPORT_FELDER verglieche die Konstante mit sich selbst und
        # bliebe gruen, egal was drinsteht.
        f = zustand.export_felder
        gleich(f.get('csvFormat'), 'custom', 'Feld csvFormat')
        gleich(f.get('timeFormat'), 'utc', 'Feld timeFormat')
        gleich(f.get('speedFormat'), 'kph', 'Feld speedFormat')
        gleich(f.get('altitudeFormat'), 'm', 'Feld altitudeFormat')
        gleich(f.get('newLineFormat'), 'cr', 'Feld newLineFormat')
        gleich(f.get('extendedHeader'), '1', 'Feld extendedHeader')
        gleich(f.get('addLapSectorEventsInHeader'), '1',
               'Feld addLapSectorEventsInHeader -- ohne das keine Sektoren')
        gleich(f.get('includeEntryExit'), '1',
               'Feld includeEntryExit -- ohne das keine Teilrunden')
        pruefe('bikeMode' not in f,
               'Bike Mode wird nicht geschickt, er aendert die Sektoren nicht')
        gleich(len(f), 8, 'und kein neuntes Feld')

        # -- Sessionseite --------------------------------------------------
        seite = rb.sessionseite('b' * 24)
        gleich(seite, {'turn': 2, 'datum': '2026-08-10', 'startzeit': '20:05'},
               'Turn, Tag und Ortszeit von der Sessionseite')
        gleich(rb.sessionseite('9' * 24), None,
               'eine Seite ohne Ueberschrift bricht nicht ab')

        # -- Abgleich ------------------------------------------------------
        ordner = tempfile.mkdtemp()
        try:
            rb3 = S.RaceBox(basis, zeitgrenze=5)
            erste_ausgabe = io.StringIO()
            exporte_vorher = zustand.exporte
            with contextlib.redirect_stdout(erste_ausgabe):
                geholt = S.abgleichen(rb3, EMAIL, PASSWORT, ordner,
                                      gleichzeitig=3)
            gleich(geholt, len(TESTSESSIONS), 'beim ersten Lauf alles holen')
            pruefe('Erstlauf' in erste_ausgabe.getvalue(),
                   'der Erstlauf sagt an, dass er laenger dauert')
            pruefe('Jeder weitere Lauf' in erste_ausgabe.getvalue(),
                   'und dass es danach schneller geht')

            gleich(zustand.jsons, len(TESTSESSIONS),
                   'je Session genau eine JSON-Anfrage')
            gleich(zustand.exporte, exporte_vorher + len(TESTSESSIONS),
                   'und je Session ein Export -- daher kommen die Runden')
            pruefe(len(rb3.verbindungen) <= 4,
                   'hoechstens eine Verbindung je Arbeiter, dazu die des '
                   'Hauptfadens fuer Anmeldung und Liste')
            gleich(len(rb3.verbindungen), 4,
                   'drei Arbeiter und der Hauptfaden -- es lief parallel')
            aufbauten = rb3.zeiten()['aufbauten']
            pruefe(rb3.anfragen > 3 * aufbauten,
                   'jede Verbindung traegt mehrere Anfragen (%d Anfragen '
                   'ueber %d Aufbauten), statt fuer jede neu aufgebaut zu '
                   'werden' % (rb3.anfragen, aufbauten))
            pruefe('Aufbau' in erste_ausgabe.getvalue()
                   and 'Warten auf Antwort' in erste_ausgabe.getvalue(),
                   'die Ausgabe schluesselt auf, wo die Zeit hingeht')

            cache = S.cache_lesen(ordner)
            gleich(len(cache), len(TESTSESSIONS), 'alles im Cache')
            eintrag = cache['a' * 24]
            gleich(eintrag['strecke'], 'Talkurs', 'Strecke im Cache')
            gleich(eintrag['fahrzeug'], 'Yamaha MT-07', 'Fahrzeug im Cache')
            gleich(eintrag['datum'], '2026-08-10', 'Ortszeit-Datum im Cache')
            gleich(eintrag['startzeit'], '19:16',
                   'Startzeit aus der Session, nicht die UTC-Zeit')
            gleich(len(eintrag['runden']), 3, 'Runden im Cache')
            pruefe(eintrag['kennzahlen']['meter'] > 0,
                   'die Kennzahlen aus der Telemetrie liegen mit im Cache')

            anfragen = rb3.anfragen
            rb4 = S.RaceBox(basis, zeitgrenze=5)
            zweite_ausgabe = io.StringIO()
            with contextlib.redirect_stdout(zweite_ausgabe):
                geholt = S.abgleichen(rb4, EMAIL, PASSWORT, ordner)
            gleich(geholt, 0, 'beim zweiten Lauf nichts holen')
            pruefe('Erstlauf' not in zweite_ausgabe.getvalue(),
                   'und dann auch keine Ansage mehr dazu')
            pruefe(rb4.anfragen < anfragen / 2,
                   'der zweite Lauf kostet nur einen Bruchteil der Anfragen')

            # -- nur ein Fahrzeug ------------------------------------------
            rb5 = S.RaceBox(basis, zeitgrenze=5)
            with contextlib.redirect_stdout(io.StringIO()):
                geholt = S.abgleichen(rb5, EMAIL, PASSWORT, ordner, neu=True,
                                      gleichzeitig=3, fahrzeug='Yamaha*')
            gleich(geholt, 5, 'nur die Sessions dieses Fahrzeugs')
            fehler = None
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    S.abgleichen(S.RaceBox(basis, zeitgrenze=5), EMAIL,
                                 PASSWORT, ordner, neu=True,
                                 fahrzeug='Gibt Es Nicht*')
            except SystemExit as e:
                fehler = str(e)
            pruefe(fehler is not None and 'Vorhanden' in fehler,
                   'ein Fahrzeug, das es nicht gibt, wird benannt -- mit '
                   'der Liste derer, die es gibt')

            # -- nur ab einem Tag ------------------------------------------
            rb6 = S.RaceBox(basis, zeitgrenze=5)
            seit_ausgabe = io.StringIO()
            with contextlib.redirect_stdout(seit_ausgabe):
                geholt = S.abgleichen(rb6, EMAIL, PASSWORT, ordner, neu=True,
                                      gleichzeitig=2, seit='2026-08-01')
            # Der Tag steht erst *nach* dem Holen fest -- abgebrochen wird
            # deshalb am Blockende, nicht mitten im Block. Ueberschossen
            # wird hoechstens ein Block.
            gleich(geholt, 6, 'ab dem 1. August plus dem angefangenen Block')
            pruefe('Rest uebersprungen' in seit_ausgabe.getvalue(),
                   'der Rest wird gar nicht erst geholt')
            gleich(S.cache_lesen(ordner)['d' * 24].get('quelle'), 'json+csv',
                   'die Runden kommen aus dem Export, alles uebrige aus '
                   'dem JSON')

            # -- Abgleich mit dem Originalexport ---------------------------
            archiv = tempfile.mkdtemp()
            try:
                zustand.junk = 4096       # kein 5-MB-Rumpf noetig
                zustand.verdreht = {'a' * 24}
                # Bei dieser Session wird beim Holen ein Randsektor
                # verworfen. Verglichen werden muss trotzdem gegen die
                # unbereinigte Liste -- sonst meldet der Abgleich, was das
                # Werkzeug selbst getan hat.
                zustand.bei_null = {'b' * 24}
                # Eine Session mit zwei Runden -- bei nur einer bliebe der
                # Versatz ungeprueft, weil das CSV dann gar keine Runde
                # mehr listet.
                zustand.ohne_erste = {'e' * 24}
                rb9 = S.RaceBox(basis, zeitgrenze=5)
                abgleich_ausgabe = io.StringIO()
                with contextlib.redirect_stdout(abgleich_ausgabe):
                    S.abgleichen(rb9, EMAIL, PASSWORT, ordner, neu=True,
                                 gleichzeitig=3, csv_ordner=archiv)
                text = abgleich_ausgabe.getvalue()
                abgelegt = sorted(os.listdir(archiv))
                gleich(len(abgelegt), len(TESTSESSIONS),
                       'je Session liegt ein Originalexport im Archiv')
                pruefe(abgelegt[0].endswith('_bikemode.csv'),
                       'unter sprechendem Namen')
                inhalt = open(os.path.join(archiv, abgelegt[0]),
                              encoding='utf-8').read()
                pruefe(inhalt.startswith('Format,RaceBox CSV'),
                       'und vollstaendig, nicht nur der Kopf')
                pruefe('Record,' in inhalt,
                       'mit den Datenzeilen -- anders als beim Kopfabgleich')
                pruefe('Runden aus dem CSV-Export' in text,
                       'der Lauf sagt, woher die Runden kommen')
                pruefe('Sektor 2' in text and '25.40' in text,
                       'und benennt die abweichende Sektorzeit')
                gleich(text.count('Runde 2 Sektor 2'), 1,
                       'genau die eine Abweichung, nicht mehr')
                pruefe('Abweichung(en) zum JSON' in text,
                       'und wie viele Abweichungen zum JSON blieben')
                pruefe('nur das JSON' in text
                       and 'Ein- und Ausfahrrunden' in text,
                       'Runden, die nur im JSON stehen, gelten nicht als '
                       'Abweichung -- zugeordnet wird ueber die Rundenzeit '
                       'und nicht ueber die Position')
                gleich(text.count('steht nur im CSV'), 0,
                       'und andersherum fehlt keine')
                pruefe('Sektorzeiten im CSV' not in text,
                       'ein beim Holen verworfener Randsektor gilt nicht '
                       'als Abweichung -- verglichen wird gegen die '
                       'unbereinigte Liste')
                pruefe(S.cache_lesen(ordner)['a' * 24].get('abgleich') is None,
                       'die Befunde landen nicht im Cache')
                gleich(S.cache_lesen(ordner)['c' * 24]['quelle'], 'json+csv',
                       'die Runden stammen aus dem Export')
            finally:
                zustand.junk, zustand.verdreht = JUNK, set()
                zustand.bei_null = set()
                zustand.ohne_erste = set()
                shutil.rmtree(archiv)

            # -- eine Session, die sich nicht lesen laesst -----------------
            zustand.kaputt = {'a' * 24}
            rb8 = S.RaceBox(basis, zeitgrenze=5)
            kaputt_ausgabe = io.StringIO()
            with contextlib.redirect_stdout(kaputt_ausgabe):
                geholt = S.abgleichen(rb8, EMAIL, PASSWORT, ordner, neu=True,
                                      gleichzeitig=3)
            zustand.kaputt = set()
            gleich(geholt, len(TESTSESSIONS) - 1,
                   'eine unlesbare Session kostet sich selbst, nicht den Lauf')
            pruefe('uebersprungen' in kaputt_ausgabe.getvalue(),
                   'sie wird dabei benannt')
            pruefe('--diagnose' in kaputt_ausgabe.getvalue(),
                   'mit dem Hinweis, wie man ihr auf den Grund geht')

            # -- der alte Weg als Rueckfallebene ---------------------------
            exporte_vorher = zustand.exporte
            rb7 = S.RaceBox(basis, zeitgrenze=5)
            with contextlib.redirect_stdout(io.StringIO()):
                geholt = S.abgleichen(rb7, EMAIL, PASSWORT, ordner, neu=True,
                                      gleichzeitig=2, csv_weg=True)
            gleich(geholt, len(TESTSESSIONS), '--csv holt ueber den CSV-Weg')
            gleich(zustand.exporte, exporte_vorher + len(TESTSESSIONS),
                   'und benutzt dabei wieder den Export')
            gleich(S.cache_lesen(ordner)['a' * 24]['fahrzeug'],
                   'Yamaha MT-07',
                   'auch dort steht das Fahrzeug, ueber die Filterlisten')

            # -- der ganze Lauf --------------------------------------------
            os.environ['RACEBOX_EMAIL'] = EMAIL
            os.environ['RACEBOX_PASSWORT'] = PASSWORT
            # Die Ausblendliste liegt neben dem Skript -- im Test also im
            # Wegwerfordner und nicht im Arbeitsverzeichnis.
            echter_ordner, S.ORDNER = S.ORDNER, ordner
            try:
                puffer = io.StringIO()
                with contextlib.redirect_stdout(puffer):
                    S.main(['--basis', basis, '--cache', ordner])
                text = puffer.getvalue()
                pruefe('Talkurs' in text and '1:26.00' in text,
                       'der ganze Lauf endet in der Uebersicht')
                pruefe('UEBUNGSPLATZ NORD' in text,
                       'ohne eigene Liste wird nichts ausgeblendet')
                pruefe(ordner in text, 'die Uebersicht nennt den Datenordner')

                puffer = io.StringIO()
                with contextlib.redirect_stdout(puffer):
                    S.main(['--nur-cache', '--cache', ordner,
                            '--ausblenden', 'Uebungsplatz*',
                            '--ausblenden', 'Kartbahn*'])
                text = puffer.getvalue()
                pruefe('UEBUNGSPLATZ NORD' not in text.split('Ausgeblendet')[0],
                       'nach --ausblenden sind die Uebungsplaetze aus der '
                       'Tabelle')
                gleich(S.ausblenden_lesen(ordner),
                       ['Uebungsplatz*', 'Kartbahn*'],
                       'und stehen dauerhaft in der Datei')

                puffer = io.StringIO()
                with contextlib.redirect_stdout(puffer):
                    S.main(['--nur-cache', '--cache', ordner, '--alle'])
                pruefe('UEBUNGSPLATZ NORD' in puffer.getvalue(),
                       '--alle zeigt sie fuer einen Lauf wieder')
            finally:
                S.ORDNER = echter_ordner

            puffer = io.StringIO()
            with contextlib.redirect_stdout(puffer):
                S.main(['--nur-cache', '--cache', ordner, '--strecke', 'Talkurs'])
            text = puffer.getvalue()
            pruefe('Woraus die Golden Lap besteht' in text,
                   '--strecke zeigt das Detail ohne Menue')
            pruefe('Anmelden' not in text, '--nur-cache geht nicht ins Netz')
        finally:
            shutil.rmtree(ordner)
            os.environ.pop('RACEBOX_EMAIL', None)
            os.environ.pop('RACEBOX_PASSWORT', None)
    finally:
        S.BASIS = echte_basis
        server.shutdown()


def test_speicherort():
    """Die Daten liegen neben dem Skript, nicht im Benutzerprofil.

    Ein versteckter Ordner unter `~` ist der uebliche Ort und trotzdem der
    falsche: Wer das Werkzeug in einen Ordner legt, sucht seine Daten dort.
    """
    hier = os.path.dirname(os.path.abspath(S.__file__))
    gleich(S.ORDNER, hier, 'der Datenordner ist der Skriptordner')
    gleich(S.ZUGANG, os.path.join(hier, 'zugang'), 'zugang liegt daneben')
    gleich(S.CACHE, os.path.join(hier, 'cache'), 'cache liegt daneben')
    pruefe('.racebox-sektoren' not in S.ORDNER,
           'kein versteckter Ordner im Benutzerprofil mehr')

    os.environ['RB_GOLDEN_LAP_DIR'] = os.path.join('/ein', 'anderer')
    try:
        gleich(S.daten_ordner(), os.path.join('/ein', 'anderer'),
               'die Umgebungsvariable hat Vorrang')
    finally:
        del os.environ['RB_GOLDEN_LAP_DIR']
    gleich(S.daten_ordner(), hier, 'ohne sie wieder neben dem Skript')

    # Ein Ort, an dem sich nichts anlegen laesst, ergibt eine Meldung und
    # keinen Traceback.
    handle, datei = tempfile.mkstemp()
    os.close(handle)
    try:
        fehler = None
        try:
            S.ordner_anlegen(os.path.join(datei, 'geht-nicht'))
        except SystemExit as e:
            fehler = str(e)
        pruefe(fehler is not None, 'ein unmoeglicher Ort meldet sich')
        pruefe('RB_GOLDEN_LAP_DIR' in (fehler or ''),
               'und sagt, womit man ihn verlegt')
    finally:
        os.unlink(datei)


def test_verbindungsaufbau():
    """Eine Adresse, die nicht antwortet, darf den Lauf nicht anhalten.

    Genau daran hing der erste echte Lauf: Loest ein Rechner eine
    IPv6-Adresse auf, die sein Netz nicht erreicht, wartet er dort die
    volle Zeitgrenze ab -- und `urllib` baut fuer *jede* Anfrage eine neue
    Verbindung auf. Hier bekommt jede Adresse nur eine kurze Frist, danach
    ist die naechste dran.
    """
    # Statt auf eine echte tote Adresse zu warten -- das dauerte im Test
    # so lange wie in echt -- wird die Steckdose selbst nachgebaut. So
    # steht in der Zusicherung, welche Frist gesetzt *wird*, und nicht,
    # wie lange der Testrechner zufaellig braucht.
    gesetzt, versuche = [], []

    class Attrappe:
        def __init__(self, *args):
            pass

        def settimeout(self, wert):
            gesetzt.append(wert)

        def connect(self, adresse):
            versuche.append(adresse)
            if adresse[0] == '192.0.2.1':
                raise OSError('nicht erreichbar')

        def close(self):
            pass

    echte_aufloesung, echte_dose = socket.getaddrinfo, socket.socket
    grenze = S.VERBINDUNGSGRENZE
    try:
        S.VERBINDUNGSGRENZE = 5

        def zwei_adressen(host, gefragt, *rest, **auch):
            # 192.0.2.0/24 ist fuer Beispiele reserviert und wird nicht
            # geroutet -- so sieht eine IPv6-Adresse aus, die das Netz des
            # Rechners nicht erreicht.
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
                     ('192.0.2.1', 9)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, '',
                     ('127.0.0.1', 443))]
        socket.getaddrinfo = zwei_adressen
        socket.socket = Attrappe
        dose = S.steckdose('irgendwo.invalid', 443, 30)
        pruefe(dose is not None, 'die zweite Adresse traegt')
        gleich([a[0] for a in versuche], ['192.0.2.1', '127.0.0.1'],
               'erst die eine Adresse, dann die naechste')
        gleich(gesetzt[0], 5,
               'die tote Adresse bekommt die kurze Frist, nicht die volle '
               'Zeitgrenze von 30 Sekunden')
        gleich(gesetzt[-1], 30,
               'die tragende Verbindung bekommt danach die volle Zeitgrenze')
        pruefe(S.VERBINDUNGSGRENZE <= 10,
               'die Frist bleibt kurz genug, um nicht selbst zur Bremse '
               'zu werden')
    finally:
        socket.getaddrinfo = echte_aufloesung
        socket.socket = echte_dose
        S.VERBINDUNGSGRENZE = grenze


def test_zeitgrenze():
    """Eine Gegenstelle, die nie antwortet, darf nicht ewig haengen.

    Genau das ist beim ersten Lauf am echten Konto passiert: Die Meldung
    "Anmelden bei ..." stand da, und dahinter wartete urllib ohne
    Zeitgrenze -- ununterscheidbar von einem Absturz.
    """
    lauscher = socket.socket()
    lauscher.bind(('127.0.0.1', 0))
    lauscher.listen(1)
    basis = 'http://127.0.0.1:%d' % lauscher.getsockname()[1]
    try:
        rb = S.RaceBox(basis, zeitgrenze=1)
        fehler = None
        try:
            rb.liste()
        except SystemExit as e:
            fehler = str(e)
        pruefe(fehler is not None, 'eine stumme Gegenstelle laeuft in die '
                                   'Zeitgrenze, statt ewig zu warten')
        pruefe('1 Sekunden' in (fehler or ''),
               'die Meldung nennt die Zeitgrenze')
        pruefe('Proxy' in (fehler or ''),
               'und den haeufigsten Grund dafuer')
        gleich(S.RaceBox(basis).zeitgrenze, S.ZEITGRENZE,
               'ohne Angabe gilt die Vorgabe')
    finally:
        lauscher.close()


def test_cache():
    ordner = tempfile.mkdtemp()
    try:
        eintrag = sessions_bauen()[0]
        pfad = S.cache_schreiben(eintrag, ordner)
        pruefe(os.path.exists(pfad), 'Cache-Datei liegt da')
        pruefe(not os.path.exists(pfad + '.neu'),
               'die vorlaeufige Datei ist weg')
        gleich(S.cache_lesen(ordner)[eintrag['id']]['strecke'],
               eintrag['strecke'], 'gelesen wie geschrieben')
        with open(os.path.join(ordner, 'kaputt.json'), 'w') as f:
            f.write('{kein json')
        gleich(len(S.cache_lesen(ordner)), 1,
               'eine kaputte Datei wird uebergangen, nicht geworfen')
        gleich(S.cache_lesen(os.path.join(ordner, 'gibtsnicht')), {},
               'ein fehlender Ordner ist leer, kein Fehler')
    finally:
        shutil.rmtree(ordner)


def test_splitansicht_ueber_die_bedienung():
    html = ('<a href="/webapp/session/%s">x</a>'
            '<a href="/webapp/session/%s">y</a>'
            '<a href="/webapp/session/%s">z</a>' % ('a' * 24, 'a' * 24, 'b' * 24))
    gleich(S.ID_MUSTER.findall(html), ['a' * 24, 'a' * 24, 'b' * 24],
           'IDs aus den Links')
    t = S.KOPF_MUSTER.search('<h1>#04 on 10/08/2026 19:42</h1>')
    gleich(t.groups(), ('04', '10', '08', '2026', '19', '42'),
           'Turnnummer, Tag und Ortszeit aus der Ueberschrift')
    gleich(S.fahrzeuge_lesen('<select name="tid"><option value="x">y</option>'
                             '</select>'), {},
           'ohne Fahrzeugfeld kommt nichts zurueck, statt zu raten')
    gleich(S.fahrzeuge_lesen(
        '<select id="filter-vehicle"><option value="all">All Vehicles</option>'
        '<option value="1">Yamaha MT-07</option></select>'),
        {'1': 'Yamaha MT-07'},
        'auch ohne name=vid ueber "All Vehicles" erkannt')


def main():
    for name, f in sorted(globals().items()):
        if name.startswith('test_') and callable(f):
            f()
    print()
    if ROT:
        print('%d gruen, %d ROT' % (GRUEN, len(ROT)))
        for r in ROT:
            print('  %s' % r)
        return 1
    print('%d Zusicherungen gruen' % GRUEN)
    return 0


if __name__ == '__main__':
    sys.exit(main())
