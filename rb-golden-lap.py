#!/usr/bin/env python3
"""Racebox Golden Lap -- Sektorzeiten ueber Sessions und Fahrtage hinweg.

Die RaceBox-App und racebox.pro zeigen Sektorzeiten immer nur innerhalb
einer Session. Die schnellste Runde, die aus den bereits gefahrenen
Sektoren zusammensetzbar waere, sieht man dort nicht -- und ueber Turns
oder Fahrtage hinweg schon gar nicht. Genau das rechnet dieses Werkzeug:
je Strecke und Fahrzeug die beste theoretische Runde und woraus sie
besteht.

Nur Standardbibliothek. Laeuft unter Windows, Linux und macOS.

    python3 rb-golden-lap.py           Uebersicht, dann Strecke waehlen
    python3 rb-golden-lap.py --help    alle Schalter
"""

import argparse
import fnmatch
import gzip
import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# --- Die Schnittstelle von racebox.pro ------------------------------------
#
# Nachgemessen, nicht zugesichert -- sie kann sich jederzeit aendern. Was
# dahintersteckt und welche Fallen darin liegen, steht in README.md unter
# "Die Schnittstelle von racebox.pro".

BASIS = 'https://www.racebox.pro'
LOGIN = '/webapp/login'
LISTE = '/webapp/sessions'
SEITE = '/webapp/session/%s'
EXPORT = '/webapp/session/%s/export/csv'
# Die Datenquelle der Webseite selbst: Auf jeder Sessionseite steht
# `data-fetch-url="/webapp/session/%s/json"`. Damit reicht eine Anfrage je
# Session statt zweier, und das Fahrzeug kommt gleich mit.
JSON = '/webapp/session/%s/json'

# Eintraege aelterer Fassungen werden neu geholt. Fassung 2 kam aus dem
# JSON-Weg: Sie kennt Fahrzeug, Konfiguration und Kennzahlen, und ihre
# Sektoren sind nach Splitpunkten aufgebaut statt nach CSV-Feldern.
# Fassung 3 haelt zusaetzlich fest, welcher Sektor gerechnet statt gemessen
# ist und ob die Summe der Runde aufging -- beides laesst sich aus einem
# fertigen Eintrag nicht mehr zurueckgewinnen, weil er schon bereinigt ist.
# Fassung 4 nimmt die Runden aus dem CSV-Export statt aus dem JSON.
CACHE_VERSION = 5

# racebox.pro sperrt `Python-urllib/...` mit 403 -- und nur das. Deshalb
# steht hier, was das Werkzeug wirklich ist, und kein vorgetaeuschter Safari.
KENNUNG = 'racebox-golden-lap/1.0 (+https://github.com/T28PJ/racebox-golden-lap)'

# Ohne Zeitgrenze wartet urllib unbegrenzt. Eine stehende Verbindung -- ein
# Proxy, der nicht antwortet, ein halb offener TLS-Handschlag -- sieht dann
# aus wie ein Absturz, und man weiss nicht einmal, an welcher Stelle.
ZEITGRENZE = 30

# Die Exporteinstellungen. Drei davon sind fuer uns wesentlich:
# `extendedHeader` und `addLapSectorEventsInHeader` bringen den Kopfblock mit
# den Rundenzeiten erst hervor, `includeEntryExit` die Ein- und Ausfahrrunden
# -- deren Teilsektoren sind echte Messungen zwischen zwei Splitpunkten und
# zaehlen mit. Der Rest betrifft nur die Datenzeilen, die wir nie lesen.
#
# `newLineFormat=cr` ist die Falle aus der Oberflaeche: Der Wert heisst dort
# "Linux/Mac" und liefert LF, nicht CR.
EXPORT_FELDER = {
    'csvFormat': 'custom',
    'timeFormat': 'utc',
    'speedFormat': 'kph',
    'altitudeFormat': 'm',
    'newLineFormat': 'cr',
    'extendedHeader': '1',
    'addLapSectorEventsInHeader': '1',
    'includeEntryExit': '1',
}

# Eine Session-ID ist eine 24-stellige Hexzahl. Das Muster erkennt sie im
# Link der Sessionliste genauso wie in einer eingefuegten URL.
ID_MUSTER = re.compile(r'/webapp/session/([0-9a-f]{24})')

# Die Ueberschrift einer Sessionseite: "#04 on 10/08/2026 19:42" -- Tag und
# Startzeit in Ortszeit. Im CSV-Kopf steht stattdessen UTC im
# 12-Stunden-Format ("Time,05/16/45"), das waere die falsche Uhrzeit.
KOPF_MUSTER = re.compile(
    r'#(\d+)\s+on\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})')

def daten_ordner():
    """Wo Zugang und Cache liegen: neben dem Skript.

    Ein versteckter Ordner im Benutzerprofil ist der uebliche Ort und
    trotzdem der falsche: Wer das Werkzeug in einen Ordner legt, sucht
    seine Daten auch dort und nicht unter einem versteckten Ordner im Benutzerprofil.
    Wer es anders will, setzt RB_GOLDEN_LAP_DIR.
    """
    return (os.environ.get('RB_GOLDEN_LAP_DIR')
            or os.path.dirname(os.path.abspath(__file__)))


ORDNER = daten_ordner()
ZUGANG = os.path.join(ORDNER, 'zugang')
CACHE = os.path.join(ORDNER, 'cache')
# Getrennt vom Cache: Der Cache ist Rechengrundlage und darf jederzeit weg,
# das Archiv sind die Originalexporte und soll bleiben.
CSV_ORDNER = os.path.join(ORDNER, 'csv-exports')


def melde(text=''):
    """Ausgeben und sofort sichtbar machen.

    Ohne `flush` schreibt Python blockweise: Ein Schritt, der ueber das Netz
    geht, saehe minutenlang wie ein Absturz aus.
    """
    print(text, flush=True)


# Ein Ausgang gehoert benannt, nicht erraten. Strg+C bleibt richtig und
# funktioniert, aber es ist die Notbremse und nicht die Tuer: Wer davon
# nichts weiss, drueckt es trotzdem -- und niemand darf darauf angewiesen
# sein, dass er es kennt oder fuer harmlos haelt. Jede Eingabezeile nennt
# deshalb ihren Ausgang, und `q` wirkt an jeder gleich.
AUSGANG = ('q', 'ende')

# Was `q` an der jeweiligen Stelle bedeutet, entscheidet der Aufrufer: auf
# der Streckenauswahl das Ende des Laufs, in der Fahrzeugauswahl den
# Schritt zurueck. Vom Abbruch per Strg+C ist es ausdruecklich
# unterschieden -- an der Startfrage heisst Strg+C "jetzt nicht holen" und
# `q` "Programm aus". Zweimal None wuerde die beiden verwechseln.
ENDE = object()


def frage(text):
    """Eine Zeile einlesen. `ENDE` fuer `q`, None fuer Strg+C oder Dateiende.

    Bewusst nicht benutzt bei der Frage nach dem Passwort: Dort waere `q`
    ein zulaessiges Passwort und keine Ansage.
    """
    try:
        antwort = input(text)
    except (EOFError, KeyboardInterrupt):
        melde()
        return None
    return ENDE if antwort.strip().lower() in AUSGANG else antwort


class NichtVerfuegbar(Exception):
    """Dieser Weg geht gerade nicht -- der andere vielleicht schon.

    Getrennt von SystemExit, damit der Aufrufer auf den CSV-Weg
    zurueckfallen kann, statt den ganzen Lauf abzubrechen.
    """


class AnmeldungFehlgeschlagen(SystemExit):
    """E-Mail oder Passwort stimmen nicht.

    Eigene Klasse, damit das Nachfragen nur hier greift: Ein 403 oder ein
    weggebrochenes Netz sind keine Tippfehler.
    """


# --- Zahlen und Zeiten ----------------------------------------------------

def zahl(text):
    """Eine Zahl aus dem CSV-Kopf. Immer mit Punkt, nie mit Komma.

    Bewusst nicht ueber die Locale: racebox.pro schreibt `15.701`, und auf
    einem deutschen System wuerde ein locale-abhaengiger Parser daraus
    15701 machen -- der Fehler faellt erst bei der Auswertung auf.
    """
    return float(text.strip())


def hundertstel(sekunden):
    """Auf Hundertstel abschneiden, nicht runden.

    Gerechnet wird mit Tausendsteln -- so kommen sie von der Box. Angezeigt
    werden Hundertstel, so wie die RaceBox-App und jede Zeitnahme an der
    Strecke. Abgeschnitten und nicht gerundet: Eine Zeit von 1:58.299 als
    1:58.30 zu zeigen hiesse, eine Hundertstel zu behaupten, die niemand
    gefahren ist.
    """
    # Erst auf Tausendstel festzurren: 6.8 liegt als 6.799999999999997 im
    # Speicher, und Abschneiden machte daraus 6.79. Die Quelle liefert
    # Tausendstel, also ist das die Genauigkeit, auf der gekuerzt wird.
    genau = round(sekunden, 3)
    schub = 1e-6 if genau >= 0 else -1e-6
    return int(genau * 100 + schub) / 100.0


def zeit_text(sekunden, breite=0):
    """Sekunden als `1:58.29` bzw. `58.29`, wie im Rennsport ueblich."""
    if sekunden is None:
        return '--'.rjust(breite)
    kurz = hundertstel(sekunden)
    minuten = int(kurz // 60)
    rest = kurz - minuten * 60
    if minuten:
        s = '%d:%05.2f' % (minuten, rest)
    else:
        s = '%.2f' % rest
    return s.rjust(breite)


def delta_text(sekunden, breite=0):
    """Ein Unterschied mit Vorzeichen: `-1.45`."""
    if sekunden is None:
        return '--'.rjust(breite)
    return ('%+.2f' % hundertstel(sekunden)).rjust(breite)


# --- Zugangsdaten ---------------------------------------------------------

ZUGANG_HILFE = """\
Die Zugangsdaten liegen im Klartext in %s (Rechte 0600, nur du darfst
lesen). Wer das nicht will, setzt stattdessen die Umgebungsvariablen
RACEBOX_EMAIL und RACEBOX_PASSWORT -- dann wird nichts gespeichert.
"""


def ordner_anlegen(ordner):
    """Anlegen, und bei einem schreibgeschuetzten Ort klar sagen, warum nicht.

    Neben dem Skript zu schreiben geht fast immer -- ausser das Skript
    liegt irgendwo, wo der Benutzer nichts darf. Dann ist ein Traceback die
    falsche Antwort.
    """
    try:
        os.makedirs(ordner, exist_ok=True)
    except OSError as e:
        raise SystemExit(
            'In %s laesst sich nichts anlegen (%s).\nLeg das Skript in '
            'einen Ordner, in dem du schreiben darfst, oder setze '
            'RB_GOLDEN_LAP_DIR auf einen anderen Ort.' % (ordner, e))


def zugang_lesen(neu=False):
    """E-Mail und Passwort holen: Umgebung, Datei, sonst fragen."""
    if os.environ.get('RACEBOX_EMAIL') and os.environ.get('RACEBOX_PASSWORT'):
        return os.environ['RACEBOX_EMAIL'], os.environ['RACEBOX_PASSWORT']

    if not neu and os.path.exists(ZUGANG):
        werte = {}
        with open(ZUGANG, encoding='utf-8') as f:
            for zeile in f:
                if zeile.strip() and not zeile.lstrip().startswith('#'):
                    schluessel, _, wert = zeile.partition('=')
                    werte[schluessel.strip().lower()] = wert.strip()
        if werte.get('email') and werte.get('passwort'):
            return werte['email'], werte['passwort']

    return zugang_fragen()


def zugang_fragen():
    """Fragen und ablegen. Das Passwort bleibt beim Tippen verdeckt."""
    import getpass
    melde(ZUGANG_HILFE % ZUGANG)
    email = input('RaceBox-E-Mail (leer = abbrechen): ').strip()
    passwort = getpass.getpass('RaceBox-Passwort: ')
    if not email or not passwort:
        raise SystemExit('Ohne E-Mail und Passwort geht es nicht.')

    ordner_anlegen(ORDNER)
    # Erst die Rechte, dann der Inhalt: Zwischen `open` und `chmod` laege
    # das Passwort sonst kurz fuer alle lesbar auf der Platte.
    fd = os.open(ZUGANG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('email = %s\npasswort = %s\n' % (email, passwort))
    melde('Abgelegt in %s' % ZUGANG)
    return email, passwort


# --- Die Sitzung auf racebox.pro ------------------------------------------

# Ein Verbindungsaufbau, der laenger dauert als das, ist keiner mehr. Der
# Wert entscheidet ueber Minuten: Loest ein Rechner eine IPv6-Adresse auf,
# die sein Netz nicht erreicht, wartet Windows sonst rund 21 Sekunden je
# Versuch -- und `urllib` baut fuer jede Anfrage eine neue Verbindung auf.
VERBINDUNGSGRENZE = 5


def steckdose(host, port, zeitgrenze, nur_ipv4=False):
    """Eine Verbindung zum ersten Adressen-Eintrag, der wirklich antwortet.

    `socket.create_connection` probiert die Adressen der Reihe nach und
    laesst jeder die volle Zeitgrenze -- bei kaputtem IPv6 sind das 21
    Sekunden Stillstand vor jedem einzelnen Byte. Hier bekommt jede Adresse
    nur VERBINDUNGSGRENZE Sekunden, danach ist die naechste dran.
    """
    familie = socket.AF_INET if nur_ipv4 else 0
    letzter = None
    for af, art, proto, _, adresse in socket.getaddrinfo(
            host, port, familie, socket.SOCK_STREAM):
        dose = socket.socket(af, art, proto)
        try:
            dose.settimeout(min(zeitgrenze, VERBINDUNGSGRENZE))
            dose.connect(adresse)
            dose.settimeout(zeitgrenze)
            return dose
        except OSError as e:
            letzter = e
            dose.close()
    raise letzter or OSError('keine Adresse fuer %s' % host)


class Verbindung:
    """Eine offen gehaltene HTTP-Verbindung zu racebox.pro.

    Der Grund ist gemessen und nicht vermutet: 259 Sessions kosten ueber
    500 Anfragen, und jede einzelne mit neuem TCP- und TLS-Handschlag zu
    bezahlen ist der Unterschied zwischen Minuten und Stunden. Die
    Verbindung bleibt deshalb stehen, und nur wenn die Gegenstelle sie
    zumacht, wird eine neue gebaut.

    Nicht threadsicher -- jeder Arbeiter bekommt seine eigene.
    """

    def __init__(self, basis, zeitgrenze=None, nur_ipv4=False):
        teile = urllib.parse.urlsplit(basis)
        self.sicher = teile.scheme != 'http'
        self.host = teile.hostname
        self.port = teile.port or (443 if self.sicher else 80)
        self.zeitgrenze = zeitgrenze or ZEITGRENZE
        self.nur_ipv4 = nur_ipv4
        self.leitung = None
        self.aufbauten = 0            # wie oft neu verbunden werden musste
        self.zeiten = {'aufbau': 0.0, 'warten': 0.0, 'laden': 0.0}

    def _leitung(self):
        if self.leitung is not None:
            return self.leitung, 0.0
        begonnen = time.monotonic()
        self.aufbauten += 1
        dose = steckdose(self.host, self.port, self.zeitgrenze, self.nur_ipv4)
        if self.sicher:
            zusammenhang = ssl.create_default_context()
            dose = zusammenhang.wrap_socket(dose, server_hostname=self.host)
        art = (http.client.HTTPSConnection if self.sicher
               else http.client.HTTPConnection)
        self.leitung = art(self.host, self.port, timeout=self.zeitgrenze)
        self.leitung.sock = dose
        return self.leitung, time.monotonic() - begonnen

    def zumachen(self):
        if self.leitung is not None:
            try:
                self.leitung.close()
            except OSError:
                pass
            self.leitung = None

    def hole(self, pfad, daten=None, kekse=None, nur_bis=None):
        """Eine Anfrage. Return (code, kopfzeilen, rumpf).

        `nur_bis` bricht das Lesen ab, sobald die Bytefolge im Rumpf steht --
        dafuer wird die Verbindung geschlossen, denn der Rest der Antwort
        stuende sonst quer in der Leitung.
        """
        for versuch in (1, 2):
            try:
                return self._hole(pfad, daten, kekse, nur_bis)
            except (http.client.HTTPException, ConnectionError, OSError) as e:
                # Eine offen gehaltene Verbindung kann jederzeit von der
                # Gegenseite zugemacht werden. Einmal neu aufbauen ist die
                # richtige Antwort darauf, aufgeben nicht.
                self.zumachen()
                if versuch == 2 or isinstance(e, (socket.timeout, TimeoutError)):
                    raise
        raise AssertionError('unerreichbar')

    def _hole(self, pfad, daten, kekse, nur_bis):
        leitung, aufbau = self._leitung()
        kopf = {'User-Agent': KENNUNG, 'Accept-Encoding': 'gzip',
                'Connection': 'keep-alive'}
        if kekse:
            kopf['Cookie'] = kekse
        rumpf = None
        if daten is not None:
            rumpf = urllib.parse.urlencode(daten).encode()
            kopf['Content-Type'] = 'application/x-www-form-urlencoded'
        begonnen = time.monotonic()
        leitung.request('POST' if daten is not None else 'GET', pfad,
                        body=rumpf, headers=kopf)
        antwort = leitung.getresponse()
        gewartet = time.monotonic() - begonnen

        begonnen = time.monotonic()
        if nur_bis:
            puffer = b''
            while nur_bis not in puffer and len(puffer) < 256 * 1024:
                stueck = antwort.read(4096)
                if not stueck:
                    break
                puffer += stueck
            inhalt = puffer
            self.zumachen()          # der Rest bleibt liegen
        else:
            inhalt = antwort.read()
        if antwort.headers.get('Content-Encoding') == 'gzip' and inhalt:
            try:
                inhalt = gzip.decompress(inhalt)
            except (OSError, EOFError):
                pass                 # abgebrochener Strom: unentpackbar
        self.zeiten['aufbau'] += aufbau
        self.zeiten['warten'] += gewartet
        self.zeiten['laden'] += time.monotonic() - begonnen
        if antwort.will_close:
            self.zumachen()
        return antwort.status, antwort.headers, inhalt


class RaceBox:
    """Anmelden, auflisten, Sessions holen.

    Eine Sitzung, aber mehrere Verbindungen: Die Anmeldung laeuft ueber eine
    einzige, danach bekommt jeder Arbeiter seine eigene. Die Kekse sind ab
    dann unveraenderlich und werden nur noch gelesen -- deshalb reicht ein
    schlichtes dict fuer alle.
    """

    def __init__(self, basis=BASIS, diagnose=None, zeitgrenze=None,
                 nur_ipv4=False):
        self.basis = basis.rstrip('/')
        self.diagnose = diagnose      # Ordner fuer Rohabzuege oder None
        self.zeitgrenze = zeitgrenze or ZEITGRENZE
        self.nur_ipv4 = nur_ipv4
        self.kekse = {}
        self.anfragen = 0             # nur zum Zaehlen im Selbsttest
        self.oertlich = threading.local()
        self.schloss = threading.Lock()
        self.verbindungen = []        # alle, um die Zeiten zu summieren

    # -- unterste Ebene ----------------------------------------------------

    @property
    def verbindung(self):
        """Die Verbindung dieses Threads, beim ersten Zugriff aufgebaut."""
        eigene = getattr(self.oertlich, 'verbindung', None)
        if eigene is None:
            eigene = Verbindung(self.basis, self.zeitgrenze, self.nur_ipv4)
            self.oertlich.verbindung = eigene
            with self.schloss:
                self.verbindungen.append(eigene)
        return eigene

    def zeiten(self):
        """Was der bisherige Lauf wo verbracht hat, ueber alle Arbeiter."""
        summe = {'aufbau': 0.0, 'warten': 0.0, 'laden': 0.0, 'aufbauten': 0}
        for verbindung in list(self.verbindungen):
            for name, wert in verbindung.zeiten.items():
                summe[name] += wert
            summe['aufbauten'] += verbindung.aufbauten
        return summe

    def session_holen(self, sid, csv_weg=False, fahrzeug=None,
                      mit_csv=True, csv_ordner=None):
        """Eine Session als Cache-Eintrag -- ueber JSON, sonst ueber CSV.

        Der JSON-Weg ist eine Anfrage statt zweier und bringt Fahrzeug,
        Konfiguration und Telemetrie mit. Der CSV-Weg bleibt als
        Rueckfallebene: Er ist der aeltere und laenger belegte, falls
        racebox.pro den anderen umbaut.
        """
        if not csv_weg:
            try:
                daten = self.sessiondaten(sid)
                csv_runden, ergebnis = None, None
                if mit_csv:
                    try:
                        text = self.export(sid)
                        export_ablegen(sid, text, csv_ordner)
                        csv_runden = kopf_lesen(text)['runden']
                        ergebnis = csv_abgleich(sid, daten, text)
                    except NichtVerfuegbar as e:
                        ergebnis = (['%s' % e], 0, 0)
                eintrag = session_aus_json(sid, daten, csv_runden)
                if ergebnis is not None:
                    eintrag['abgleich'] = ergebnis
                return eintrag
            except NichtVerfuegbar as e:
                melde('  %s -- weiter ueber den CSV-Weg' % e)
        seite = self.sessionseite(sid)
        return session_bauen(sid, self.kopfblock(sid), seite, fahrzeug)

    def _kekse_merken(self, kopfzeilen):
        for wert in kopfzeilen.get_all('Set-Cookie') or []:
            name, _, rest = wert.partition('=')
            with self.schloss:
                self.kekse[name.strip()] = rest.split(';')[0].strip()

    def _keksband(self):
        return '; '.join('%s=%s' % (k, v) for k, v in self.kekse.items())

    def _anfrage(self, pfad, daten=None, nur_bis=None):
        """Eine Anfrage; Weiterleitungen werden nicht verfolgt.

        Das ist hier kein Verzicht, sondern Absicht: Nach der Anmeldung
        schickt racebox.pro einen 302 hinterher, dessen Ziel an manchen
        Anschluessen nicht erreichbar ist. Gebraucht wird er nicht -- das
        Sitzungsmerkmal steht im 302 selbst.
        """
        with self.schloss:
            self.anfragen += 1
        try:
            code, kopfzeilen, rumpf = self.verbindung.hole(
                pfad, daten, self._keksband(), nur_bis)
        except (socket.timeout, TimeoutError):
            raise SystemExit(
                'Keine Antwort von %s innerhalb von %d Sekunden (%s).\n'
                'Laeuft ueber diesen Rechner ein Proxy oder eine VPN? '
                'Mit --zeitgrenze 120 laesst sich laenger warten.'
                % (self.basis, self.zeitgrenze, pfad))
        except OSError as e:
            raise SystemExit('racebox.pro ist nicht erreichbar: %s\n'
                             'Angefragt war %s%s.' % (e, self.basis, pfad))
        self._kekse_merken(kopfzeilen)
        if code == 403:
            raise SystemExit(
                'racebox.pro antwortet mit 403 auf %s.\nEin 403 kam frueher '
                'von der Kennung: racebox.pro sperrt `Python-urllib`. Steht '
                'in KENNUNG etwas anderes?' % pfad)
        if code >= 400:
            raise SystemExit('racebox.pro antwortet mit %s auf %s.'
                             % (code, pfad))
        return code, kopfzeilen, rumpf

    def _text(self, pfad, daten=None, name=None):
        _, _, roh = self._anfrage(pfad, daten)
        text = roh.decode('utf-8', 'replace')
        self._abzug(name, text)
        return text

    def _abzug(self, name, text):
        """Rohabzug fuer `--diagnose`.

        Wenn ein Suchmuster nicht mehr greift, ist die Seite umgebaut worden.
        Dann braucht es das HTML, nicht eine Vermutung darueber.
        """
        if self.diagnose and name:
            os.makedirs(self.diagnose, exist_ok=True)
            ziel = os.path.join(self.diagnose, name)
            with open(ziel, 'w', encoding='utf-8') as f:
                f.write(text)

    # -- die einzelnen Wege ------------------------------------------------

    def anmelden(self, email, passwort):
        """Anmelden und pruefen, dass es gewirkt hat.

        Auf den Statuscode ist kein Verlass: Die Seite antwortet auch mit
        200, wenn sie nur wieder das Anmeldeformular zeigt. Geprueft wird
        deshalb, ob danach noch ein Passwortfeld dasteht.
        """
        melde('  Anmeldeformular abschicken ...')
        code, kopfzeilen, _ = self._anfrage(
            LOGIN, {'email': email, 'password': passwort, 'redirect_to': ''})
        ziel = kopfzeilen.get('Location', '')
        if ziel:
            melde('  angenommen, Weiterleitung nach %s (wird nicht gefolgt)'
                  % ziel)
        else:
            melde('  Antwort %s ohne Weiterleitung' % code)
        melde('  Sessionliste holen ...')
        html = self._text(self.liste_url(), name='sessions_seite1.html')
        if 'name="password"' in html:
            raise AnmeldungFehlgeschlagen(
                'Anmeldung fehlgeschlagen -- E-Mail oder Passwort stimmen '
                'nicht. Neu setzen: rb-golden-lap.py --zugang')
        return html

    def liste_url(self, seite=1, vid='all'):
        """Die Sessionliste mit den Filtern aus der Oberflaeche.

        `type=track` blendet Aufzeichnungen anderer Art aus, `uid=own` alles,
        was nicht selbst gefahren wurde. Seite 1 kommt ohne `page`, wie beim
        Klick aus dem Menue.
        """
        url = '%s?type=track&tid=all&vid=%s&uid=own' % (LISTE, vid)
        return url + ('&page=%d' % seite if seite > 1 else '')

    def liste(self, seite=1, vid='all'):
        """Die Session-IDs einer Listenseite, in Dokumentreihenfolge.

        Bewusst ueber die Links und nicht ueber die Struktur der Seite: Ein
        Suchmuster auf `/webapp/session/<hex>` ueberlebt jede Umgestaltung
        der Kacheln, ein Parser dafuer nicht.
        """
        html = self._text(self.liste_url(seite, vid),
                          name='sessions_%s_seite%d.html' % (vid, seite))
        gesehen, raus = set(), []
        for sid in ID_MUSTER.findall(html):
            if sid not in gesehen:
                gesehen.add(sid)
                raus.append(sid)
        return raus

    def alle_ids(self, vid='all', melden=False):
        """Alle Sessions durchblaettern, bis eine Seite nichts Neues bringt.

        Abgebrochen wird bei einer leeren Seite und bei einer Seite, die nur
        schon Gesehenes zeigt -- manche Server geben bei zu hoher Seitenzahl
        wieder die letzte aus, das liefe sonst endlos.
        """
        alle, seite = [], 1
        while True:
            ids = self.liste(seite, vid)
            neu = [i for i in ids if i not in alle]
            if melden:
                melde('  Seite %d: %d Session(s)%s'
                      % (seite, len(ids), '' if neu else ', nichts Neues'))
            if not neu:
                return alle
            alle.extend(neu)
            seite += 1
            if seite > 200:           # Notbremse, nicht erwartet
                return alle

    def fahrzeuge(self, html=None):
        """Die Fahrzeuge aus dem Filtermenue der Sessionliste.

        Im CSV-Kopf steht kein Fahrzeug -- die Zuordnung geht nur ueber den
        `vid`-Filter der Liste. Deshalb hier erst die Auswahlliste lesen.
        """
        if html is None:
            html = self._text(self.liste_url(), name='sessions_seite1.html')
        return fahrzeuge_lesen(html)

    def sessionseite(self, sid):
        """Kopfdaten einer Session: Nummer des Turns, Tag und Startzeit.

        Rund 20 kB. Der Preis dafuer, die Ortszeit richtig zu haben -- im
        CSV-Kopf steht nur UTC im 12-Stunden-Format.
        """
        html = self._text(SEITE % sid, name='session_%s.html' % sid)
        t = KOPF_MUSTER.search(html)
        if not t:
            return None
        nr, tag, monat, jahr, stunde, minute = t.groups()
        return {'turn': int(nr), 'datum': '%s-%s-%s' % (jahr, monat, tag),
                'startzeit': '%s:%s' % (stunde, minute)}

    def kopfblock(self, sid):
        """Der Kopfblock des CSV-Exports -- ohne die Datenzeilen.

        Der Export ist rund 400 kB, gebraucht wird das erste Kilobyte. Also
        wird gelesen, bis die Spaltenzeile `Record,` auftaucht, und dann die
        Verbindung geschlossen. Der Server erzeugt zwar die ganze Datei, uns
        erreicht sie nicht -- ueber ein Mobilfunknetz ist das der
        Unterschied zwischen Minuten und Sekunden.
        """
        _, kopfzeilen, puffer = self._anfrage(EXPORT % sid, EXPORT_FELDER,
                                              nur_bis=b'\nRecord,')
        typ = kopfzeilen.get('Content-Type', '')
        text = puffer.split(b'\nRecord,')[0].decode('utf-8', 'replace')
        if 'RaceBox' not in text and 'csv' not in typ.lower():
            raise SystemExit(
                'Session %s liefert kein CSV (%s). Ist die Anmeldung noch '
                'gueltig?' % (sid, typ or 'ohne Angabe'))
        self._abzug('kopf_%s.txt' % sid, text)
        return text

    def export(self, sid, bikemode=True):
        """Der vollstaendige CSV-Export einer Session, wie von Hand geholt.

        Anders als `kopfblock` wird hier nicht nach dem Kopf abgebrochen:
        Die Datei soll so ins Archiv, wie sie aus der Oberflaeche kaeme.
        """
        felder = dict(EXPORT_FELDER)
        if bikemode:
            felder['bikeMode'] = '1'   # aus heisst: Feld gar nicht schicken
        _, kopfzeilen, roh = self._anfrage(EXPORT % sid, felder)
        typ = kopfzeilen.get('Content-Type', '')
        text = roh.decode('utf-8', 'replace')
        if 'RaceBox' not in text[:200] and 'csv' not in typ.lower():
            raise NichtVerfuegbar(
                'Session %s liefert kein CSV (%s)' % (sid, typ or 'ohne Angabe'))
        return text

    def sessiondaten(self, sid):
        """Eine Session als JSON -- alles, was die Webseite selbst benutzt.

        Der Weg steht auf jeder Sessionseite: `data-fetch-url` zeigt auf
        `/webapp/session/<id>/json`. Eine Anfrage liefert Strecke,
        Konfiguration, Fahrzeug, Turn, Ortszeit, alle Runden mit ihren
        Sektoren -- und dazu Hoechst- und Mindestgeschwindigkeit.

        Der frueher benutzte Weg brauchte dafuer zwei Anfragen, von denen
        eine den Server zwang, eine 400 kB grosse CSV zu bauen.
        """
        _, kopfzeilen, roh = self._anfrage(JSON % sid)
        typ = kopfzeilen.get('Content-Type', '')
        if 'json' not in typ.lower():
            raise NichtVerfuegbar(
                'Session %s liefert kein JSON (%s)' % (sid, typ or 'ohne Angabe'))
        try:
            daten = json.loads(roh.decode('utf-8', 'replace'))
        except ValueError as e:
            raise NichtVerfuegbar('Session %s: JSON unlesbar (%s)' % (sid, e))
        if self.diagnose:
            self._abzug('json_%s.json' % sid,
                        json.dumps(daten.get('session', {}).get('meta', {}),
                                   indent=1)[:200000])
        return daten


# --- Was im Kopfblock steht -----------------------------------------------

def kopf_lesen(text):
    """Den CSV-Kopfblock als dict, mit `runden` als Liste.

    Eine Rundenzeile sieht so aus:

        Lap 2, 37.013, sectors, 12.326,9.467,7.406,0,7.814

    Die Zahl der Sektorfelder ist fest je Strecke, unbenutzte Splits stehen
    als 0 -- auch mittendrin. Die Felder werden deshalb nach ihrer Position
    gefuehrt und nicht durchgezaehlt.
    """
    kopf = {'runden': []}
    for zeile in text.splitlines():
        teile = [p.strip() for p in zeile.split(',')]
        if not teile or not teile[0]:
            continue
        if teile[0].startswith('Lap ') and 'sectors' in teile:
            i = teile.index('sectors')
            sektoren = [zahl(v) for v in teile[i + 1:] if v != '']
            zeit = zahl(teile[1])
            kopf['runden'].append({
                'nr': int(teile[0][4:].strip()),
                'zeit': zeit,
                'sektoren': sektoren,
                'stimmig': abs(sum(sektoren) - zeit) < 0.051,
            })
        elif len(teile) >= 2:
            kopf[teile[0]] = teile[1]
    return kopf


def session_bauen(sid, kopftext, seite, fahrzeug):
    """Aus Kopfblock, Sessionseite und Fahrzeug einen Cache-Eintrag."""
    kopf = kopf_lesen(kopftext)
    datum_utc = kopf.get('Date UTC', '')
    eintrag = {
        'version': 1,          # CSV-Weg, die aeltere Rueckfallebene
        'id': sid,
        'geholt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'strecke': kopf.get('Track', '').strip(),
        'konfiguration': kopf.get('Configuration', '').strip(),
        'typ': kopf.get('Session Type', '').strip(),
        'datum_utc': datum_utc,
        'bestzeit_kopf': zahl(kopf['Best Lap Time'])
                         if kopf.get('Best Lap Time') else None,
        'geraet': kopf.get('Data Source', '').strip(),
        'fahrzeug': fahrzeug or 'ohne Fahrzeug',
        'runden': kopf['runden'],
    }
    # Die Sessionseite kennt Ortszeit und die Nummer des Turns. Faellt sie
    # aus, bleibt UTC als Notnagel -- besser eine Stunde daneben als leer.
    if seite:
        eintrag['datum'] = seite['datum']
        eintrag['startzeit'] = seite['startzeit']
        eintrag['turn'] = seite['turn']
    else:
        eintrag['datum'] = datum_utc[:10]
        eintrag['startzeit'] = datum_utc[11:16] + ' UTC' if datum_utc else '?'
        eintrag['turn'] = int(kopf.get('Session Index', 0) or 0)
    return eintrag


def runden_aus_meta(meta, splits):
    """Die Runden einer Session, wie sie in der Antwort stehen.

    Bewusst ohne das Verwerfen der Randsektoren -- das ist ein zweiter,
    eigener Schritt. Nur so laesst sich dieselbe Liste noch einmal bilden,
    wenn man sie unangetastet braucht, etwa fuer den Abgleich mit dem
    CSV-Export.
    """
    raus = []
    for nr, runde in enumerate(meta.get('laps') or [], 1):
        if not isinstance(runde, dict) or 'time' not in runde:
            continue
        sektoren = [0.0] * (splits + 1)
        gemessen = 0.0
        # Ueblich ist `{"time": 12.3, "sectorIndex": 0}`. Es kommen aber
        # auch blanke Zahlen vor -- und die sind *keine* Zeiten: Der
        # Abgleich mit dem Export zeigte eine Runde, deren Sektoren im JSON
        # als 1.00 und 2.00 dastanden, waehrend das CSV 24.89 und 17.02
        # nannte. Das waren die Indizes. Wer sie als Zeiten liest, baut
        # daraus eine Golden Lap, die niemand gefahren ist. Was sich nicht
        # eindeutig als Zeit ausweist, wird deshalb uebergangen.
        for stelle, sektor in enumerate(runde.get('sectors') or []):
            if not isinstance(sektor, dict):
                continue
            i, wert = sektor.get('sectorIndex', stelle), sektor.get('time')
            try:
                wert = float(wert)
            except (TypeError, ValueError):
                continue
            if isinstance(i, int) and 0 <= i < len(sektoren):
                sektoren[i] = round(wert, 3)
                gemessen += wert
        # Das Schlussstueck ins Ziel wird ueblicherweise nicht mitgeliefert;
        # es ist die Rundenzeit minus der Summe der uebrigen. Steht es aber
        # schon in der Liste, ist sein Platz belegt -- dann waere die
        # Differenz null und wuerde eine echte Sektorzeit ueberschreiben.
        rest = round(float(runde['time']) - gemessen, 3)
        abgeleitet = None
        if sektoren[-1] == 0 and rest > 0:
            sektoren[-1] = rest
            # Diese eine Zahl ist nicht gemessen, sondern die Differenz.
            # Bei einer vollstaendigen Runde geht die Rechnung auf; bei
            # einer Teilrunde traegt sie die ganze Unsicherheit.
            abgeleitet = len(sektoren)
        zeit = round(float(runde['time']), 3)
        raus.append({
            'nr': nr, 'zeit': zeit, 'sektoren': sektoren,
            'abgeleitet': abgeleitet,
            # Die Sektoren einer Runde muessen ihre Rundenzeit ergeben.
            # Tun sie es nicht, passt an den Daten etwas nicht, und dann
            # darf keine dieser Zahlen eine Sektorbestzeit werden.
            'stimmig': abs(sum(sektoren) - zeit) < 0.051,
        })
    return raus


def csv_runden_umrechnen(csv_runden, splits):
    """Die Rundenliste des CSV auf die Sektorpositionen des JSON bringen.

    Das CSV hat eine feste Zahl Felder und legt das Schlussstueck ans
    Ende: Eine Strecke mit drei Splitlinien belegt die Felder 1, 2, 3 und
    5, Feld 4 bleibt leer. Gerechnet wird dagegen mit vier dicht
    gezaehlten Sektoren. Uebersetzt wird deshalb ueber die Bedeutung der
    Felder und nicht ueber ihre Nummer.
    """
    raus = []
    for runde in csv_runden:
        feld = runde['sektoren']
        zwischen = (list(feld[:splits]) + [0.0] * splits)[:splits]
        dicht = zwischen + [feld[-1] if feld else 0.0]
        raus.append({
            'nr': runde['nr'], 'zeit': runde['zeit'], 'sektoren': dicht,
            # Im CSV ist jede Zahl geliefert, keine gerechnet.
            'abgeleitet': None,
            'stimmig': abs(sum(dicht) - runde['zeit']) < 0.051,
        })
    return raus


def session_aus_json(sid, daten, csv_runden=None):
    """Ein Cache-Eintrag aus der JSON-Antwort der Webseite.

    Zwei Dinge stehen dort besser als im CSV-Kopf. Erstens die Sektoren:
    Sie kommen als Liste mit `sectorIndex` statt als Felder mit aufgefuellten
    Nullen -- die Zahl der Splitpunkte steht in der Streckenkonfiguration
    und muss nicht erraten werden. Der letzte Sektor, vom letzten Split ins
    Ziel, wird nicht mitgeliefert; er ist die Rundenzeit minus der Summe der
    uebrigen.

    Zweitens `lapEvents`, die Zahl der Ziellinienueberfahrten. Damit ist
    erkennbar, welche Sektoren an den Raendern einer Session gar keine
    Messung zwischen zwei Linien sind -- siehe `raender_entschaerfen`.
    """
    meta = (daten.get('session') or {}).get('meta') or {}
    if not meta:
        raise NichtVerfuegbar('Session %s: keine Kopfdaten im JSON' % sid)
    strecke = meta.get('track') or {}
    konfig = strecke.get('configuration') or {}
    splits = len(konfig.get('splitLines') or [])
    fahrzeug = meta.get('vehicle') or {}
    name = ' '.join(str(fahrzeug.get(t, '')).strip()
                    for t in ('make', 'model')).strip()

    # Das CSV fuehrt nur die abgeschlossenen Runden -- die Exportmaske
    # sagt es woertlich ("all completed lap and sector times") -- und
    # liefert jede Sektorzeit einschliesslich des Schlussstuecks. Damit
    # entfaellt beides: das Rechnen des letzten Sektors und das Verwerfen
    # der Randsektoren. Nur wenn der Export keine Rundenzeilen enthaelt,
    # bleibt der Weg ueber das JSON.
    if csv_runden:
        runden = csv_runden_umrechnen(csv_runden, splits)
    else:
        runden = runden_aus_meta(meta, splits)
        raender_entschaerfen(runden, meta)

    # `dateTimeStartedLocal` ist die Ortszeit als Zeitstempel -- gelesen
    # wird er deshalb bewusst als UTC: Wir wollen die Wanduhr des Fahrers,
    # nicht die Zeitzone dieses Rechners.
    ortszeit = datetime.fromtimestamp(
        meta.get('dateTimeStartedLocal', 0) or 0, timezone.utc)
    return {
        'version': CACHE_VERSION,
        'id': sid,
        'geholt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'quelle': 'json+csv' if csv_runden else 'json',
        'strecke': (strecke.get('name') or '').strip(),
        'konfiguration': (konfig.get('name') or '').strip(),
        'konfig_id': konfig.get('id') or '',
        'splits': splits,
        'typ': 'Track' if meta.get('sessionType') == 1 else 'Anderes',
        'datum_utc': datetime.fromtimestamp(
            meta.get('dateTimeStartedUTC', 0) or 0,
            timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'datum': ortszeit.strftime('%Y-%m-%d'),
        'startzeit': ortszeit.strftime('%H:%M'),
        'turn': meta.get('indexInTheDay', 0),
        'bestzeit_kopf': meta.get('bestLapTime'),
        'geraet': str(meta.get('deviceSerialNumber', '')),
        'fahrzeug': name or 'ohne Fahrzeug',
        'kennzahlen': kennzahlen_rechnen(meta, daten.get('session', {})
                                         .get('data') or {}),
        # Die folgenden fuenf sind RaceBoxens eigene Zahlen, so wie sie in
        # der Antwort stehen -- und sie werden nirgends gelesen. Das ist
        # Absicht und keine Nachlaessigkeit: `maxSpeed` traegt genau den
        # Messfehler, den `kennzahlen_rechnen` herausrechnet; in einer
        # echten Session standen dort 221.99 km/h aus einem Messpunkt ohne
        # Positionsfix, waehrend dasselbe Geraet auf derselben Strecke in
        # drei Jahren nie ueber 118 kam. Abgelegt bleiben sie als
        # Gegenprobe -- wer wissen will, was RaceBox selbst behauptet,
        # findet es hier. Wer sie in eine Rechnung zieht, holt sich den
        # Fehler zurueck.
        'dauer': meta.get('duration'),
        'fahrzeug_id': fahrzeug.get('id', ''),
        'hoechstgeschwindigkeit': meta.get('maxSpeed'),
        'mindestgeschwindigkeit': meta.get('minSpeed'),
        'groesste_g': meta.get('maxG'),
        'runden': runden,
    }


# Ab hier gilt eine Fahrt als Fahrt. Darunter steht das Motorrad in der
# Box, rollt an oder wartet auf die Freigabe -- das gehoert nicht in eine
# Durchschnittsgeschwindigkeit und nicht in die gefahrenen Kilometer.
FAHRSCHWELLE = 5.0        # km/h


# Wie weit ein Messpunkt von der Mitte seiner eigenen Aufzeichnung
# entfernt liegen darf. Bewusst grosszuegig: Auf keiner Rennstrecke liegen
# zwei Punkte derselben Session hundert Kilometer auseinander. Verliert der
# Empfaenger seinen Fix, schreibt er auch nicht "ein bisschen daneben",
# sondern 0/0 -- den Nullpunkt des Gradnetzes im Golf von Guinea, viertausend
# Kilometer entfernt. Die Grenze trennt damit Messung von Nichtmessung und
# sagt nichts ueber die Fahrt.
ORTSGRENZE = 100000        # Meter


# Wie lange nach einem Ausfall die Werte noch unbrauchbar sind. Ein
# Empfaenger, der seinen Fix verloren hat, liefert nicht sofort wieder
# saubere Zahlen: In zwei echten Sessions lagen alle sieben unmoeglichen
# Geschwindigkeiten innerhalb von 0,16 s nach einem Punkt ohne Position --
# und die Position selbst war da laengst wieder richtig, nur die
# Geschwindigkeit las abwechselnd 0,00 und 222 km/h. Eine Sekunde ist
# grosszuegig gewaehlt: Sie kostet in einer beschaedigten Aufzeichnung drei
# Prozent der Punkte und in einer heilen keinen einzigen, weil es dort
# keinen Ausfall gibt.
FIXERHOLUNG = 1.0        # Sekunden

# Die Felder aus den abgelegten Exporten haben ihre eigene Version, getrennt
# von `CACHE_VERSION`. Sie entstehen ohne Netz, also soll eine Korrektur an
# ihrer Rechnung auch keinen Download nach sich ziehen -- ein erneutes Lesen
# der Dateien genuegt. Hochzaehlen, wenn sich an `runden_aus_export` etwas
# aendert; sonst blieben die alten Zahlen stehen und niemand saehe es.
RUNDEN_VERSION = 2

# Ab wann eine Abweichung zwischen Lap-Spalte und Rundenzeiten eine eigene
# Meldung wert ist. Die Grenze verwirft nichts -- gezaehlt wird nur, damit
# ein Einzelfall nicht wie ein Systemfehler aussieht. Ueblich sind drei
# Hundertstel, ein Messpunkt bei 25 Hz; eine ganze Sekunde ist davon weit
# entfernt und heisst: Dort stimmt die Rundengrenze nicht.
PROBE_GRENZE = 1.0        # Sekunden



def ortsmitte(punkte):
    """Die Mitte einer Aufzeichnung als Median von Breite und Laenge.

    Median und nicht Mittelwert: Ein einziger Punkt auf 0/0 zoege einen
    Mittelwert schon merklich fort, und dann laege die Mitte neben der
    Strecke. Der Median haelt still, solange die Ausfaelle nicht die
    Mehrheit sind.
    """
    if not punkte:
        return None
    breiten = sorted(p[0] for p in punkte)
    laengen = sorted(p[1] for p in punkte)
    mitte = len(punkte) // 2
    return breiten[mitte], laengen[mitte]


def am_ort(breite, laenge, mitte):
    """Ob ein Messpunkt zu dieser Aufzeichnung gehoert.

    Gerechnet in der Ebene und nicht auf der Kugel: Bei Entfernungen, um
    die es hier geht -- ein paar Kilometer gegen ein paar tausend -- ist
    der Unterschied ohne Belang, und die Formel bleibt lesbar.
    """
    if mitte is None:
        return True
    dy = (breite - mitte[0]) * 111320.0
    dx = (laenge - mitte[1]) * 111320.0 * math.cos(math.radians(mitte[0]))
    return dy * dy + dx * dx <= ORTSGRENZE * ORTSGRENZE


def ungueltige_punkte(punkte, mitte):
    """Die Indizes der Messpunkte, die keine Messung sind.

    `punkte` ist eine Liste aus (Zeit in Sekunden, Breite, Laenge).

    Ungueltig ist, wessen Position nicht zur Aufzeichnung gehoert -- und
    alles, was in den `FIXERHOLUNG` Sekunden davor und danach liegt. Das
    Zweite ist der eigentliche Fang: Der Ausreisser selbst hat eine
    tadellose Position, er steht nur zwei Messpunkte hinter einem, der
    keine hatte.
    """
    if mitte is None:
        return set()
    ohne = [i for i, (_, b, l) in enumerate(punkte) if not am_ort(b, l, mitte)]
    if not ohne:
        return set()
    raus = set(ohne)
    for i in ohne:
        wann = punkte[i][0]
        for richtung in (-1, 1):
            j = i + richtung
            while (0 <= j < len(punkte)
                   and abs(punkte[j][0] - wann) <= FIXERHOLUNG):
                raus.add(j)
                j += richtung
    return raus


def kennzahlen_rechnen(meta, daten):
    """Kilometer, Geschwindigkeiten und Schraeglagen einer Session.

    Die Telemetrie steckt im selben JSON wie die Rundenzeiten und laesst
    sich nicht abtrennen -- geladen wird sie also ohnehin. Sie hier gleich
    auszurechnen kostet keine zusaetzliche Anfrage und kein zusaetzliches
    Byte; nachtraeglich waeren es 259 neue Downloads.

    Die Strecke kommt aus der Geschwindigkeit mal Zeit, nicht aus dem
    Abstand der GPS-Punkte. Beides waere moeglich, aber die Geschwindigkeit
    misst der Empfaenger ueber die Dopplerverschiebung und damit deutlich
    ruhiger: Aus Punktabstaenden summierte sich im Stand aus lauter Rauschen
    eine Scheinstrecke.

    Der Abstand der GPS-Punkte wird trotzdem gebraucht, aber fuer etwas
    anderes: Wo der Empfaenger seinen Fix verliert, schreibt er die Position
    als 0/0 und die Geschwindigkeit meist als 0.00 -- dazwischen aber auch
    einzelne Werte, die es nie gegeben hat. In einer echten Aufzeichnung
    standen so 221.99 km/h auf einer Strecke, auf der dasselbe Geraet in
    drei Jahren nie ueber 118 kam; die Nachbarpunkte lasen 0.00, und die
    Position sprang nach Null. Solche Punkte werden uebergangen und
    gezaehlt. Das ist keine Annahme darueber, wie schnell jemand faehrt --
    ein Punkt ohne Position ist keine Messung.
    """
    spalten = daten.get('dataColumns') or []
    zeilen = daten.get('data') or []
    if not zeilen or 'Speed' not in spalten:
        return None
    wo = {name: i for i, name in enumerate(spalten)}
    i_zeit, i_v = wo.get('iTOW'), wo['Speed']
    i_lage = wo.get('LeanAngle')
    i_breite, i_laenge = wo.get('Latitude'), wo.get('Longitude')

    # Ohne Zeitspalte gaebe es kein Fenster fuer die Erholung -- dann
    # dient der laufende Index als Ersatzuhr. Die Box misst mit 25 Hz.
    ungueltig = set()
    mitte = None
    if i_breite is not None and i_laenge is not None:
        orte = [((z[i_zeit] / 1000.0) if i_zeit is not None else nr / 25.0,
                 z[i_breite] or 0.0, z[i_laenge] or 0.0)
                for nr, z in enumerate(zeilen) if len(z) > i_laenge]
        mitte = ortsmitte([(b, l) for _, b, l in orte])
        ungueltig = ungueltige_punkte(orte, mitte)

    meter = meter_gesamt = fahrzeit = gesamtzeit = 0.0
    v_max = lage_links = lage_rechts = 0.0
    v_min = None
    vorige_zeit = None
    for nr, zeile in enumerate(zeilen):
        if nr in ungueltig:
            # Kein Fix, keine Messung. Auch die Zeit darueber zaehlt nicht:
            # Was in dieser Luecke gefahren wurde, weiss niemand.
            vorige_zeit = None
            continue
        v = zeile[i_v] or 0.0
        if v > v_max:
            v_max = v
        if v > FAHRSCHWELLE and (v_min is None or v < v_min):
            v_min = v
        if i_lage is not None and len(zeile) > i_lage:
            lage = zeile[i_lage] or 0.0
            lage_links = max(lage_links, lage)
            lage_rechts = min(lage_rechts, lage)
        if i_zeit is None:
            continue
        jetzt = zeile[i_zeit]
        if vorige_zeit is not None:
            dt = (jetzt - vorige_zeit) / 1000.0
            # Ein Sprung heisst Luecke in der Aufzeichnung, keine Fahrt.
            if 0 < dt < 5:
                gesamtzeit += dt
                meter_gesamt += v / 3.6 * dt
                if v > FAHRSCHWELLE:
                    fahrzeit += dt
                    meter += v / 3.6 * dt
        vorige_zeit = jetzt

    return {
        'meter': round(meter),
        'meter_gesamt': round(meter_gesamt),
        'fahrzeit': round(fahrzeit, 1),
        'gesamtzeit': round(gesamtzeit, 1),
        'v_max': round(v_max, 2),
        'v_min': round(v_min, 2) if v_min is not None else None,
        'v_schnitt': round(meter / fahrzeit * 3.6, 2) if fahrzeit else None,
        'schraeglage_links': round(lage_links, 1),
        'schraeglage_rechts': round(abs(lage_rechts), 1),
        'punkte': len(zeilen) - len(ungueltig),
        'punkte_ohne_ort': len(ungueltig),
        'schwelle': FAHRSCHWELLE,
    }


def csv_sekunden(text):
    """`2026-08-25T13:36:33.480Z` als Sekunden seit Mitternacht UTC.

    Von Hand zerlegt und nicht ueber `strptime`: Das laeuft je Datenzeile
    einmal, und bei siebzigtausend Zeilen je Session kostet der Umweg ueber
    die Zeitzonenrechnerei mehr als die ganze uebrige Schleife. Gebildet
    werden ohnehin nur Differenzen -- der Tageswechsel um Mitternacht
    ergibt genau eine negative, und die faellt durch dieselbe Pruefung wie
    eine Luecke in der Aufzeichnung.
    """
    try:
        return (int(text[11:13]) * 3600 + int(text[14:16]) * 60
                + float(text[17:23]))
    except (ValueError, IndexError):
        return None


def runden_aus_export(pfad):
    """Meter und Sekunden je Rundennummer aus einem abgelegten Export.

    Die Datenzeilen des Originalexports fuehren eine Spalte `Lap`: Sie
    sagt je Messpunkt, zu welcher gezaehlten Runde er gehoert, und `0`
    heisst zu keiner -- Boxenausfahrt, Auslaufrunde, Stehen. Damit laesst
    sich beantworten, was der Cache allein nicht hergibt: wie viele der
    gefahrenen Kilometer in gezaehlten Runden lagen. Bei den Zeiten geht
    das ohne Weiteres, weil jede Rundenzeit einzeln im Cache steht; bei den
    Kilometern braeuchte es die Zuordnung jedes einzelnen Messpunkts, und
    im Cache steht kein einziger.

    Gerechnet wird genau wie in `kennzahlen_rechnen` -- Strecke aus
    Geschwindigkeit mal Zeit, gefahren ab `FAHRSCHWELLE`. Sonst waeren die
    beiden Zahlen nicht vergleichbar, und die Gegenprobe unten waere keine.

    Return ein dict oder None, wenn die Datei keine Datenzeilen oder keine
    Lap-Spalte hat.
    """
    kopfrunden, spalten = [], None
    punkte = []                       # (Sekunde, km/h, Runde, Breite, Laenge)
    i_t = i_v = i_lap = i_breite = i_laenge = 0
    try:
        datei = open(pfad, encoding='utf-8', errors='replace')
    except OSError:
        return None
    with datei as f:
        for zeile in f:
            if spalten is None:
                zeile = zeile.rstrip('\r\n')
                if zeile.startswith('Record,'):
                    spalten = zeile.split(',')
                    gebraucht = {'Lap', 'Speed', 'Time', 'Latitude',
                                 'Longitude'}
                    if not gebraucht <= set(spalten):
                        return None
                    i_t = spalten.index('Time')
                    i_v = spalten.index('Speed')
                    i_lap = spalten.index('Lap')
                    i_breite = spalten.index('Latitude')
                    i_laenge = spalten.index('Longitude')
                    continue
                # Die Rundenzeiten aus dem Kopf -- sie sind die Probe darauf,
                # dass die Lap-Spalte haelt.
                teile = [p.strip() for p in zeile.split(',')]
                if teile and teile[0].startswith('Lap ') and 'sectors' in teile:
                    try:
                        kopfrunden.append((int(teile[0][4:]), zahl(teile[1])))
                    except ValueError:
                        pass
                continue
            felder = zeile.split(',')
            if len(felder) <= max(i_lap, i_laenge):
                continue
            jetzt = csv_sekunden(felder[i_t])
            try:
                v, runde = float(felder[i_v]), int(felder[i_lap])
                breite = float(felder[i_breite])
                laenge = float(felder[i_laenge])
            except ValueError:
                continue
            if jetzt is None:
                continue
            punkte.append((jetzt, v, runde, breite, laenge))
    if spalten is None:
        return None

    # Dieselbe Regel wie in `kennzahlen_rechnen`: Ein Punkt ohne gueltige
    # Position ist keine Messung. Erst danach wird gerechnet, damit die
    # beiden Quellen dieselben Punkte zaehlen und die Gegenprobe unten
    # etwas wert ist.
    mitte = ortsmitte([(p[3], p[4]) for p in punkte])
    ungueltig = ungueltige_punkte([(p[0], p[3], p[4]) for p in punkte], mitte)
    meter = fahrzeit = 0.0            # in gezaehlten Runden
    meter_alle = 0.0                  # alles Gefahrene laut Export
    # Zwei Uhren, und sie messen absichtlich Verschiedenes. `wanduhr` laeuft
    # ueber alles, was zwischen zwei Messpunkten liegt -- auch ueber eine
    # Aufzeichnungsluecke und ueber verworfene Punkte. Nur sie ist mit der
    # Rundenzeit aus dem Kopfblock vergleichbar, denn die zaehlt ebenfalls
    # durch. Die Meter dagegen werden nur dort gezaehlt, wo wirklich
    # gemessen wurde: Was in einer Luecke gefahren wurde, weiss niemand.
    wanduhr = {}                      # Rundennummer -> Sekunden, alles
    vorige = vorige_uhr = None
    for nr, (jetzt, v, runde, breite, laenge) in enumerate(punkte):
        if vorige_uhr is not None and jetzt > vorige_uhr:
            wanduhr[runde] = wanduhr.get(runde, 0.0) + (jetzt - vorige_uhr)
        vorige_uhr = jetzt
        if nr in ungueltig:
            vorige = None
            continue
        if vorige is not None:
            dt = jetzt - vorige
            # Ein Sprung heisst Luecke in der Aufzeichnung, keine Fahrt.
            if 0 < dt < 5:
                if v > FAHRSCHWELLE:
                    meter_alle += v / 3.6 * dt
                    if runde > 0:
                        meter += v / 3.6 * dt
                        fahrzeit += dt
        vorige = jetzt

    # Die Probe: Die aus den Datenzeilen zurueckgerechnete Dauer jeder Runde
    # muss ihre Rundenzeit aus dem Kopf ergeben. Tut sie es nicht, sagt die
    # Lap-Spalte etwas anderes als die Rundenzeiten -- und dann ist die
    # Aufteilung keine.
    #
    # Verglichen wird gegen `wanduhr` und nicht gegen die gefahrene Zeit.
    # Sonst misst die Probe die eigenen Auslassungen: In einer echten
    # Session stand eine halbstuendige Runde, in der die Box dreimal
    # aussetzte -- 274 + 52 + 94 Sekunden. Die Probe meldete daraufhin
    # 419,865 s Abweichung, waehrend die uebrigen vierzehn Runden derselben
    # Session auf 0,032 s stimmten. Der Fehler lag in der Probe.
    probe = max((abs(wanduhr.get(nr, 0.0) - soll) for nr, soll in kopfrunden),
                default=0.0)
    return {
        'runden_version': RUNDEN_VERSION,
        'meter_runden': round(meter),
        'fahrzeit_runden': round(fahrzeit, 1),
        'meter_export': round(meter_alle),
        'runden_probe': round(probe, 3),
    }


def runden_km_ergaenzen(sessions, csv_ordner=None, cache_ordner=None):
    """Fehlende Rundenkilometer aus den abgelegten Exporten nachtragen.

    Kein Netz und keine neue Cache-Version: Der vollstaendige Export liegt
    schon auf der Platte, weil er beim Holen ohnehin abgelegt wird. Einmal
    je Session gerechnet und in dieselbe Cache-Datei geschrieben -- beim
    naechsten Lauf ist nichts mehr zu tun.

    Return (ergaenzt, ohne_export).
    """
    offen = [s for s in sessions
             if s.get('kennzahlen')
             and s['kennzahlen'].get('runden_version', 0) < RUNDEN_VERSION]
    if not offen:
        return 0, 0
    melde('Rundenkilometer aus den abgelegten Exporten (%s) ...'
          % anzahl(len(offen), 'Session', 'Sessions'))
    ergaenzt = ohne = 0
    for session in offen:
        pfad = export_pfad(session['id'], csv_ordner)
        if not os.path.exists(pfad):
            # Kein Export, kein Urteil: Vielleicht liegt er beim naechsten
            # Lauf da. Vermerkt wird deshalb nichts -- nachgesehen wird
            # wieder, und das kostet ein os.path.exists.
            ohne += 1
            continue
        werte = runden_aus_export(pfad)
        # Auch ein "da steht nichts Brauchbares drin" wird vermerkt. Sonst
        # wuerden bei jedem Lauf wieder drei Megabyte dafuer gelesen.
        session['kennzahlen'].update(werte or {'meter_runden': None})
        session['kennzahlen']['runden_version'] = RUNDEN_VERSION
        cache_schreiben(session, cache_ordner)
        ergaenzt += 1
    melde('  %d ergaenzt%s' % (ergaenzt,
                               ', %d ohne Export' % ohne if ohne else ''))
    return ergaenzt, ohne


def raender_entschaerfen(runden, meta):
    """Sektoren an den Raendern einer Session verwerfen, die keine sind.

    Aufgelistet werden nur Runden zwischen Linienueberfahrten -- was davor
    und danach gefahren wurde, taucht als Runde gar nicht auf. Meistens
    sind die Randsektoren also echte Messungen zwischen zwei Linien, nur
    langsam gefahren. An zwei Stellen aber nicht:

    **Faengt die erste Runde bei Record 0 an**, lief ihr erstes Stueck ab
    dem Moment, in dem die Box aufzeichnet, und nicht ab einer Linie. Ob
    das so ist, steht in den Daten: `sensorRecordIndex` ist der Record, an
    dem die Runde endet, und Rundenzeit mal Abtastrate ist ihre Laenge.

    **Endet die letzte Runde nicht am Ziel**, lief ihr letztes Stueck bis
    zum Ende der Aufzeichnung. Erkennbar an `lapEvents`: Liegt eine Runde
    mehr vor als Ueberfahrten, ist die letzte eine Ausfahrrunde.

    Meist sind solche Zeiten zu lang und damit harmlos -- eine zu lange
    Zeit wird nie eine Bestzeit. Sie koennen aber zu kurz sein: Wer zwei
    Sekunden vor einem Splitpunkt auf Aufnahme drueckt, bekaeme sonst
    einen Sektorrekord geschenkt, den niemand gefahren ist.
    """
    if not runden:
        return
    dauer = meta.get('duration') or 0
    punkte = meta.get('records') or 0
    rate = (punkte / dauer) if dauer else 0
    erste_roh = (meta.get('laps') or [{}])[0]
    ende = erste_roh.get('sensorRecordIndex')
    if rate and ende is not None:
        beginn = ende - runden[0]['zeit'] * rate
        if beginn < rate:            # weniger als eine Sekunde nach dem Start
            for i, wert in enumerate(runden[0]['sektoren']):
                if wert > 0:
                    runden[0]['sektoren'][i] = 0.0
                    break

    linien = meta.get('lapEvents')
    if linien is not None and len(runden) > linien:
        runden[-1]['sektoren'][-1] = 0.0


# --- Fahrzeuge aus dem Filtermenue ----------------------------------------

SELECT_MUSTER = re.compile(r'<select\b(?P<attr>[^>]*)>(?P<inhalt>.*?)</select>',
                           re.S | re.I)
OPTION_MUSTER = re.compile(
    r'<option\b[^>]*\bvalue\s*=\s*["\']?(?P<wert>[^"\'>\s]*)["\']?[^>]*>'
    r'(?P<text>.*?)</option>', re.S | re.I)
TAGS = re.compile(r'<[^>]+>')


def fahrzeuge_lesen(html):
    """{id: name} aus der Fahrzeugauswahl der Sessionliste.

    Zwei Wege, in dieser Reihenfolge: das Auswahlfeld, dessen Name `vid`
    ist, sonst das, dessen erster Eintrag "All Vehicles" heisst. Findet
    sich keines, kommt ein leeres dict zurueck -- der Aufrufer arbeitet
    dann ohne Fahrzeuge weiter, statt abzubrechen.
    """
    kandidaten = []
    for t in SELECT_MUSTER.finditer(html):
        attr, inhalt = t.group('attr'), t.group('inhalt')
        eintraege = []
        for o in OPTION_MUSTER.finditer(inhalt):
            name = TAGS.sub('', o.group('text'))
            name = ' '.join(name.split())
            eintraege.append((o.group('wert'), name))
        if not eintraege:
            continue
        heisst_vid = re.search(r'\b(name|id)\s*=\s*["\']?vid\b', attr, re.I)
        alle_fahrzeuge = any(
            e[0] in ('all', '') and 'vehicle' in e[1].lower()
            for e in eintraege)
        if heisst_vid or alle_fahrzeuge:
            kandidaten.append(eintraege)

    if not kandidaten:
        return {}
    return {wert: name for wert, name in kandidaten[0]
            if wert and wert != 'all'}


# --- Abgleich mit dem CSV-Export ------------------------------------------

def export_pfad(sid, ordner=None):
    """Wo der Originalexport einer Session liegt."""
    return os.path.join(ordner or CSV_ORDNER, '%s_bikemode.csv' % sid)


def export_ablegen(sid, text, ordner=None):
    """Den Originalexport ablegen. Return den Pfad."""
    ordner = ordner or CSV_ORDNER
    ordner_anlegen(ordner)
    pfad = export_pfad(sid, ordner)
    vorlaeufig = pfad + '.neu'
    with open(vorlaeufig, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    os.replace(vorlaeufig, pfad)
    return pfad


def csv_abgleich(sid, daten, csvtext):
    """Die Sektoren aus dem JSON gegen die aus dem CSV-Export halten.

    Beide kommen aus derselben Datenbank -- das hier ist keine zweite
    Meinung ueber die Wirklichkeit, sondern eine ueber die Rechnung. Der
    entscheidende Unterschied: Das CSV liefert den Schlusssektor mit,
    waehrend er im JSON fehlt und gebildet werden muss. Genau diese eine
    Zahl hat schon einmal eine Golden Lap um sechs Sekunden verkuerzt.

    Verglichen wird gegen die *unbereinigte* Rundenliste: Was beim Holen
    absichtlich verworfen wird, waere sonst als Abweichung gemeldet.

    Return (befunde, verglichene Runden, nur im JSON gefuehrte Runden).
    Die letzte Zahl ist keine Abweichung: Das sind die Ein- und
    Ausfahrrunden, die das CSV nicht kennt.
    """
    meta = (daten.get('session') or {}).get('meta') or {}
    konfig = (meta.get('track') or {}).get('configuration') or {}
    aus_json = runden_aus_meta(meta, len(konfig.get('splitLines') or []))
    aus_csv = kopf_lesen(csvtext)['runden']

    if not aus_csv:
        return (['%s: der CSV-Export enthaelt keine Rundenzeilen' % sid[:8]],
                0, len(aus_json))

    # Zugeordnet wird ueber die Rundenzeit, nicht ueber die Position: Das
    # JSON fuehrt zusaetzlich die Ein- und die Ausfahrrunde, die das CSV
    # gar nicht listet. Wer stur Position gegen Position haelt, meldet
    # eine ganze Session als abweichend, obwohl jede Zahl stimmt.
    befunde = []
    offen = list(aus_json)
    for csv_runde in aus_csv:
        passend = [j for j in offen
                   if abs(j['zeit'] - csv_runde['zeit']) < 0.0005]
        if not passend:
            befunde.append('%s Runde %d (%s): steht nur im CSV'
                           % (sid[:8], csv_runde['nr'],
                              zeit_text(csv_runde['zeit'])))
            continue
        json_runde = passend[0]
        offen.remove(json_runde)
        # Das CSV fuellt unbenutzte Sektorfelder mit Nullen auf und legt
        # das Schlussstueck ans Ende; das JSON zaehlt dicht durch. Ueber
        # die belegten Werte in ihrer Reihenfolge sind beide vergleichbar.
        csv_werte = [w for w in csv_runde['sektoren'] if w > 0]
        json_werte = [w for w in json_runde['sektoren'] if w > 0]
        if len(csv_werte) != len(json_werte):
            befunde.append('%s Runde %d: %d Sektorzeiten im CSV, %d im JSON'
                           % (sid[:8], csv_runde['nr'], len(csv_werte),
                              len(json_werte)))
            continue
        for nr, (aus_c, aus_j) in enumerate(zip(csv_werte, json_werte), 1):
            if abs(aus_c - aus_j) > 0.0005:
                befunde.append(
                    '%s Runde %d Sektor %d: %s im CSV, %s im JSON'
                    % (sid[:8], csv_runde['nr'], nr, zeit_text(aus_c),
                       zeit_text(aus_j)))
    return befunde, len(aus_csv), len(offen)


# --- Cache ----------------------------------------------------------------
#
# Eine aufgezeichnete Session aendert sich nicht mehr. Sie wird deshalb genau
# einmal geholt und liegt danach als JSON auf der Platte; jeder weitere Lauf
# fragt nur nach dem, was neu dazugekommen ist.

def cache_pfad(ordner=None):
    return ordner or CACHE


def cache_lesen(ordner=None):
    """Alle abgelegten Sessions als {id: eintrag}."""
    ordner = cache_pfad(ordner)
    raus = {}
    if not os.path.isdir(ordner):
        return raus
    for name in sorted(os.listdir(ordner)):
        if not name.endswith('.json'):
            continue
        pfad = os.path.join(ordner, name)
        try:
            with open(pfad, encoding='utf-8') as f:
                eintrag = json.load(f)
        except (ValueError, OSError):
            continue      # kaputte Datei: beim naechsten Lauf neu holen
        if eintrag.get('id'):
            raus[eintrag['id']] = eintrag
    return raus


def cache_schreiben(eintrag, ordner=None):
    ordner = cache_pfad(ordner)
    ordner_anlegen(ordner)
    pfad = os.path.join(ordner, '%s.json' % eintrag['id'])
    # Erst daneben schreiben, dann umbenennen: Ein Abbruch mitten im
    # Schreiben liesse sonst eine halbe Datei zurueck, die beim naechsten
    # Lauf als "schon geholt" gilt.
    vorlaeufig = pfad + '.neu'
    with open(vorlaeufig, 'w', encoding='utf-8') as f:
        json.dump(eintrag, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(vorlaeufig, pfad)
    return pfad


# --- Sektorlayout ---------------------------------------------------------
#
# Wie viele Splitpunkte eine Strecke hat, steht nirgends -- nur die Zeiten
# stehen da, in einer festen Zahl Felder, unbenutzte als 0. Auch mittendrin:
# eine Vierteilung kann als `12.3, 9.4, 7.4, 0, 7.8` ankommen. Deshalb wird
# das Layout als Menge belegter *Positionen* bestimmt und nicht gezaehlt.

def session_layout(session):
    """Die belegten Sektorpositionen einer Session (1-basiert) oder None.

    Genommen wird die Runde mit den meisten belegten Sektoren -- das ist
    eine vollstaendige Runde, sofern die Session ueberhaupt eine hat.
    Sessions, die nur aus Ein- und Ausfahrrunden bestehen, geben None
    zurueck: Aus einem Teilstueck laesst sich das Layout nicht ablesen.
    """
    beste = None
    for runde in session.get('runden', []):
        belegt = tuple(i + 1 for i, wert in enumerate(runde['sektoren'])
                       if wert > 0)
        if len(belegt) > 1 and (beste is None or len(belegt) > len(beste)):
            beste = belegt
    return beste


def layout_bestimmen(sessions):
    """Das gueltige Layout einer Strecke und die Sessions, die abweichen.

    Verschiebt RaceBox die Splitpunkte einer Strecke in seiner Datenbank,
    sind alte und neue Sektorzeiten nicht mehr vergleichbar -- die
    theoretische Runde waere stillschweigend falsch. Erkennbar ist das
    daran, dass zwei Sessions derselben Strecke verschiedene Positionen
    belegen. Gueltig ist dann das Layout der *neuesten* Session: Das ist
    die Einteilung, die beim naechsten Mal wieder gilt.

    Return (layout, passend, abweichend). `layout` ist None, wenn keine
    einzige Session eine vollstaendige Runde hat.
    """
    nach_datum = sorted(sessions, key=sortier_schluessel, reverse=True)

    # Steht die Streckenkonfiguration in den Daten, muss nichts erraten
    # werden: Die Zahl der Splitlinien sagt, wie viele Sektoren es gibt, und
    # ihre Kennung sagt, ob zwei Sessions dieselbe Einteilung gefahren sind.
    for session in nach_datum:
        if session.get('konfig_id') and session.get('splits') is not None:
            gueltig = session['konfig_id']
            layout = tuple(range(1, session['splits'] + 2))
            passend, abweichend = [], []
            for andere in sessions:
                eigene = andere.get('konfig_id')
                (passend if not eigene or eigene == gueltig
                 else abweichend).append(andere)
            return layout, passend, abweichend

    layout = None
    for session in nach_datum:
        layout = session_layout(session)
        if layout:
            break
    if not layout:
        return None, [], list(sessions)

    passend, abweichend = [], []
    for session in sessions:
        eigen = session_layout(session)
        # Sessions ohne eigenes Layout (nur Teilrunden) passen, solange ihre
        # belegten Positionen im gueltigen Layout vorkommen.
        if eigen is None:
            belegt = set()
            for runde in session.get('runden', []):
                belegt |= {i + 1 for i, w in enumerate(runde['sektoren'])
                           if w > 0}
            (passend if belegt <= set(layout) else abweichend).append(session)
        else:
            (passend if eigen == layout else abweichend).append(session)
    return layout, passend, abweichend


def sortier_schluessel(session):
    """Sessions chronologisch: Tag, dann Turn, dann Startzeit."""
    return (session.get('datum', ''), session.get('turn', 0),
            session.get('startzeit', ''))


# --- Runden und Sektoren --------------------------------------------------

def runden(sessions, layout):
    """Alle Runden als flache Liste, mit Herkunft und Vollstaendigkeit.

    Eine Runde ist *vollstaendig*, wenn jede Position des Layouts belegt
    ist. Alles andere ist eine Teilrunde -- typisch die Ein- und
    Ausfahrrunde, bei der nur das Stueck zwischen zwei Splitpunkten
    gemessen wurde.
    """
    raus = []
    for session in sorted(sessions, key=sortier_schluessel):
        for runde in session.get('runden', []):
            sektoren = runde['sektoren']
            werte = {}
            for pos in layout:
                wert = sektoren[pos - 1] if pos <= len(sektoren) else 0.0
                if wert > 0:
                    werte[pos] = wert
            # `stimmig` wird beim Holen bestimmt, solange die Sektoren noch
            # unangetastet sind. Hier nachzurechnen ginge schief: Was im
            # Cache liegt, ist bereits bereinigt -- die Randsektoren der
            # Ein- und Ausfahrrunde sind heraus, und dann geht die Summe
            # natuerlich nicht mehr auf. Genau so wurden einmal zwei
            # Runden je Turn als unstimmig gemeldet, die voellig in Ordnung
            # waren. Fehlt die Angabe, gilt die Runde als stimmig.
            stimmig = runde.get('stimmig', True)
            vollstaendig = len(werte) == len(layout)
            # Der gerechnete Schlusssektor ist nur dann eine Aussage, wenn
            # die Summe der Runde aufgeht -- also bei einer vollstaendigen
            # Runde. Sonst ist er die Differenz zu einer Rundenzeit, von
            # der wir gerade nicht wissen, was sie umfasst.
            abgeleitet = runde.get('abgeleitet')
            if abgeleitet is None and session.get('quelle') == 'json':
                abgeleitet = len(runde['sektoren'])   # aeltere Eintraege
            if not vollstaendig and abgeleitet in werte:
                del werte[abgeleitet]
            raus.append({
                'session': session,
                'nr': runde['nr'],
                'zeit': runde['zeit'],
                'sektoren': werte,
                # Wie viel Zeit die Sektoren nicht erklaeren -- gerechnet
                # auf den urspruenglichen Werten, nicht auf den hier
                # aussortierten. Sonst zeigt die Splitansicht eine
                # Luecke, die sie selbst gerissen hat.
                'luecke': round(runde['zeit'] - sum(sektoren), 3),
                'vollstaendig': vollstaendig,
                'stimmig': stimmig,
            })
    return raus


def bester_sektor(alle_runden, pos, nur_vollstaendig=False):
    """Die schnellste Zeit fuer eine Sektorposition, mit ihrer Herkunft.

    Runden, deren Sektoren nicht ihre Rundenzeit ergeben, bleiben aussen
    vor. Eine Golden Lap aus solchen Teilen waere schneller als alles je
    Gefahrene und trotzdem falsch -- und faellt genau deshalb erst auf,
    wenn jemand hinschaut.
    """
    treffer = [r for r in alle_runden if pos in r['sektoren'] and r['stimmig']
               and (r['vollstaendig'] or not nur_vollstaendig)]
    if not treffer:
        return None
    return min(treffer, key=lambda r: (r['sektoren'][pos],
                                       sortier_schluessel(r['session'])))


def sektor_rangliste(alle_runden, pos, anzahl=3):
    """Die schnellsten Zeiten einer Sektorposition, beste zuerst."""
    treffer = [r for r in alle_runden if pos in r['sektoren'] and r['stimmig']]
    treffer.sort(key=lambda r: (r['sektoren'][pos],
                                sortier_schluessel(r['session'])))
    return treffer[:anzahl]


def theoretische_runde(alle_runden, layout, nur_vollstaendig=False):
    """Summe der Sektorbestzeiten und woraus sie besteht.

    Return (zeit, [(position, runde), ...]) oder (None, []), wenn zu einer
    Position nichts vorliegt.
    """
    teile = []
    for pos in layout:
        beste = bester_sektor(alle_runden, pos, nur_vollstaendig)
        if beste is None:
            return None, []
        teile.append((pos, beste))
    return sum(r['sektoren'][pos] for pos, r in teile), teile


def beste_runden(alle_runden, anzahl=3):
    """Die schnellsten vollstaendig gemessenen Runden, schnellste zuerst."""
    treffer = [r for r in alle_runden if r['vollstaendig']]
    treffer.sort(key=lambda r: (r['zeit'], sortier_schluessel(r['session'])))
    return treffer[:anzahl]


def beste_runde(alle_runden):
    """Die schnellste vollstaendig gemessene Runde."""
    treffer = beste_runden(alle_runden, 1)
    return treffer[0] if treffer else None


def mittelwert(werte):
    """Der Median -- unempfindlich gegen einzelne Ausreisser."""
    geordnet = sorted(werte)
    if not geordnet:
        return None
    mitte = len(geordnet) // 2
    if len(geordnet) % 2:
        return geordnet[mitte]
    return (geordnet[mitte - 1] + geordnet[mitte]) / 2.0


# Eine Sektorzeit unter der Haelfte des Ueblichen ist keine Fahrleistung
# mehr. Der Wert urteilt bewusst nicht ueber den Fahrer: Die beste
# Sektorzeit einer Strecke liegt bei neunzig bis fuenfundneunzig Prozent
# des Medians derselben Position, also mit riesigem Abstand darueber.
# Gemeint sind Faelle wie dieser, am echten Konto gefunden: 0.27 Sekunden
# fuer einen Sektor, in dem sonst 15 Sekunden stehen. Bei 100 km/h sind
# das sieben Meter.
#
# Zustande kommt so etwas nicht durch eine falsche Rechnung -- die Summe
# der Sektoren ergibt die Rundenzeit auf die Tausendstel -- sondern durch
# eine verpasste Splitueberfahrt: Sie verschiebt Zeit aus einem Sektor in
# den nachbarschaftlichen. Die Rundenzeit bleibt richtig, die Aufteilung
# nicht. Dieselbe Fehlmessung steht im CSV wie im JSON; sie ist nicht
# wegzurechnen, nur zu erkennen.
SEKTOR_SCHWELLE = 0.5


def unplausible_sektoren_entfernen(alle_runden, layout):
    """Sektorzeiten aussortieren, die keine Messung mehr sein koennen.

    Verglichen wird mit dem Median derselben Sektorposition ueber alle
    vollstaendigen Runden. Der Median ist dafuer das richtige Mass, weil
    ein einzelner Ausreisser ihn nicht zieht -- anders als der Mittelwert.

    Return die Zahl der aussortierten Zeiten.
    """
    entfernt = 0
    for pos in layout:
        werte = [r['sektoren'][pos] for r in alle_runden
                 if r['vollstaendig'] and pos in r['sektoren']]
        median = mittelwert(werte)
        if median is None:
            continue
        schwelle = median * SEKTOR_SCHWELLE
        for runde in alle_runden:
            if pos in runde['sektoren'] and runde['sektoren'][pos] < schwelle:
                del runde['sektoren'][pos]
                runde['vollstaendig'] = False
                entfernt += 1
    return entfernt


def auswerten(sessions, layout):
    """Alle Kennzahlen einer Strecke-Fahrzeug-Kombination."""
    alle = runden(sessions, layout)
    aussortiert = unplausible_sektoren_entfernen(alle, layout)
    theo, teile = theoretische_runde(alle, layout)
    streng, streng_teile = theoretische_runde(alle, layout, True)
    best = beste_runde(alle)

    # Je Turn und je Tag dieselbe Rechnung ueber einer kleineren Menge --
    # so sieht man, ob ein einzelner Turn schon nah dran war oder ob die
    # gute theoretische Runde aus Teilen von drei Fahrtagen besteht.
    # Wichtig: beides aus `alle` heraus gruppieren und nicht neu berechnen.
    # Wer hier `runden(...)` noch einmal aufruft, bekommt frische Runden --
    # und damit die aussortierten Sektorzeiten zurueck, die oben gerade
    # verworfen wurden. Genau daran hing eine Turn-Theo von 24.85 gegen
    # eine Bestrunde von 1:03.
    nach_session = {}
    for runde in alle:
        nach_session.setdefault(runde['session']['id'], []).append(runde)
    je_turn = []
    for session in sorted(sessions, key=sortier_schluessel):
        eigene = nach_session.get(session['id'], [])
        t, _ = theoretische_runde(eigene, layout)
        je_turn.append({'session': session, 'best': beste_runde(eigene),
                        'theo': t, 'runden': len(eigene)})

    nach_tag = {}
    for runde in alle:
        nach_tag.setdefault(runde['session'].get('datum', '?'), []).append(runde)
    tage = {}
    for session in sessions:
        tage.setdefault(session.get('datum', '?'), []).append(session)
    je_tag = []
    for datum in sorted(tage):
        eigene = nach_tag.get(datum, [])
        t, _ = theoretische_runde(eigene, layout)
        je_tag.append({'datum': datum, 'turns': len(tage[datum]),
                       'best': beste_runde(eigene), 'theo': t,
                       'runden': len(eigene)})

    # Der beste einzelne Turn ist die ehrlichere Zahl als die Golden Lap:
    # Sie sagt, was an einem Nachmittag zusammenkam, ohne Sektoren aus vier
    # Fahrtagen zusammenzusetzen. RaceBox zeigt genau diese je Session an.
    mit_theo = [t for t in je_turn if t['theo']]
    bester_turn = min(mit_theo, key=lambda t: t['theo']) if mit_theo else None

    top = beste_runden(alle)
    return {
        'sessions': sessions,
        'alle_runden': alle,
        'top_runden': top,
        'bester_turn': bester_turn,
        'anzahl_runden': len(alle),
        'anzahl_vollstaendig': sum(1 for r in alle if r['vollstaendig']),
        'anzahl_unstimmig': sum(1 for r in alle if not r['stimmig']),
        'aussortiert': aussortiert,
        # Wie weit die drittbeste Runde von der besten weg ist. Eine kleine
        # Streuung heisst: die Bestzeit war kein Ausreisser, du kannst sie.
        'streuung': (mittelwert([r['zeit'] for r in top[1:]]) - top[0]['zeit'])
                    if len(top) > 1 else None,
        'tage': len(tage),
        'best': best,
        'theo': theo,
        'theo_teile': teile,
        # Wie viele Sektoren der Golden Lap aus einer Ein- oder
        # Ausfahrrunde stammen. Nicht dasselbe wie `streng_weicht_ab`:
        # Trifft eine Teilrunde die Bestzeit einer vollstaendigen Runde
        # genau, aendert sich die Zahl nicht -- gebaut ist die Golden Lap
        # trotzdem aus ihr, und das gehoert markiert.
        'aus_teilrunden': sum(1 for _, r in teile if not r['vollstaendig']),
        # Je Sektorposition die Bestzeit aus einer *vollstaendigen* Runde.
        # Getrennt von `theo_streng_teile`, weil das leer bleibt, sobald zu
        # einer einzigen Position nichts Vollstaendiges vorliegt -- die
        # uebrigen Positionen haetten dann trotzdem eine Zahl, und die
        # gehoert gezeigt.
        'streng_je_sektor': {pos: bester_sektor(alle, pos, True)
                             for pos in layout},
        'theo_streng': streng,
        'theo_streng_teile': streng_teile,
        # Nur wenn sich beide unterscheiden, ist die strenge Zahl eine
        # eigene Aussage -- sonst haengt keine Bestzeit an einer Teilrunde.
        'streng_weicht_ab': (streng is not None and theo is not None
                             and abs(streng - theo) > 0.0005),
        'delta': (theo - best['zeit']) if (theo and best) else None,
        # Eine theoretische Runde liegt erfahrungsgemaess ein bis drei
        # Prozent unter der Bestrunde. Fuenfzehn Prozent und mehr heissen
        # fast immer: Eine Sektorzeit gehoert dort nicht hin. Das gehoert
        # gesagt und nicht stillschweigend angezeigt.
        'unglaubwuerdig': bool(theo and best and theo < best['zeit'] * 0.85),
        'rangliste': {pos: sektor_rangliste(alle, pos) for pos in layout},
        'je_turn': je_turn,
        'je_tag': je_tag,
    }


# --- Strecken und Fahrzeuge -----------------------------------------------
#
# Sektoren verschiedener Fahrzeuge zu mischen ergaebe eine theoretische
# Runde, die so niemand faehrt. Gerechnet wird deshalb je Strecke *und*
# Fahrzeug. Das Layout dagegen haengt an der Strecke allein und wird ueber
# alle Fahrzeuge zusammen bestimmt -- sonst haette ein Fahrzeug mit nur
# einer Ausfahrrunde kein Layout.

# Ab Werk wird nichts ausgeblendet. Welche Strecke ein Uebungsplatz ist,
# weiss nur der, der dort gefahren ist -- eingebaute Eigennamen waeren fuer
# jeden anderen Unsinn und im ungluecklichen Fall eine Strecke, die er
# wirklich faehrt. Die Liste ist deshalb eine Datei neben dem Skript.
AUSBLENDEN_VORGABE = []

AUSBLENDEN_VORLAGE = """\
# Strecken, die in der Uebersicht nicht auftauchen sollen -- eine je Zeile.
# Gedacht fuer Uebungsplaetze und Testrunden, die die Sicht auf die echten
# Rennstrecken verstellen.
#
# `*` ist ein Platzhalter, Gross- und Kleinschreibung ist egal. Zeilen mit
# `#` sind Kommentar. Beispiele -- das `#` davor entfernen, dann gelten sie:
#
#   Kartbahn*
#   Parkplatz hinterm Baumarkt
#
# Dasselbe geht vom Terminal aus, dann landet die Zeile hier:
#
#   python3 rb-golden-lap.py --ausblenden "Kartbahn*"
#
# Ausgeblendete Strecken werden unter der Uebersicht benannt, verschwinden
# also nicht spurlos; --alle zeigt sie fuer einen Lauf wieder vollstaendig.
"""


FAHRZEUG_VORLAGE = """\
# Fahrzeuge, die in der Uebersicht auftauchen sollen -- eine je Zeile.
# Steht hier nichts, werden alle gezeigt.
#
# `*` ist ein Platzhalter, Gross- und Kleinschreibung ist egal. Zeilen mit
# `#` sind Kommentar. Beispiel -- das `#` davor entfernen, dann gilt es:
#
#   Yamaha*
#
# --alle zeigt fuer einen Lauf wieder jedes Fahrzeug.
"""


def fahrzeugfilter_lesen(ordner=None):
    """Die Fahrzeugliste aus der Datei neben dem Skript.

    Wer sein Konto mit anderen teilt, hat schnell acht Fahrzeuge in der
    Uebersicht und sucht seines darin. Leer heisst: alle zeigen.
    """
    pfad = os.path.join(ordner or ORDNER, 'fahrzeuge')
    if not os.path.exists(pfad):
        try:
            with open(pfad, 'w', encoding='utf-8') as f:
                f.write(FAHRZEUG_VORLAGE)
        except OSError:
            pass
        return []
    muster = []
    with open(pfad, encoding='utf-8') as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile and not zeile.startswith('#'):
                muster.append(zeile)
    return muster


def fahrzeuge_filtern(strecken, muster):
    """Nur die passenden Fahrzeuge stehen lassen. Return die Zahl der Rest."""
    if not muster:
        return 0
    versteckt = set()
    for eintrag in strecken:
        bleiben = []
        for z in eintrag['fahrzeuge']:
            if ist_ausgeblendet(z['fahrzeug'], muster):
                bleiben.append(z)
            else:
                versteckt.add(z['fahrzeug'])
        eintrag['fahrzeuge'] = bleiben
    return len(versteckt)


def ausblenden_pfad(ordner=None):
    return os.path.join(ordner or ORDNER, 'ausblenden')


def ausblenden_lesen(ordner=None):
    """Die Ausblendliste aus der Datei neben dem Skript.

    Gibt es sie noch nicht, wird sie angelegt -- leer, aber mit Erklaerung
    darin. Eine Datei, die dasteht, findet man; eine Einstellung, von der
    nur das README weiss, nicht. Scheitert das Anlegen, ist das kein Grund
    abzubrechen: Gelesen wird dann eben nichts.
    """
    pfad = ausblenden_pfad(ordner)
    if not os.path.exists(pfad):
        try:
            with open(pfad, 'w', encoding='utf-8') as f:
                f.write(AUSBLENDEN_VORLAGE)
        except OSError:
            pass
        return list(AUSBLENDEN_VORGABE)
    muster = []
    with open(pfad, encoding='utf-8') as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile and not zeile.startswith('#'):
                muster.append(zeile)
    return muster


def ausblenden_ergaenzen(neue, ordner=None):
    """Muster in die Ausblendliste aufnehmen. Return die vollstaendige Liste.

    Doppeltes wird nicht noch einmal geschrieben -- wer denselben Aufruf
    zweimal absetzt, soll keine zweite Zeile bekommen.
    """
    pfad = ausblenden_pfad(ordner)
    vorhanden = ausblenden_lesen(ordner)
    dazu = [m for m in neue if m not in vorhanden]
    if dazu:
        ordner_anlegen(os.path.dirname(pfad) or '.')
        with open(pfad, 'a', encoding='utf-8') as f:
            f.write('\n'.join(dazu) + '\n')
    for m in neue:
        melde('  %s %s' % (m, 'aufgenommen' if m in dazu else 'stand schon da'))
    melde('  Ausblendliste: %s' % pfad)
    return vorhanden + dazu


def ist_ausgeblendet(strecke, muster):
    return any(fnmatch.fnmatch(strecke.lower(), m.lower()) for m in muster)


def doppelte_entfernen(sessions):
    """Dieselbe Aufzeichnung unter zwei Kennungen zusammenfassen.

    Am echten Konto liegen Sessions doppelt: gleicher Tag, gleiche
    Startzeit, gleicher Turn, gleiches Fahrzeug -- aber zwei Kennungen.
    Dann steht dieselbe Runde zweimal in der Rangliste, und die
    zweitbeste Runde ist in Wahrheit die beste noch einmal. Behalten wird
    die Fassung mit den meisten Runden; die andere ist ein Teilupload.

    Return (bereinigt, Zahl der entfernten).
    """
    nach_lauf, paare = {}, []
    for session in sessions:
        schluessel = (session.get('strecke'), session.get('konfiguration'),
                      session.get('fahrzeug'), session.get('datum'),
                      session.get('startzeit'), session.get('turn'))
        vorher = nach_lauf.get(schluessel)
        if vorher is None:
            nach_lauf[schluessel] = session
            continue
        # Behalten wird die Fassung mit den meisten Runden; welche
        # ausgeschieden ist, wird gemerkt -- nur so laesst sich spaeter
        # nachsehen, ob die beiden ueberhaupt dasselbe enthalten.
        laenger, kuerzer = sorted(
            (vorher, session), key=lambda x: -len(x.get('runden', [])))
        nach_lauf[schluessel] = laenger
        paare.append((laenger, kuerzer))
    return list(nach_lauf.values()), paare


def doppelte_vergleichen(paare, ordner=None):
    """Die abgelegten Exporte zweier doppelter Sessions gegeneinander halten.

    Dass eine Fassung mehr Runden fuehrt, ist die eine Frage. Die andere
    ist, ob sie in den gemeinsamen Runden dasselbe sagt -- eine kuerzere
    Fassung mit *abweichenden* Zeiten waere kein Teilupload, sondern eine
    zweite Messung, und dann waere das Zusammenfassen falsch.

    Zugeordnet wird ueber die Rundenzeit, nicht ueber die Nummer: Faengt
    ein Teilupload spaeter an, zaehlt er ab eins weiter, und Position
    gegen Position gehalten waere jede Runde eine Abweichung.

    Return eine Liste von Befunden, einer je Paar.
    """
    ordner = ordner or CSV_ORDNER
    befunde = []
    for laenger, kuerzer in paare:
        eintrag = {
            'strecke': laenger.get('strecke', ''),
            'konfiguration': laenger.get('konfiguration', ''),
            'fahrzeug': laenger.get('fahrzeug', ''),
            'datum': laenger.get('datum', ''),
            'startzeit': laenger.get('startzeit', ''),
            'lang': laenger, 'kurz': kuerzer,
            'fehlend': [], 'nur_lang': [], 'nur_kurz': [],
            'abweichend': [], 'verschoben': [], 'gleich': 0,
        }
        runden = {}
        for rolle, session in (('lang', laenger), ('kurz', kuerzer)):
            pfad = export_pfad(session.get('id', ''), ordner)
            try:
                with open(pfad, encoding='utf-8', errors='replace') as f:
                    runden[rolle] = kopf_lesen(f.read())['runden']
            except OSError:
                eintrag['fehlend'].append(pfad)
        if eintrag['fehlend']:
            befunde.append(eintrag)
            continue

        offen = list(runden['kurz'])
        for lang_runde in runden['lang']:
            passend = [k for k in offen
                       if abs(k['zeit'] - lang_runde['zeit']) < 0.0005]
            if not passend:
                eintrag['nur_lang'].append(lang_runde)
                continue
            kurz_runde = passend[0]
            offen.remove(kurz_runde)
            if lang_runde['sektoren'] != kurz_runde['sektoren']:
                eintrag['abweichend'].append((lang_runde, kurz_runde))
            else:
                eintrag['gleich'] += 1
            if lang_runde['nr'] != kurz_runde['nr']:
                eintrag['verschoben'].append((lang_runde, kurz_runde))
        eintrag['nur_kurz'] = offen
        befunde.append(eintrag)
    return befunde


def strecken_bauen(sessions, ausblenden=()):
    """Alle Sessions zu Strecken und darin zu Fahrzeugen ordnen.

    Return (strecken, ausgeblendet, doppelte). `strecken` ist nach dem
    juengsten Fahrtag sortiert -- was zuletzt gefahren wurde, steht oben.
    `doppelte` sind die Paare, von denen nur eine Fassung gezaehlt wurde.
    """
    sessions, doppelte_paare = doppelte_entfernen(sessions)
    nach_strecke = {}
    ausgeblendet = {}
    for session in sessions:
        name = session.get('strecke') or '(ohne Strecke)'
        if ist_ausgeblendet(name, ausblenden):
            ausgeblendet.setdefault(name, []).append(session)
            continue
        nach_strecke.setdefault((name, session.get('konfiguration', '')),
                                []).append(session)

    strecken = []
    for (name, konfig), eigene in nach_strecke.items():
        layout, passend, abweichend = layout_bestimmen(eigene)
        eintrag = {
            'strecke': name,
            'konfiguration': konfig,
            'layout_nr': None,
            'layout': layout,
            # Wie viele Sektorfelder die Strecke ueberhaupt hat. Interessant,
            # weil RaceBox Luecken laesst: eine Vierteilung kommt als fuenf
            # Felder mit einer Null in der Mitte an. Ob das an der Strecke
            # haengt oder am Format, sieht man erst, wenn man mehrere
            # Strecken nebeneinander legt.
            'felder': max([len(r['sektoren']) for s in eigene
                           for r in s.get('runden', [])] or [0]),
            'abweichend': abweichend,
            'letzte': max(s.get('datum', '') for s in eigene),
            'fahrzeuge': [],
        }
        if layout:
            nach_fahrzeug = {}
            for session in passend:
                nach_fahrzeug.setdefault(
                    session.get('fahrzeug', 'ohne Fahrzeug'), []).append(session)
            for fahrzeug in sorted(nach_fahrzeug):
                zahlen = auswerten(nach_fahrzeug[fahrzeug], layout)
                zahlen['fahrzeug'] = fahrzeug
                eintrag['fahrzeuge'].append(zahlen)
            # Das schnellste Fahrzeug zuerst, damit die Uebersicht mit der
            # Zeile beginnt, die die Strecke kennzeichnet.
            eintrag['fahrzeuge'].sort(
                key=lambda z: (z['best']['zeit'] if z['best'] else 1e9))
        strecken.append(eintrag)

    # Hat eine Strecke mehrere Layouts, bekommt jedes eine Nummer. In der
    # Uebersicht steht dann `Bergring (2)` statt
    # `Bergring / Grand Prix` -- der Name bleibt lesbar, und welches
    # Layout gemeint ist, steht in der Fusszeile und in der Detailansicht.
    nach_name = {}
    for eintrag in strecken:
        nach_name.setdefault(eintrag['strecke'], []).append(eintrag)
    for gleichnamige in nach_name.values():
        if len(gleichnamige) < 2:
            continue
        for nr, eintrag in enumerate(
                sorted(gleichnamige, key=lambda e: e['konfiguration']), 1):
            eintrag['layout_nr'] = nr

    strecken.sort(key=lambda e: e['letzte'], reverse=True)
    return strecken, ausgeblendet, doppelte_paare


# --- Ausgabe --------------------------------------------------------------

def sektor_nummer(layout, pos):
    """Die Nummer, unter der ein Sektor angezeigt wird.

    Intern zaehlt die *Position* im Sektorfeld, weil RaceBox Luecken laesst
    (`12.3, 9.4, 7.4, 0, 7.8` ist eine Vierteilung). Zu sehen bekommt man
    aber die vierte und nicht die fuenfte -- so, wie die App sie zeigt.
    """
    return layout.index(pos) + 1


def herkunft(runde, kurz=False):
    """Woher eine Runde stammt: `2026-08-10 Turn 3 (19:16) Runde 7`."""
    s = runde['session']
    if kurz:
        return '%s T%d R%d' % (s.get('datum', '?'), s.get('turn', 0),
                               runde['nr'])
    return '%s Turn %d (%s) Runde %d' % (
        s.get('datum', '?'), s.get('turn', 0), s.get('startzeit', '?'),
        runde['nr'])


def kurzname(eintrag):
    """`Bergring (2)` -- fuer die Uebersicht, wo Breite knapp ist."""
    if eintrag.get('layout_nr'):
        return '%s (%d)' % (eintrag['strecke'], eintrag['layout_nr'])
    return eintrag['strecke']


def langname(eintrag):
    """`Bergring / Grand Prix` -- fuer die Detailansicht."""
    if eintrag['konfiguration']:
        return '%s / %s' % (eintrag['strecke'], eintrag['konfiguration'])
    return eintrag['strecke']


def anzahl(zahl, einzahl, mehrzahl):
    """`1 Turn` statt `1 Turns`."""
    return '%d %s' % (zahl, einzahl if zahl == 1 else mehrzahl)


def zeige_uebersicht(strecken, ausgeblendet, ordner=None, versteckt=0,
                     doppelte=()):
    """Die Startseite: je Strecke ein Block, je Fahrzeug eine Zeile.

    Fuenf Zahlen, und jede beantwortet eine andere Frage. Die
    **Bestrunde** ist gefahren worden. Die **Streuung** sagt, wie weit die
    drittbeste davon weg ist -- klein heisst, du kannst die Zeit, gross
    heisst, sie war ein Ausreisser. Die **Turn-Theo** ist die beste
    theoretische Runde innerhalb *eines* Turns: dieselben Reifen, dasselbe
    Wetter, dieselbe halbe Stunde, und das, was RaceBox je Session zeigt.
    Die **Golden Lap** setzt die besten Sektoren ueber alle Turns und
    Fahrtage zusammen -- die Obergrenze, keine Prognose. Das **Delta** ist,
    was zwischen Bestrunde und Golden Lap liegt.
    """
    turns = sum(len(z['sessions']) for e in strecken for z in e['fahrzeuge'])
    runden_gesamt = sum(z['anzahl_runden']
                        for e in strecken for z in e['fahrzeuge'])
    breite = 88
    melde()
    melde('%d Strecke(n), %d Turn(s), %d Runde(n)'
          % (len(strecken), turns, runden_gesamt))
    melde()
    melde('  %-4s%-24s%7s %10s %9s %11s %12s%8s'
          % ('#', 'Strecke / Fahrzeug', 'Runden', 'Bestrunde', 'Streuung',
             'Turn-Theo', 'Golden Lap', 'Delta'))
    for eintrag_nr, e in enumerate(strecken, 1):
        melde('  ' + '-' * breite)
        # Grossbuchstaben: Der Streckenname ist die Ueberschrift des
        # Blocks, und in einer Kommandozeile gibt es keine Fettschrift.
        melde('  %-4d%s' % (eintrag_nr, kurzname(e).upper()))
        if not e['fahrzeuge']:
            melde('      %s' % 'keine vollstaendige Runde')
        for z in e['fahrzeuge']:
            # `(TR)` steht *links* vor der Zahl und nicht dahinter: Die
            # Zeit bleibt rechtsbuendig in ihrer Spalte, und die Tabelle
            # rutscht nicht, nur weil eine Zeile markiert ist.
            golden = zeit_text(z['theo'])
            if z['aus_teilrunden']:
                golden = '(TR) ' + golden
            melde('      %-22s%7d %10s %9s %11s %12s%8s%s'
                  % (z['fahrzeug'][:22], z['anzahl_runden'],
                     zeit_text(z['best']['zeit'] if z['best'] else None),
                     delta_text(z['streuung']),
                     zeit_text(z['bester_turn']['theo']
                               if z['bester_turn'] else None),
                     golden, delta_text(z['delta']),
                     '  (!)' if z['unglaubwuerdig'] else ''))
    melde('  ' + '-' * breite)
    melde()
    fussnoten(strecken, ausgeblendet, ordner, versteckt, doppelte)


def fussnoten(strecken, ausgeblendet, ordner, versteckt=0, doppelte=()):
    """Was unter der Tabelle steht: Warnungen, Layouts, Speicherort."""
    fraglich = [z for e in strecken for z in e['fahrzeuge']
                if z['unglaubwuerdig']]
    if fraglich:
        melde('  (!) Diese Golden Lap liegt mehr als fuenfzehn Prozent '
              'unter der Bestrunde.')
        melde('      Wahrscheinlich geht eine Sektorzeit ein, die dort '
              'nicht hingehoert.')
        melde('      Die Detailansicht zeigt, welche.')
        melde()
    teilrunden = [z for e in strecken for z in e['fahrzeuge']
                  if z['aus_teilrunden']]
    if teilrunden:
        melde('  (TR) In dieser Golden Lap steckt eine Sektorzeit aus einer '
              'Ein- oder Ausfahrrunde.')
        melde('       Gemessen ist sie trotzdem zwischen denselben zwei '
              'Splitpunkten. Die Detailansicht')
        melde('       zeigt, welche -- und was ohne sie uebrig bliebe.')
        melde()
    nur_json = sorted({s['id'] for e in strecken for z in e['fahrzeuge']
                       for s in z['sessions'] if s.get('quelle') != 'json+csv'})
    if nur_json:
        melde('  %d Session(s) ohne Rundenzeilen im Export -- dort gelten '
              'die Runden aus dem JSON,' % len(nur_json))
        melde('      samt gerechnetem Schlusssektor. Das ist die '
              'Rueckfallebene, nicht der Normalfall.')
        melde()
    aussortiert = sum(z['aussortiert'] for e in strecken
                      for z in e['fahrzeuge'])
    if aussortiert:
        melde('  %d Sektorzeit(en) aussortiert: weniger als die Haelfte des '
              'Ueblichen an derselben' % aussortiert)
        melde('      Stelle der Strecke. Dahinter steckt eine verpasste '
              'Splitueberfahrt, keine Rundenzeit.')
        melde()
    if doppelte:
        melde('  %d Session(s) lagen doppelt im Konto -- gleicher Tag, '
              'gleiche Startzeit, gleicher' % len(doppelte))
        melde('      Turn, zwei Kennungen. Gezaehlt wurde die Fassung mit '
              'den meisten Runden; --doppelte')
        melde('      vergleicht die beiden Exporte miteinander.')
        melde()
    if versteckt:
        melde('  %d weitere(s) Fahrzeug(e) nicht gezeigt -- Datei '
              '`fahrzeuge` oder --alle' % versteckt)
    mehrere = [e for e in strecken if e.get('layout_nr')]
    if mehrere:
        melde('  Layouts: %s'
              % ',  '.join('%s = %s' % (kurzname(e),
                                        e['konfiguration'] or 'ohne Namen')
                           for e in sorted(mehrere,
                                           key=lambda e: (e['strecke'],
                                                          e['layout_nr']))))
    if ausgeblendet:
        melde('  Ausgeblendet: %s -- mit --alle wieder sichtbar'
              % ', '.join('%s (%d)' % (k, len(v))
                          for k, v in sorted(ausgeblendet.items())))
    if ordner:
        melde('  Daten: %s' % ordner)


def zeige_doppelte(befunde):
    """Was der Vergleich der doppelt abgelegten Exporte ergeben hat."""
    if not befunde:
        melde('Keine doppelt abgelegten Sessions gefunden.')
        return
    melde()
    melde('=' * 72)
    melde('%s doppelt im Konto' % anzahl(len(befunde), 'Session', 'Sessions'))
    melde('=' * 72)
    melde('Gleicher Tag, gleiche Startzeit, gleicher Turn, gleiches '
          'Fahrzeug -- zwei Kennungen.')
    melde('Verglichen werden die abgelegten Originalexporte, Runde fuer '
          'Runde ueber die Rundenzeit.')

    einig = 0
    for b in befunde:
        melde()
        melde('%s%s -- %s, %s %s'
              % (b['strecke'],
                 ' / %s' % b['konfiguration'] if b['konfiguration'] else '',
                 b['fahrzeug'], b['datum'], b['startzeit']))
        if b['fehlend']:
            for pfad in b['fehlend']:
                melde('    Export fehlt: %s' % pfad)
            melde('    Ohne die Originalexporte laesst sich nichts '
                  'vergleichen -- einmal ohne --nur-cache laufen.')
            continue
        melde('    %s  %3d Runde(n)'
              % (b['lang']['id'], len(b['lang'].get('runden', []))))
        melde('    %s  %3d Runde(n)   (nicht gezaehlt)'
              % (b['kurz']['id'], len(b['kurz'].get('runden', []))))
        melde('    %d Runde(n) in beiden, Sektor fuer Sektor gleich'
              % b['gleich'])
        for lang, kurz in b['abweichend']:
            melde('    Runde %d/%d (%s): andere Sektoren'
                  % (lang['nr'], kurz['nr'], zeit_text(lang['zeit'])))
            melde('        %s' % ', '.join('%.3f' % v
                                           for v in lang['sektoren']))
            melde('        %s' % ', '.join('%.3f' % v
                                           for v in kurz['sektoren']))
        for runde in b['nur_lang']:
            melde('    Runde %d (%s): nur in der gezaehlten Fassung'
                  % (runde['nr'], zeit_text(runde['zeit'])))
        for runde in b['nur_kurz']:
            melde('    Runde %d (%s): nur in der *nicht* gezaehlten Fassung'
                  % (runde['nr'], zeit_text(runde['zeit'])))
        if b['verschoben']:
            melde('    %d Runde(n) tragen dieselbe Zeit unter einer anderen '
                  'Nummer -- ein Teilupload,' % len(b['verschoben']))
            melde('        der spaeter anfaengt und wieder bei eins zaehlt.')
        if not b['abweichend'] and not b['nur_kurz']:
            einig += 1

    melde()
    if einig == len(befunde):
        melde('Ergebnis: Keine der kuerzeren Fassungen enthaelt eine Runde '
              'oder eine Sektorzeit,')
        melde('die in der gezaehlten Fassung fehlt oder dort anders steht. '
              'Es sind Teiluploads')
        melde('derselben Aufzeichnung -- die kuerzere wegzulassen verliert '
              'nichts.')
    else:
        melde('Ergebnis: %d von %d Paaren sind nicht deckungsgleich. Dort '
              'ist die kuerzere Fassung' % (len(befunde) - einig,
                                            len(befunde)))
        melde('kein reiner Teilupload -- die Zeilen oben sagen, woran es '
              'liegt.')


def zeige_verpasste_splits(strecken):
    """Wo RaceBox Splitueberfahrten verpasst hat -- Stelle, Tag, Groesse.

    Eine Runde ist unstimmig, wenn ihre Sektoren nicht die Rundenzeit
    ergeben: Dann fehlt eine Splitueberfahrt, und niemand weiss, welcher
    Teil der Runde in welcher Zahl steckt. Ob das an einer Stelle der
    Strecke haengt, an einem Fahrtag oder am Fahrzeug, steht nicht in
    einer einzelnen Runde -- nur im Muster ueber alle.
    """
    for eintrag in strecken:
        for z in eintrag['fahrzeuge']:
            krumme = [r for r in z['alle_runden'] if not r['stimmig']]
            if not krumme:
                continue
            melde()
            melde('=' * 72)
            melde('%s -- %s' % (langname(eintrag), z['fahrzeug']))
            melde('=' * 72)
            melde('%d von %d Runden unstimmig (%.0f %%), bei %s'
                  % (len(krumme), z['anzahl_runden'],
                     100.0 * len(krumme) / max(z['anzahl_runden'], 1),
                     anzahl(len(z['sessions']), 'Turn', 'Turns')))
            melde('Kommen zwei je Turn heraus, sind es die Ein- und '
                  'Ausfahrrunden und nicht mehr.')

            # Welcher Sektor fehlt? Gezaehlt wird, welche Layoutposition
            # in der unstimmigen Runde leer geblieben ist.
            fehlt = {}
            for runde in krumme:
                leer = tuple(sektor_nummer(eintrag['layout'], pos)
                             for pos in eintrag['layout']
                             if pos not in runde['sektoren'])
                fehlt[leer] = fehlt.get(leer, 0) + 1
            melde()
            melde('  Welche Sektoren fehlen')
            for leer, wie_oft in sorted(fehlt.items(), key=lambda p: -p[1]):
                melde('    %-24s %4d Runde(n)'
                      % (', '.join('Sektor %d' % n for n in leer)
                         or 'keiner -- alle belegt', wie_oft))

            # Ueber welche Tage verteilt? Ein einzelner Fahrtag heisst
            # etwas anderes als eine gleichmaessige Verteilung.
            nach_tag = {}
            gesamt_tag = {}
            for runde in z['alle_runden']:
                tag = runde['session'].get('datum', '?')
                gesamt_tag[tag] = gesamt_tag.get(tag, 0) + 1
                if not runde['stimmig']:
                    nach_tag[tag] = nach_tag.get(tag, 0) + 1
            melde()
            melde('  Verteilung ueber die Fahrtage')
            for tag in sorted(gesamt_tag, reverse=True):
                krumm = nach_tag.get(tag, 0)
                anteil = 100.0 * krumm / gesamt_tag[tag]
                balken = '#' * int(round(anteil / 5))
                melde('    %s  %3d von %3d  %3.0f %%  %s'
                      % (tag, krumm, gesamt_tag[tag], anteil, balken))

            # Und wie gross ist die Luecke? Eine halbe Sekunde ist etwas
            # anderes als ein halber Sektor.
            luecken = sorted(abs(r['luecke']) for r in krumme)
            melde()
            melde('  Wie viel Zeit fehlt (kleinste, mittlere, groesste)')
            melde('    %s   %s   %s'
                  % (zeit_text(luecken[0]),
                     zeit_text(mittelwert(luecken)),
                     zeit_text(luecken[-1])))


def zeige_detail(eintrag, turns_zeigen=3, nur=None):
    """Eine Strecke in voller Tiefe, je Fahrzeug ein Block.

    `nur` schraenkt auf ein Fahrzeug ein. Ist es keines dieser Strecke,
    werden alle gezeigt und nicht keines: Ein Aufruf mit einem falschen
    Wert gab sonst wortlos eine leere Seite aus -- ein Fehler, der sich wie
    ein leerer Datensatz anfuehlt und deshalb an der falschen Stelle
    gesucht wird.
    """
    if nur is not None and nur not in eintrag['fahrzeuge']:
        nur = None
    name = langname(eintrag)
    for z in eintrag['fahrzeuge']:
        if nur is not None and z is not nur:
            continue
        layout = eintrag['layout']
        melde()
        melde('=' * 72)
        melde('%s -- %s' % (name, z['fahrzeug']))
        melde('=' * 72)
        melde('%s an %s, %s (%d vollstaendig), %d Sektoren'
              % (anzahl(len(z['sessions']), 'Turn', 'Turns'),
                 anzahl(z['tage'], 'Tag', 'Tagen'),
                 anzahl(z['anzahl_runden'], 'Runde', 'Runden'),
                 z['anzahl_vollstaendig'], len(layout)))
        if eintrag['felder'] > len(layout):
            melde('RaceBox liefert %d Sektorfelder, belegt sind %s -- die '
                  'uebrigen bleiben leer.'
                  % (eintrag['felder'],
                     ', '.join(str(p) for p in layout)))
        melde()

        # Die Beschriftung bekommt eine feste Breite, statt sie je Zeile
        # von Hand auszuzaehlen: `2. beste` ist ein Zeichen kuerzer als
        # `Bestrunde`, und genau so stand die Zeitspalte einmal versetzt.
        def zeile(beschriftung, sekunden, rest=''):
            melde(('  %-15s%s   %s'
                   % (beschriftung, zeit_text(sekunden, 10), rest)).rstrip())

        if z['best']:
            zeile('Bestrunde', z['best']['zeit'], herkunft(z['best']))
        else:
            melde('  %-15skeine vollstaendig gemessene Runde' % 'Bestrunde')
        for rang, runde in enumerate(z['top_runden'][1:], 2):
            zeile('%d. beste' % rang, runde['zeit'], herkunft(runde))
        if z['bester_turn']:
            t = z['bester_turn']
            zeile('Turn-Theo', t['theo'],
                  'bester einzelner Turn: %s Turn %d (%s)'
                  % (t['session'].get('datum', '?'),
                     t['session'].get('turn', 0),
                     t['session'].get('startzeit', '?')))
        if not z['aus_teilrunden']:
            zeile('Golden Lap', z['theo'],
                  '%s gegenueber der Bestrunde' % delta_text(z['delta']))
        else:
            # Zwei Zeilen, zwei Aussagen. Oben die Golden Lap, wie sie
            # gerechnet wird, mit derselben Marke wie in der Uebersicht --
            # `Golden Lap (TR)` sind genau die fuenfzehn Zeichen, die das
            # Beschriftungsfeld hat -- und daneben der Grund fuer die
            # Marke. Darunter dieselbe Rechnung ohne die Teilrunden.
            zeile('Golden Lap (TR)', z['theo'],
                  '%d Sektorbestzeit(en) aus einer Ein-/Ausfahrrunde'
                  % z['aus_teilrunden'])
            if z['theo_streng'] is None:
                zeile('Golden Lap', None,
                      'ohne diese bleibt keine vollstaendige Runde uebrig')
            else:
                zeile('Golden Lap', z['theo_streng'],
                      '%s ohne diese, %s gegenueber der Bestrunde'
                      % (delta_text(z['theo_streng'] - z['theo']),
                         delta_text(z['theo_streng'] - z['best']['zeit'])
                         if z['best'] else '--'))

        melde()
        melde('  Woraus die Golden Lap besteht')
        aus_teilrunden = 0
        for pos, runde in z['theo_teile']:
            marke = '' if runde['vollstaendig'] else '   Teilrunde'
            aus_teilrunden += 0 if runde['vollstaendig'] else 1
            melde('    Sektor %-2d %s   %s%s'
                  % (sektor_nummer(layout, pos),
                     zeit_text(runde['sektoren'][pos], 9),
                     herkunft(runde), marke))
            if runde['vollstaendig']:
                continue
            # Gleich darunter derselbe Sektor aus einer vollstaendigen
            # Runde. Die Zeile darueber sagt sonst nur, dass etwas nicht
            # in Ordnung ist, aber nicht, was die Alternative kostet.
            streng = z['streng_je_sektor'].get(pos)
            if streng is None:
                melde('    %10s%9s   keine vollstaendige Runde mit diesem '
                      'Sektor' % ('', '--'))
            else:
                melde('    %10s%s   %s   ohne Teilrunde'
                      % ('', zeit_text(streng['sektoren'][pos], 9),
                         herkunft(streng)))
        if aus_teilrunden:
            melde('    Teilrunde = Ein- oder Ausfahrrunde. Der Sektor ist '
                  'trotzdem zwischen zwei')
            melde('    Splitpunkten gemessen und damit vergleichbar -- die '
                  'Zeile darueber sagt,')
            melde('    was ohne diese %d Sektorzeit(en) uebrig bliebe.'
                  % aus_teilrunden)

        melde()
        melde('  Die drei besten je Sektor')
        for pos in layout:
            for rang, runde in enumerate(z['rangliste'][pos], 1):
                kopf = ('Sektor %-2d' % sektor_nummer(layout, pos)
                        if rang == 1 else ' ' * 9)
                marke = '' if runde['vollstaendig'] else '   Teilrunde'
                melde('    %s  %d. %s   %s%s'
                      % (kopf, rang, zeit_text(runde['sektoren'][pos], 9),
                         herkunft(runde), marke))

        melde()
        beste_turns = sorted([t for t in z['je_turn'] if t['theo']],
                             key=lambda t: t['theo'])
        melde('  Theoretisch beste Runde je Turn (%d von %d)'
              % (min(turns_zeigen, len(beste_turns)), len(z['je_turn'])))
        for t in beste_turns[:turns_zeigen]:
            s = t['session']
            melde('    %s Turn %-2d (%s)  %3d Runde(n)  Best %s   Theo %s  %s'
                  % (s.get('datum', '?'), s.get('turn', 0),
                     s.get('startzeit', '?'), t['runden'],
                     zeit_text(t['best']['zeit'] if t['best'] else None, 9),
                     zeit_text(t['theo'], 9),
                     delta_text(t['theo'] - t['best']['zeit'], 8)
                     if t['best'] else ''))

        melde()
        melde('  Theoretisch beste Runde je Fahrtag')
        for t in sorted(z['je_tag'], key=lambda t: t['datum'], reverse=True):
            melde('    %s  %2d Turn(s)  %3d Runde(n)  Best %s   Theo %s  %s'
                  % (t['datum'], t['turns'], t['runden'],
                     zeit_text(t['best']['zeit'] if t['best'] else None, 9),
                     zeit_text(t['theo'], 9),
                     delta_text(t['theo'] - t['best']['zeit'], 8)
                     if t['best'] and t['theo'] else ''))

        if z['anzahl_unstimmig']:
            melde()
            melde('  Hinweis: bei %d von %d Runde(n) ergibt die Summe der '
                  'Sektoren nicht die Rundenzeit.'
                  % (z['anzahl_unstimmig'], z['anzahl_runden']))
            melde('  Deren Sektorzeiten sind nicht in die Golden Lap '
                  'eingegangen.')

    if eintrag['abweichend']:
        melde()
        melde('  Achtung: %d Session(s) dieser Strecke haben eine andere '
              'Sektoreinteilung' % len(eintrag['abweichend']))
        melde('  und sind deshalb nicht mitgerechnet -- RaceBox hat die '
              'Splitpunkte geaendert:')
        for s in sorted(eintrag['abweichend'], key=sortier_schluessel):
            eigen = session_layout(s)
            melde('    %s Turn %-2d  %s'
                  % (s.get('datum', '?'), s.get('turn', 0),
                     'Sektoren %s' % (', '.join(str(p) for p in eigen))
                     if eigen else 'nur Teilrunden'))


# --- Alles Gefahrene ------------------------------------------------------
#
# Nicht die schnellste Runde, sondern das grosse Bild: Kilometer, Stunden,
# Geschwindigkeiten, Schraeglagen -- insgesamt, je Fahrzeug und je Strecke.
# Dieselbe Dreiteilung, die die Sektorauswertung schon hat.
#
# Gerechnet wird ausschliesslich aus dem Cache. Die Telemetrie steckt im
# selben Dokument wie die Rundenzeiten und laesst sich nicht abtrennen --
# sie kommt beim Holen ohnehin herein, und was daraus folgt, liegt je
# Session unter `kennzahlen`. Diese Ansicht kostet also keine einzige
# Anfrage.


def km_text(meter, breite=0, stellen=1):
    """`412.7` -- Kilometer.

    In den Tabellen ohne Nachkomma (`stellen=0`): Bei zweitausendneunhundert
    Kilometern sagt die Stelle nichts mehr, sie macht die Spalte nur
    unruhiger. Im Block darueber, wo auch einstellige Zahlen vorkommen,
    bleibt sie.
    """
    s = '-' if meter is None else '%.*f' % (stellen, meter / 1000.0)
    return s.rjust(breite)


def stunden_text(sekunden, breite=0):
    """`4:07:32` -- ueber viele Turns summiert sind es Stunden."""
    if sekunden is None:
        s = '-'
    else:
        ganz = int(round(sekunden))
        s = '%d:%02d:%02d' % (ganz // 3600, ganz % 3600 // 60, ganz % 60)
    return s.rjust(breite)


def anteil_text(teil, ganz, breite=0):
    """`80 %` -- oder `-`, wo sich nichts ins Verhaeltnis setzen laesst."""
    if not ganz or teil is None:
        s = '-'
    else:
        s = '%.0f %%' % (100.0 * teil / ganz)
    return s.rjust(breite)


def wert_text(zahl, breite=0, stellen=1):
    """Eine Messzahl mit fester Nachkommastelle -- oder `-`, wo keine ist."""
    s = '-' if zahl is None else '%.*f' % (stellen, zahl)
    return s.rjust(breite)


def kennzahlen_summieren(sessions):
    """Die Kennzahlen mehrerer Sessions zu einer Zeile zusammenziehen.

    Summiert wird, was sich summieren laesst; ueber die Hoechstwerte wird
    das Maximum gebildet und ueber die niedrigste Geschwindigkeit das
    Minimum.

    Der Schnitt ist dabei *nicht* der Mittelwert der Schnitte: Ein Turn von
    zwei Minuten zaehlte darin so viel wie einer von zwanzig. Er wird
    deshalb neu gerechnet, aus der Summe der Meter durch die Summe der
    Fahrzeit -- und aus der Fahrzeit, nicht aus der Gesamtzeit: Wer die
    Standzeit mitrechnet, bekommt einen Schnitt, den niemand gefahren ist.

    Sessions ohne Telemetrie im Cache -- Eintraege aus dem alten CSV-Weg --
    werden nicht uebergangen, sondern gezaehlt. Ihre Runden zaehlen mit,
    ihre Kilometer fehlen, und beides gehoert in die Ausgabe.
    """
    summe = {
        'sessions': len(sessions), 'mit_kennzahlen': 0, 'ohne_kennzahlen': 0,
        'tage': len({s.get('datum', '?') for s in sessions}),
        'runden': 0,
        'meter': 0, 'meter_gesamt': 0, 'punkte': 0,
        # Aus den abgelegten Exporten, nicht aus der Telemetrie -- siehe
        # `runden_aus_export`. `ohne_runden_km` zaehlt, fuer wie viele
        # Sessions die Aufteilung fehlt: Eine Summe aus der Haelfte der
        # Sessions ist keine Summe, wenn niemand sagt, dass es die Haelfte
        # war.
        'meter_runden': 0, 'fahrzeit_runden': 0.0, 'meter_export': 0,
        # Die Meter *derselben* Sessions aus der Telemetrie. Nur gegen die
        # darf der Export gehalten werden: `meter` zaehlt auch Sessions
        # ohne Rundenaufteilung mit, und deren Kilometer fehlten dem Export
        # dann voellig zu Recht.
        'meter_beide': 0,
        'ohne_runden_km': 0, 'mit_runden_km': 0, 'runden_probe': 0.0,
        'punkte_ohne_ort': 0, 'sessions_probe': 0, 'probe_session': None,
        'fahrzeit': 0.0, 'gesamtzeit': 0.0,
        'v_max': 0.0, 'v_min': None, 'v_schnitt': None,
        'v_schnitt_runden': None,
        'schraeglage_links': 0.0, 'schraeglage_rechts': 0.0,
        'schwellen': set(),
    }
    for session in sessions:
        summe['runden'] += len(session.get('runden', []))
        k = session.get('kennzahlen')
        if not k:
            summe['ohne_kennzahlen'] += 1
            continue
        summe['mit_kennzahlen'] += 1
        for feld in ('meter', 'meter_gesamt', 'fahrzeit', 'gesamtzeit',
                     'punkte', 'punkte_ohne_ort'):
            summe[feld] += k.get(feld) or 0
        for feld in ('v_max', 'schraeglage_links', 'schraeglage_rechts'):
            summe[feld] = max(summe[feld], k.get(feld) or 0.0)
        if k.get('meter_runden') is None:
            summe['ohne_runden_km'] += 1
        else:
            summe['mit_runden_km'] += 1
            for feld in ('meter_runden', 'fahrzeit_runden', 'meter_export'):
                summe[feld] += k.get(feld) or 0
            summe['meter_beide'] += k.get('meter') or 0
            if (k.get('runden_probe') or 0.0) > summe['runden_probe']:
                summe['runden_probe'] = k['runden_probe']
                # Die auffaelligste Session gehoert benannt: Eine Zahl ohne
                # Fundstelle laesst sich nicht nachsehen.
                summe['probe_session'] = '%s Turn %s' % (
                    session.get('datum', '?'), session.get('turn', '?'))
            if (k.get('runden_probe') or 0.0) > PROBE_GRENZE:
                summe['sessions_probe'] += 1
        if k.get('v_min') is not None:
            summe['v_min'] = (k['v_min'] if summe['v_min'] is None
                              else min(summe['v_min'], k['v_min']))
        summe['schwellen'].add(k.get('schwelle', FAHRSCHWELLE))
    summe['fahrzeit'] = round(summe['fahrzeit'], 1)
    summe['fahrzeit_runden'] = round(summe['fahrzeit_runden'], 1)
    # Hat keine einzige Session eine Aufteilung, ist die Summe nicht null,
    # sondern unbekannt. Eine Null behauptet, es sei keine Runde gefahren
    # worden -- und das steht hier nirgends.
    if not summe['mit_runden_km']:
        summe['meter_runden'] = None
        summe['fahrzeit_runden'] = None
        summe['meter_export'] = 0
    summe['gesamtzeit'] = round(summe['gesamtzeit'], 1)
    summe['standzeit'] = round(summe['gesamtzeit'] - summe['fahrzeit'], 1)
    if summe['fahrzeit']:
        summe['v_schnitt'] = round(summe['meter'] / summe['fahrzeit'] * 3.6, 2)
    # Der Schnitt auf der gezaehlten Runde ist eine andere Zahl als der
    # Gesamtschnitt: In diesem stecken Aus- und Einfahrrunde mit drin, und
    # die faehrt niemand auf Zeit.
    if summe['fahrzeit_runden']:
        summe['v_schnitt_runden'] = round(
            summe['meter_runden'] / summe['fahrzeit_runden'] * 3.6, 2)
    summe['schwellen'] = sorted(summe['schwellen'])
    return summe


def statistik_bauen(sessions, ausblenden=(), fahrzeugmuster=()):
    """Alles Gefahrene: insgesamt, je Fahrzeug und je Strecke.

    Anders als die Sektorauswertung haengt hier nichts am Layout: Ein
    Kilometer ist auch dann gefahren, wenn RaceBox die Splitpunkte
    zwischendurch verschoben hat. Gerechnet wird deshalb ueber alle
    Sessions und nicht nur ueber die, die zum juengsten Layout passen.

    Doppelt abgelegte Sessions zaehlen einmal -- sonst stuenden dieselben
    Kilometer zweimal in der Summe.

    Je Strecke wird nach Name *und* Layout getrennt und wie in der
    Uebersicht benannt -- `Talkurs (1)`, `Talkurs (2)`. Dass RaceBox die
    Splitpunkte verschiebt, ist ein Sonderfall; zwei Ansichten aber, die
    dieselbe Strecke verschieden nennen, verwirren mehr als er.
    """
    sessions, doppelte = doppelte_entfernen(sessions)

    ausgeblendet = {}
    behalten = []
    for session in sessions:
        name = session.get('strecke') or '(ohne Strecke)'
        if ist_ausgeblendet(name, ausblenden):
            ausgeblendet.setdefault(name, []).append(session)
        else:
            behalten.append(session)

    versteckt = set()
    if fahrzeugmuster:
        passend = []
        for session in behalten:
            fahrzeug = session.get('fahrzeug', 'ohne Fahrzeug')
            if ist_ausgeblendet(fahrzeug, fahrzeugmuster):
                passend.append(session)
            else:
                versteckt.add(fahrzeug)
        # Passt kein einziges, ist der Filter kein Filter, sondern ein
        # Missverstaendnis -- dann lieber alles zeigen als nichts.
        if passend:
            behalten = passend
        else:
            versteckt = set()

    def sortieren(zeilen):
        # Wer am meisten gefahren ist, steht oben. Fehlt die Telemetrie,
        # entscheidet die Zahl der Runden -- eine Null saehe sonst aus wie
        # nichts gefahren.
        zeilen.sort(key=lambda z: (-z['zahlen']['meter_gesamt'],
                                   -z['zahlen']['runden'], z['name']))
        return zeilen

    nach_fahrzeug = {}
    for session in behalten:
        nach_fahrzeug.setdefault(session.get('fahrzeug', 'ohne Fahrzeug'),
                                 []).append(session)

    nach_strecke = {}
    for session in behalten:
        nach_strecke.setdefault(
            (session.get('strecke') or '(ohne Strecke)',
             session.get('konfiguration', '')), []).append(session)

    # Hat eine Strecke mehrere Layouts, bekommt jedes seine Nummer -- und
    # zwar dieselbe wie in der Uebersicht, ueber die Konfiguration sortiert.
    layouts = {}
    for name, konfig in nach_strecke:
        layouts.setdefault(name, []).append(konfig)
    nummer = {}
    for name, konfigs in layouts.items():
        if len(konfigs) > 1:
            for nr, konfig in enumerate(sorted(konfigs), 1):
                nummer[(name, konfig)] = nr

    je_strecke = []
    for schluessel, eigene in nach_strecke.items():
        name, konfig = schluessel
        je_strecke.append({
            'name': ('%s (%d)' % (name, nummer[schluessel])
                     if schluessel in nummer else name),
            'konfiguration': konfig,
            'layout_nr': nummer.get(schluessel),
            'zahlen': kennzahlen_summieren(eigene),
        })

    return {
        'gesamt': kennzahlen_summieren(behalten),
        'je_fahrzeug': sortieren(
            [{'name': name, 'zahlen': kennzahlen_summieren(eigene)}
             for name, eigene in nach_fahrzeug.items()]),
        'je_strecke': sortieren(je_strecke),
        'ausgeblendet': ausgeblendet,
        'versteckt': len(versteckt),
        'doppelte': doppelte,
    }


def statistik_tabelle(ueberschrift, spalte, zeilen):
    """Je Fahrzeug oder je Strecke eine Zeile, in denselben Spalten."""
    melde()
    melde('  %s' % ueberschrift)
    # Statt `km gesamt` steht hier `in Runden`: Der Unterschied zwischen
    # gesamt und gefahren ist Boxengasse und Rollen und steht vollstaendig
    # im Block darueber. Der Bruch, der etwas sagt, ist der zwischen
    # gefahren und in Runden.
    melde('    %-19s%6s%7s%8s%10s%12s%8s%13s'
          % (spalte, 'Tage', 'Turns', 'Runden', 'gefahren', 'ohne in/out',
             'v-max', 'Schraeglage'))
    # Die Einheiten in eine eigene Zeile darunter statt an die
    # Ueberschriften: `gefahren km` und `Schraeglage Grad` waeren breiter
    # als die Spalten, und dann muesste die Zahl weichen.
    melde('    %-19s%6s%7s%8s%10s%12s%8s%13s'
          % ('', '', '', '', 'km', 'km', 'km/h', 'Grad'))
    melde('    ' + '-' * 78)
    for zeile in zeilen:
        z = zeile['zahlen']
        melde('    %-19s%6d%7d%8d%10s%12s%8s%13s'
              % (zeile['name'][:19], z['tage'], z['sessions'], z['runden'],
                 km_text(z['meter'], stellen=0),
                 km_text(z['meter_runden'], stellen=0),
                 wert_text(z['v_max'], stellen=0),
                 '%s / %s' % (wert_text(z['schraeglage_links']),
                              wert_text(z['schraeglage_rechts']))))


def zeige_statistik(stat, ordner=None):
    """Alles Gefahrene -- Kilometer und Stunden statt Rundenzeiten."""
    g = stat['gesamt']
    melde()
    melde('=' * 82)
    melde('Alles Gefahrene')
    melde('=' * 82)
    melde('%s an %s, %s auf %s mit %s'
          % (anzahl(g['sessions'], 'Turn', 'Turns'),
             anzahl(g['tage'], 'Fahrtag', 'Fahrtagen'),
             anzahl(g['runden'], 'Runde', 'Runden'),
             anzahl(len(stat['je_strecke']), 'Strecke', 'Strecken'),
             anzahl(len(stat['je_fahrzeug']), 'Fahrzeug', 'Fahrzeugen')))
    melde()
    melde('  Insgesamt')
    # Eine Zeile je Frage und nicht je Groesse: Kilometer und Zeit stehen
    # nebeneinander, weil sie dieselbe Frage beantworten. Damit erledigt
    # sich auch das mehrdeutige "% davon" -- der Bezug steht als
    # Zeilenname links.
    def zeile(name, km, stunden, rest=''):
        melde(('    %-15s%8s %-3s%10s %-3s%s'
               % (name, km, 'km' if km else '', stunden, 'h' if stunden else '',
                  rest)).rstrip())

    def schnitt(v):
        return 'Schnitt %s km/h' % wert_text(v, 3, 0)

    zeile('aufgezeichnet', km_text(g['meter_gesamt'], stellen=0),
          stunden_text(g['gesamtzeit']), '%d Messpunkte' % g['punkte'])
    zeile('gefahren', km_text(g['meter'], stellen=0),
          stunden_text(g['fahrzeit']), schnitt(g['v_schnitt']))
    # Ohne die Aus- und Einfahrrunde. Kilometer und Zeit teilen sich hier
    # dieselbe Grundlage -- gefahren innerhalb gezaehlter Runden --, sodass
    # der Schnitt daneben aus den beiden Zahlen derselben Zeile hervorgeht.
    # Die Summe der Rundenzeiten waere die falsche Zahl: Sie enthaelt auch
    # Stillstand innerhalb einer Runde, und dann ginge die Rechnung nicht
    # auf.
    zeile('ohne in/out', km_text(g['meter_runden'], stellen=0),
          stunden_text(g['fahrzeit_runden']),
          '%s   %s der km'
          % (schnitt(g['v_schnitt_runden']),
             anteil_text(g['meter_runden'], g['meter'])))
    zeile('gestanden', '', stunden_text(g['standzeit']))

    # Die langsamste Geschwindigkeit stand hier einmal und ist wieder
    # verschwunden. Nicht weil sie falsch war, sondern weil ein Minimum das
    # Zusammenfassen nicht uebersteht: Ueber 259 Sessions sucht es sich
    # garantiert die eine heraus, in der jemand angehalten hat, und landet
    # damit auf der Fahrschwelle. Ein Hoechstwert ist ein Rekord, ein
    # Kleinstwert nur der langsamste Moment -- und der ist immer ein Halt.
    # Je Session bleibt `v_min` in den Kennzahlen stehen; dort ist es eine
    # Messung.
    melde('    %-15s%8s km/h   Schraeglage %s links / %s rechts'
          % ('Hoechstwerte', wert_text(g['v_max'], stellen=0),
             wert_text(g['schraeglage_links']),
             wert_text(g['schraeglage_rechts'])))
    if g['punkte_ohne_ort']:
        melde('    %-15s%8d Messpunkte ohne Positionsfix'
              % ('verworfen', g['punkte_ohne_ort']))

    statistik_tabelle('Je Fahrzeug', 'Fahrzeug', stat['je_fahrzeug'])
    statistik_tabelle('Je Strecke', 'Strecke', stat['je_strecke'])
    statistik_fussnoten(stat, ordner)


def statistik_fussnoten(stat, ordner=None):
    """Was unter der Statistik steht -- je Fussnote genau eine Zeile.

    Zwei Regeln, und beide sind mit Absicht streng.

    **Eine Zeile.** Passt eine Fussnote nicht in eine, gehoert sie nicht
    hierher, sondern in TECHNIK.md. Wer die Statistik aufruft, will Zahlen
    sehen und keinen Aufsatz darueber, wie sie zustande kommen.

    **Pruefungen schweigen, solange sie nichts zu sagen haben.** Die
    Gegenproben laufen bei jedem Aufruf mit, melden sich aber nur, wenn
    etwas nicht stimmt. Eine Zeile "alles in Ordnung" bei jedem Lauf
    erzieht dazu, den ganzen Block zu ueberspringen. Dass es die Proben
    gibt und was sie pruefen, steht in TECHNIK.md.
    """
    g = stat['gesamt']
    melde()
    if not g['mit_kennzahlen']:
        melde('  Keine der %d Session(s) hat Telemetrie -- ein Lauf mit '
              'Netz holt sie.' % g['sessions'])
    if len(g['schwellen']) > 1:
        melde('  Verschiedene Fahrschwellen im Cache (%s km/h) -- --neu '
              'rechnet alles gleich.'
              % ', '.join(wert_text(s, 0, 0) for s in g['schwellen']))
    melde('  Gefahren heisst schneller als %s km/h; `ohne in/out` laesst '
          'Aus- und Einfahrrunde weg.'
          % wert_text(g['schwellen'][0] if g['schwellen'] else FAHRSCHWELLE,
                      0, 0))

    # Die Gegenproben. Gehalten wird der Export gegen die Telemetrie
    # *derselben* Sessions -- sonst fehlten dem Export die Kilometer der
    # Sessions ohne Rundenaufteilung, und das saehe wie eine Abweichung
    # aus. Die Toleranz ist nicht gegriffen, sondern abgeleitet: Beide
    # Seiten runden je Session auf ganze Meter, ueber N Sessions sind das
    # bis zu N Meter, ohne dass etwas nicht stimmt.
    unterschied = abs(g['meter_export'] - g['meter_beide'])
    if g['mit_runden_km'] and unterschied > g['mit_runden_km']:
        melde('  Telemetrie und Export nennen %s und %s km -- sie muessten '
              'gleich sein.'
              % (km_text(g['meter_beide']), km_text(g['meter_export'])))
    if g['sessions_probe']:
        # Benannt wird die auffaelligste Session. Der Verweis stand hier
        # einmal auf `--muster`, und das war falsch: Jene Ansicht sucht
        # Runden, deren Sektoren nicht ihre Rundenzeit ergeben. Hier geht
        # die Summe aber auf -- nur die Grenze zwischen zwei Runden liegt
        # anderswo. Wer dem Verweis folgte, fand nichts.
        melde('  Rundengrenzen weichen in %d von %s ab -- am staerksten %s.'
              % (g['sessions_probe'],
                 anzahl(g['mit_runden_km'], 'Session', 'Sessions'),
                 g['probe_session'] or 'unbekannt'))

    if g['punkte_ohne_ort']:
        melde('  %d von %d Messpunkten verworfen -- dort hatte der '
              'Empfaenger keine Position.'
              % (g['punkte_ohne_ort'], g['punkte'] + g['punkte_ohne_ort']))
    if g['ohne_kennzahlen']:
        melde('  %d von %d Session(s) ohne Telemetrie -- ihre Runden '
              'zaehlen, ihre Kilometer nicht.'
              % (g['ohne_kennzahlen'], g['sessions']))
    if g['ohne_runden_km']:
        melde('  %d von %d Session(s) ohne Rundenaufteilung -- ihr Export '
              'fehlt oder hat keine.'
              % (g['ohne_runden_km'], g['mit_kennzahlen']))
    if stat['doppelte']:
        melde('  %d Session(s) lagen doppelt im Konto und zaehlen einmal.'
              % len(stat['doppelte']))
    if stat['versteckt']:
        melde('  %d weitere(s) Fahrzeug(e) nicht gezaehlt -- Datei '
              '`fahrzeuge` oder --alle.' % stat['versteckt'])
    mehrere = [z for z in stat['je_strecke'] if z.get('layout_nr')]
    if mehrere:
        melde('  Layouts: %s'
              % ',  '.join('%s = %s' % (z['name'],
                                        z['konfiguration'] or 'ohne Namen')
                           for z in sorted(mehrere, key=lambda z: z['name'])))
    if stat['ausgeblendet']:
        melde('  Nicht gezaehlt: %s -- mit --alle wieder dabei'
              % ', '.join('%s (%d)' % (k, len(v))
                          for k, v in sorted(stat['ausgeblendet'].items())))
    if ordner:
        melde('  Daten: %s' % ordner)


# --- Abgleich mit dem Konto -----------------------------------------------

def fahrzeugkarte(rb, gesucht, fahrzeuge, melden=True):
    """Zuordnung Session -> Fahrzeugname fuer die gesuchten Sessions.

    Im CSV-Kopf steht kein Fahrzeug. Die Liste laesst sich aber je Fahrzeug
    filtern -- also wird sie einmal je Fahrzeug durchgegangen und notiert,
    welche Session dabei auftaucht. Abgebrochen wird, sobald alle gesuchten
    Sessions zugeordnet sind: Die meisten Fahrzeuge im Menue gehoeren
    anderen Fahrern und liefern unter `uid=own` ohnehin nichts.
    """
    karte, offen = {}, set(gesucht)
    for vid, name in fahrzeuge.items():
        if not offen:
            break
        ids = rb.alle_ids(vid=vid)
        if not ids:
            continue
        if melden:
            melde('  %-30s %d Session(s)' % (name, len(ids)))
        for sid in ids:
            karte[sid] = name
            offen.discard(sid)
    return karte


def abgleichen(rb, email, passwort, cache_ordner=None, neu=False,
               gleichzeitig=5, fahrzeug=None, seit=None, csv_weg=False,
               mit_csv=True, csv_ordner=None):
    """Anmelden, fehlende Sessions holen, in den Cache legen.

    Return die Zahl der neu geholten Sessions.
    """
    melde('Anmelden bei %s (Zeitgrenze %d s je Anfrage) ...'
          % (rb.basis, rb.zeitgrenze))
    html = rb.anmelden(email, passwort)
    melde('  angemeldet als %s' % email)

    vorhanden = cache_lesen(cache_ordner)
    melde('Sessionliste durchgehen ...')
    # Die Liste kommt neueste zuerst. Das ist die Reihenfolge, in der auch
    # geholt wird -- so steht das Interessanteste zuerst im Cache, und
    # --seit kann aufhoeren, sobald es alt genug wird.
    ids = rb.alle_ids(melden=True)
    fehlend = [i for i in ids
               if neu or vorhanden.get(i, {}).get('version', 0) < CACHE_VERSION]
    melde('  %d Session(s) insgesamt, %d zu holen' % (len(ids), len(fehlend)))
    if not fehlend:
        return 0

    if fahrzeug:
        fehlend = auf_fahrzeug_beschraenken(rb, fehlend, fahrzeug, html)
        if not fehlend:
            return 0

    if len(fehlend) > 5:
        melde('Das ist ein Erstlauf: %d Session(s), %d gleichzeitig.'
              % (len(fehlend), gleichzeitig))
        melde('Jeder weitere Lauf holt nur, was neu dazukommt -- eine '
              'aufgezeichnete Session aendert sich nicht mehr.')
    # Nur der CSV-Weg braucht die Zuordnung ueber die Filterlisten -- im
    # JSON steht das Fahrzeug in der Antwort selbst.
    karte = {}
    if csv_weg:
        fahrzeuge = rb.fahrzeuge(html)
        if fahrzeuge:
            melde('Fahrzeuge zuordnen (%d im Menue) ...' % len(fahrzeuge))
            karte = fahrzeugkarte(rb, fehlend, fahrzeuge)
        else:
            melde('Im Filtermenue steht kein Fahrzeug -- es wird ohne '
                  'Fahrzeugtrennung gerechnet.')

    melde('Sessions holen ...')

    stand = {'nr': 0, 'geholt': 0, 'fehler': [], 'befunde': [],
             'abgeglichen': 0, 'verglichen': 0, 'nur_json': 0}
    schloss = threading.Lock()

    def eine(sid):
        """Eine Session holen. Ein Ausreisser kostet sie, nicht den Lauf.

        Bei 259 Sessions ist die Wahrscheinlichkeit hoch, dass eine
        darunter etwas enthaelt, womit hier niemand gerechnet hat. Sie
        auszulassen und am Ende zu benennen ist richtig; alles andere
        wegzuwerfen, was schon geholt war, nicht.
        """
        begonnen = time.monotonic()
        try:
            eintrag = rb.session_holen(sid, csv_weg, karte.get(sid),
                                       mit_csv, csv_ordner)
        except SystemExit:
            raise            # Netz weg oder Anmeldung hin: das trifft alle
        except Exception as e:
            with schloss:
                stand['nr'] += 1
                stand['fehler'].append((sid, '%s: %s' % (type(e).__name__, e)))
                melde('  %3d/%d  %s  uebersprungen -- %s: %s'
                      % (stand['nr'], len(fehlend), sid[:8],
                         type(e).__name__, e))
            return None
        abgleich_ergebnis = eintrag.pop('abgleich', None)
        cache_schreiben(eintrag, cache_ordner)
        with schloss:
            stand['nr'] += 1
            stand['geholt'] += 1
            if abgleich_ergebnis is not None:
                befunde, verglichen, nur_json = abgleich_ergebnis
                stand['abgeglichen'] += 1
                stand['befunde'].extend(befunde)
                stand['verglichen'] += verglichen
                stand['nur_json'] += nur_json
            melde('  %3d/%d  %s  %s Turn %-2d  %-24s %2d Runden  %4.1fs'
                  % (stand['nr'], len(fehlend), sid[:8], eintrag['datum'],
                     eintrag['turn'], eintrag['strecke'][:24],
                     len(eintrag['runden']), time.monotonic() - begonnen))
        return eintrag

    begonnen = time.monotonic()
    # In Bloecken statt in einem Rutsch: Nur so kann --seit aufhoeren,
    # sobald die Sessions alt genug sind, statt erst alle 259 zu holen.
    with ThreadPoolExecutor(max_workers=gleichzeitig) as arbeiter:
        try:
            for anfang in range(0, len(fehlend), gleichzeitig):
                block = fehlend[anfang:anfang + gleichzeitig]
                eintraege = list(arbeiter.map(eine, block))
                if seit and all(e['datum'] < seit for e in eintraege if e):
                    melde('  alle Sessions dieses Blocks sind aelter als %s '
                          '-- Rest uebersprungen.' % seit)
                    break
        except KeyboardInterrupt:
            # Ohne das Abbrechen der Warteschlange wartete der Ausstieg noch
            # auf jede Session, die schon angefangen wurde.
            arbeiter.shutdown(wait=False, cancel_futures=True)
            raise
    gedauert = time.monotonic() - begonnen

    if stand['abgeglichen']:
        melde()
        melde('  Runden aus dem CSV-Export: %d Session(s), %d Runden, '
              '%d Abweichung(en) zum JSON.'
              % (stand['abgeglichen'], stand['verglichen'],
                 len(stand['befunde'])))
        if stand['nur_json']:
            melde('  %d Runde(n) fuehrt nur das JSON -- das sind die Ein- '
                  'und Ausfahrrunden,' % stand['nur_json'])
            melde('  die das CSV gar nicht als Runden zaehlt. Keine '
                  'Abweichung.')
        for zeile in stand['befunde'][:40]:
            melde('    %s' % zeile)
        if len(stand['befunde']) > 40:
            melde('    ... und %d weitere.' % (len(stand['befunde']) - 40))
        melde('  Die Originalexporte liegen in %s' % (csv_ordner or CSV_ORDNER))
        melde()
    if stand['fehler']:
        melde()
        melde('  %d Session(s) uebersprungen:' % len(stand['fehler']))
        for sid, grund in stand['fehler']:
            melde('    %s  %s' % (sid, grund))
        melde('  Mit --diagnose <ordner> legt der naechste Lauf ihre '
              'Antworten ab.')
        melde()
    zeiten = rb.zeiten()
    melde('  %d Session(s) in %s geholt (%.1fs je Session).'
          % (stand['geholt'], dauer_text(gedauert),
             gedauert / max(stand['geholt'], 1)))
    # Die Aufschluesselung ist kein Schmuck: Sie zeigt, ob eine Leitung, ein
    # Server oder ein hakender Verbindungsaufbau die Zeit frisst.
    melde('  %d Anfrage(n) ueber %d Verbindungsaufbau(ten): %.1fs Aufbau, '
          '%.1fs Warten auf Antwort, %.1fs Laden.'
          % (rb.anfragen, zeiten['aufbauten'], zeiten['aufbau'],
             zeiten['warten'], zeiten['laden']))
    return stand['geholt']


def auf_fahrzeug_beschraenken(rb, fehlend, muster, html):
    """Nur die Sessions eines Fahrzeugs holen.

    Im JSON steht das Fahrzeug erst *nach* dem Holen -- zum Aussortieren
    vorher taugt es also nicht. Die Sessionliste laesst sich aber danach
    filtern: einmal je passendem Fahrzeug durchblaettern, und man weiss,
    welche Sessions gemeint sind.
    """
    fahrzeuge = rb.fahrzeuge(html)
    passend = {vid: name for vid, name in fahrzeuge.items()
               if ist_ausgeblendet(name, [muster])}
    if not passend:
        raise SystemExit(
            'Kein Fahrzeug passt auf %r. Vorhanden: %s'
            % (muster, ', '.join(sorted(fahrzeuge.values())) or 'keines'))
    melde('Auf %s beschraenken ...' % ', '.join(sorted(passend.values())))
    erlaubt = set()
    for vid, name in passend.items():
        ids = rb.alle_ids(vid=vid)
        melde('  %-30s %d Session(s)' % (name, len(ids)))
        erlaubt |= set(ids)
    beschraenkt = [i for i in fehlend if i in erlaubt]
    melde('  %d von %d Session(s) bleiben uebrig.'
          % (len(beschraenkt), len(fehlend)))
    return beschraenkt


def dauer_text(sekunden):
    """`3:07 min` oder `48s` -- je nachdem, was sich besser liest."""
    if sekunden < 90:
        return '%.0fs' % sekunden
    return '%d:%02d min' % (int(sekunden // 60), int(sekunden % 60))


# --- Bedienung ------------------------------------------------------------

def strecke_waehlen(strecken, wahl):
    """Eine Strecke aus Nummer oder Namensteil finden. None, wenn nichts passt."""
    wahl = (wahl or '').strip()
    if not wahl:
        return None
    if wahl.isdigit():
        nr = int(wahl)
        return strecken[nr - 1] if 1 <= nr <= len(strecken) else None
    treffer = [e for e in strecken if wahl.lower() in e['strecke'].lower()]
    return treffer[0] if len(treffer) == 1 else None


def fahrzeug_waehlen(eintrag):
    """Bei mehreren Fahrzeugen fragen, welches gemeint ist.

    Return das gewaehlte Fahrzeug oder None fuer alle. Enter zeigt alle --
    wer sein Konto teilt, will meistens eines sehen, aber nicht immer.
    """
    fahrzeuge = eintrag['fahrzeuge']
    if len(fahrzeuge) < 2:
        return None
    melde()
    for nr, z in enumerate(fahrzeuge, 1):
        melde('    %d  %-24s %5d Runden   Bestrunde %s'
              % (nr, z['fahrzeug'][:24], z['anzahl_runden'],
                 zeit_text(z['best']['zeit'] if z['best'] else None)))
    wahl = frage('Fahrzeug waehlen (Nummer, Enter = alle, q = zurueck): ')
    if wahl is ENDE:
        # Als einzige Stelle verlaesst der Ausgang hier eine Ebene und
        # nicht den Lauf: Wer sich in der Strecke vertan hat, will zur
        # Uebersicht und nicht aus dem Programm. Genau so steht es in der
        # Zeile.
        return ENDE
    if wahl is None:
        return None
    wahl = wahl.strip()
    if wahl.isdigit() and 1 <= int(wahl) <= len(fahrzeuge):
        return fahrzeuge[int(wahl) - 1]
    return None


def weiter():
    """Das Anhalten nach einer Ansicht. None heisst: Schluss."""
    melde()
    antwort = frage('Weiter mit Enter, q = Ende ... ')
    return None if antwort is None or antwort is ENDE else antwort


def schleife(strecken, ausgeblendet, turns_zeigen, ordner=None,
             versteckt=0, doppelte=(), stat=None):
    """Uebersicht zeigen, Strecke waehlen, Detail zeigen, von vorn."""
    while True:
        zeige_uebersicht(strecken, ausgeblendet, ordner, versteckt,
                         doppelte)
        melde()
        # Die Zeile fragt nicht nach einer Strecke, denn sie nimmt auch
        # etwas an, das keine ist. Sie nennt stattdessen, was sie versteht
        # -- und darunter beide Wege hinaus: das Enter, das es schon immer
        # gab, und das `q`, das man nicht raten muss.
        wahl = frage('Auswahl -- Nummer oder Name = Strecke, '
                     's = Statistik, Enter oder q = Ende: ')
        if wahl is None or wahl is ENDE or not wahl.strip():
            return
        # `s` steht aus demselben Grund dort: Eine Ansicht, von der nur das
        # README weiss, findet niemand.
        if wahl.strip().lower() in ('s', 'statistik') and stat is not None:
            zeige_statistik(stat, ordner)
            if weiter() is None:
                return
            continue
        eintrag = strecke_waehlen(strecken, wahl)
        if eintrag is None:
            melde('Keine eindeutige Strecke zu %r.' % wahl.strip())
            continue
        gewaehlt = fahrzeug_waehlen(eintrag)
        if gewaehlt is ENDE:
            continue
        zeige_detail(eintrag, turns_zeigen, gewaehlt)
        if weiter() is None:
            return


def argumente(argv=None):
    p = argparse.ArgumentParser(
        description='Sektorzeiten aus dem RaceBox-Konto ueber Sessions und '
                    'Fahrtage hinweg vergleichen.')
    p.add_argument('--strecke', help='direkt diese Strecke zeigen '
                                     '(Nummer oder Namensteil), ohne Menue')
    p.add_argument('--statistik', action='store_true',
                   help='alles Gefahrene: Kilometer, Stunden, '
                        'Geschwindigkeiten -- insgesamt, je Fahrzeug '
                        'und je Strecke')
    # Hiess einmal `--muster`. Das sagte, wonach die Ansicht sucht (nach
    # einem Muster), aber nicht, worin -- und wer den Namen liest, weiss
    # danach nicht, ob er den Schalter braucht.
    p.add_argument('--verpasste-splits', action='store_true',
                   dest='verpasste_splits',
                   help='wo RaceBox Splitueberfahrten verpasst hat -- an '
                        'welcher Stelle der Strecke, an welchen Tagen')
    p.add_argument('--doppelte', action='store_true',
                   help='die Originalexporte doppelt abgelegter Sessions '
                        'gegeneinander halten')
    p.add_argument('--alle', action='store_true',
                   help='auch ausgeblendete Strecken zeigen')
    p.add_argument('--ausblenden', action='append', metavar='MUSTER',
                   help='diese Strecke kuenftig weglassen (z. B. "Kartbahn*"); '
                        'landet in der Datei `ausblenden` neben dem Skript')
    p.add_argument('--gleichzeitig', type=int, default=5, metavar='N',
                   help='so viele Sessions gleichzeitig holen (Vorgabe 5)')
    p.add_argument('--fahrzeug', metavar='MUSTER',
                   help='nur Sessions dieses Fahrzeugs holen, z. B. '
                        '"Yamaha*"')
    p.add_argument('--seit', metavar='JJJJ-MM-TT',
                   help='nur Sessions ab diesem Tag holen')
    p.add_argument('--ohne-csv', action='store_true', dest='ohne_csv',
                   help='die Runden aus dem JSON nehmen statt aus dem '
                        'Originalexport -- eine Anfrage weniger je Session')
    p.add_argument('--csv', action='store_true',
                   help='ueber den alten CSV-Weg holen statt ueber JSON')
    p.add_argument('--ipv4', action='store_true',
                   help='nur IPv4 verwenden -- hilft, wenn IPv6 nicht traegt')
    p.add_argument('--nur-cache', action='store_true',
                   help='nichts holen, nur rechnen (ohne Netz)')
    p.add_argument('--neu', action='store_true',
                   help='alle Sessions neu holen, auch die schon bekannten')
    p.add_argument('--zugang', action='store_true',
                   help='E-Mail und Passwort neu setzen')
    p.add_argument('--turns', type=int, default=3, metavar='N',
                   help='so viele Turns in der Detailansicht (Vorgabe 3)')
    p.add_argument('--cache', metavar='ORDNER', help='anderer Cache-Ordner')
    p.add_argument('--diagnose', metavar='ORDNER',
                   help='die geholten Seiten dort als Rohabzug ablegen')
    p.add_argument('--zeitgrenze', type=int, default=ZEITGRENZE, metavar='N',
                   help='so viele Sekunden je Anfrage warten (Vorgabe %d)'
                        % ZEITGRENZE)
    p.add_argument('--basis', default=BASIS,
                   help='andere Adresse (fuer den Selbsttest)')
    return p.parse_args(argv)


def soll_holen(cache_ordner):
    """Fragen, ob neu geholt werden soll. Return True oder False.

    Der haeufigste Lauf ist der zweite: nachschauen, was man schon hat.
    Dafuer jedes Mal eine Anmeldung und ein Blaettern durch dreizehn Seiten
    zu bezahlen ist Verschwendung. Gefragt wird nur, wenn ein Mensch
    davorsitzt -- ohne Terminal bleibt es beim Holen.
    """
    vorhanden = cache_lesen(cache_ordner)
    if not vorhanden:
        return True
    juengste = max((s.get('datum', '') for s in vorhanden.values()), default='?')
    melde('Im Cache liegen %d Session(s), juengste vom %s.'
          % (len(vorhanden), juengste))
    if not sys.stdin.isatty():
        return True
    antwort = frage('Neue Sessions von racebox.pro holen? [J/n, q = beenden] ')
    if antwort is ENDE:
        raise SystemExit('Beendet.')
    if antwort is None:
        # Strg+C ist die Notbremse und sagt "jetzt nicht holen" -- nicht
        # "Programm aus". Gerechnet wird dann aus dem Cache.
        return False
    return not antwort.strip().lower().startswith('n')


def main(argv=None):
    a = argumente(argv)
    cache_ordner = a.cache or CACHE

    holen = not a.nur_cache
    if holen and not a.neu and not a.zugang:
        holen = soll_holen(cache_ordner)
    if holen:
        email, passwort = zugang_lesen(neu=a.zugang)
        rb = RaceBox(a.basis, a.diagnose, a.zeitgrenze, a.ipv4)
        try:
            abgleichen(rb, email, passwort, cache_ordner, a.neu,
                       max(1, a.gleichzeitig), a.fahrzeug, a.seit, a.csv,
                       not a.ohne_csv)
        except AnmeldungFehlgeschlagen as e:
            raise SystemExit(str(e))

    sessions = list(cache_lesen(cache_ordner).values())
    if not sessions:
        raise SystemExit(
            'Keine Sessions im Cache (%s). Ohne --nur-cache starten, dann '
            'werden sie geholt.' % cache_ordner)

    if a.ausblenden:
        melde('Ausblendliste ergaenzen ...')
        ausblenden_ergaenzen(a.ausblenden)
        melde()
    muster = [] if a.alle else ausblenden_lesen()
    strecken, ausgeblendet, doppelte = strecken_bauen(sessions, muster)
    # Derselbe Schalter wie beim Holen: Wer nur ein Fahrzeug holen wollte,
    # will es meistens auch nur sehen.
    fahrzeugmuster = ([] if a.alle else
                      ([a.fahrzeug] if a.fahrzeug else fahrzeugfilter_lesen()))
    versteckt = fahrzeuge_filtern(strecken, fahrzeugmuster)
    strecken = [e for e in strecken if e['fahrzeuge']] or strecken
    if not strecken:
        raise SystemExit('Alle %d Strecke(n) sind ausgeblendet. Mit --alle '
                         'anzeigen.' % len(ausgeblendet))

    # Die Rundenkilometer kommen aus den abgelegten Exporten und werden
    # einmal je Session gerechnet -- immer, nicht nur wenn die Statistik
    # gleich drankommt. Hier stand einmal eine Bedingung aus vier Teilen,
    # die das an `--strecke`, `--muster` und ein Terminal knuepfte, um beim
    # ersten Lauf ein paar Sekunden zu sparen. Die Sekunden waren den Satz
    # nicht wert.
    runden_km_ergaenzen(sessions, None, cache_ordner)

    # Die Statistik haengt nicht am Sektorlayout -- sie wird deshalb aus
    # den Sessions gebaut und nicht aus den fertigen Strecken. Kosten tut
    # das nichts: Es ist eine Summe ueber den Cache, keine Anfrage.
    stat = statistik_bauen(sessions, muster, fahrzeugmuster)

    if a.statistik:
        # `--strecke` neben `--statistik` stillschweigend zu uebergehen
        # waere die schlechteste der Moeglichkeiten: Der Schalter steht da,
        # also soll er wirken. Er schraenkt die Statistik auf eine Strecke
        # ein -- je Fahrzeug aufgeschluesselt, wie sonst auch.
        if a.strecke:
            gewaehlt = strecke_waehlen(strecken, a.strecke)
            if gewaehlt is None:
                raise SystemExit('Keine eindeutige Strecke zu %r.' % a.strecke)
            stat = statistik_bauen(
                [s for s in sessions
                 if (s.get('strecke') or '(ohne Strecke)')
                 == gewaehlt['strecke']], muster, fahrzeugmuster)
        zeige_statistik(stat, cache_ordner)
        return 0

    if a.doppelte:
        zeige_doppelte(doppelte_vergleichen(doppelte))
        return 0

    if a.verpasste_splits:
        gewaehlt = ([strecke_waehlen(strecken, a.strecke)] if a.strecke
                    else strecken)
        zeige_verpasste_splits([e for e in gewaehlt if e])
        return 0

    if a.strecke:
        eintrag = strecke_waehlen(strecken, a.strecke)
        if eintrag is None:
            zeige_uebersicht(strecken, ausgeblendet, cache_ordner, versteckt)
            raise SystemExit('\nKeine eindeutige Strecke zu %r.' % a.strecke)
        zeige_detail(eintrag, a.turns)
        return 0

    # Ohne Terminal (Pipe, Datei) waere die Frage nach der Strecke eine
    # Falle -- dann bleibt es bei der Uebersicht.
    if not sys.stdin.isatty():
        zeige_uebersicht(strecken, ausgeblendet, cache_ordner, versteckt,
                         doppelte)
        return 0

    schleife(strecken, ausgeblendet, a.turns, cache_ordner, versteckt,
             doppelte, stat)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Strg+C ist kein Absturz, sondern eine Ansage. Und sie kostet
        # nichts: Jede geholte Session liegt schon im Cache.
        melde()
        melde('Abgebrochen. Was geholt wurde, liegt im Cache -- der naechste '
              'Lauf macht dort weiter.')
        sys.exit(130)
