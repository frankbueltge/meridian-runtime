# S1-Ableitung: die Skript-Außenkante in das Security-Gate (2026-07-26)

**Status:** Entschieden — der Owner (Frank) hat am 2026-07-26 die Reihenfolge
freigegeben, in der `scripts/` in die Sicherheitsprüfung an erster Stelle steht.
Governance-Commit vor dem Bau; der Merge nach `main` bleibt an eine ausdrückliche
Owner-Freigabe gebunden.

**Der Anlass ist eine vorbestehende, seit heute doppelt so große Lücke:**
`make security-check` scannt `packages adapters services` — **nicht `scripts`**.
Dort liegen die **einzigen zwei Netzzugriffe des gesamten Repositories**, und seit
N2-T03a ist es einer mehr als vorher.

## Fact-Lock (erstverifiziert, nicht aus der Warnung übernommen)

`bandit -c pyproject.toml -r scripts` findet **8 Befunde: 4 Low, 4 Medium, 0 High.**
Sie zerfallen in drei Klassen mit **unterschiedlichen Antworten** — bandit wirft
zusammen, was getrennt gehört:

### Klasse 1 — real verwundbar: Entity-Expansion (B314/B405, 4 Befunde)

Empirisch geprüft an Python 3.12.8, nicht aus der Doku geschlossen:

| Angriff | Ergebnis |
|---|---|
| **Billion Laughs** (4 Ebenen) | **expandiert auf 30.000 Zeichen — VERWUNDBAR** |
| **External Entity** (`file:///etc/passwd`) | `ParseError: undefined entity` — **geschützt** |

Das ist die entscheidende Trennung: **die eine Hälfte der „XML attacks"-Warnung
trifft zu, die andere nicht.** ElementTree löst keine externen Entities auf — ein
Dateizugriff oder SSRF über XXE ist nicht erreichbar. Aber es expandiert
Entity-Ketten, und eine echte Bombe (9 Ebenen) ergäbe Gigabytes. Das ist ein
realer DoS-Vektor gegen den Rechner, der den Abruf fährt.

### Klasse 2 — real, aber von bandit gar nicht gemeldet: unbegrenzter Download

Beide Skripte setzen `REQUEST_TIMEOUT_SECONDS = 30`, aber lesen die Antwort mit
einem nackten `response.read()` — **ohne jedes Größenlimit.** Ein kompromittierter
oder untergeschobener Host könnte einen mehrere Gigabyte großen Körper liefern und
den Speicher erschöpfen. bandit sieht das nicht; der Fact-Lock schon.

Größenordnung zur Einordnung, real gemessen: eine arXiv-Antwort für **eine** ID ist
**5.426 Bytes**. Ein 20-ID-Batch liegt im niedrigen sechsstelligen Bereich. Ein
Limit im einstelligen MB-Bereich ist also großzügig und trotzdem wirksam.

### Klasse 3 — belegt harmlos, gehört dokumentiert statt behoben

- **B310 ×2 (urlopen scheme audit).** Beide Skripte rufen `_check_allowlisted(url)`
  **vor** jedem `urlopen`: `scheme != "https"` und Host außerhalb der
  Zwei-Host-Allowlist sind je eine **typisierte Verweigerung**. Die N2-T02a-Ableitung
  hat das ausdrücklich als „die saubere Antwort auf bandit B310 — die Scheme-Prüfung
  ist echt, nicht kosmetisch" festgehalten, und es steht bereits unter Testschutz
  (monkeypatchtes `urlopen`, das den Test fallen lässt, falls es je gerufen wird).
- **B404/B603 in `run_test_tier.py`.** `subprocess.run([sys.executable, "-m",
  "pytest", str(directory)], cwd=REPO_ROOT, check=False)` — keine Shell, kein
  `shell=True`, und `directory` stammt aus einem geschlossenen Dict bekannter
  Test-Tiers, nie aus Benutzereingabe.

Für diese vier ist die richtige Antwort eine **lokale, begründete, getestete
Unterdrückung** — nicht eine globale Ausnahme für `scripts/`, die die echten
Befunde gleich mit verstecken würde.

## Die tragende Entscheidung: verweigern statt „sicher parsen"

Der Reflex wäre `defusedxml`. Dagegen sprechen zwei Dinge:

1. **Es wäre ein neuer Dependency** — die Disziplin des Repos ist, keinen
   aufzunehmen, solange die Stdlib reicht.
2. **Es ist weniger streng als nötig.** `defusedxml` parst DTDs sicher. Wir brauchen
   sie überhaupt nicht.

Erstverifiziert an der realen Antwort von `export.arxiv.org`: **kein `<!DOCTYPE`,
keine `<!ENTITY`.** Atom-Feeds tragen keine DTD, und der Crossref-Weg ist JSON (der
JATS-Abstract darin wird per Regex entschlagwortet, nie geparst). Ein Dokument mit
DTD ist an dieser Kante also **immer** anomal.

Die Antwort ist darum: **jedes Dokument mit DOCTYPE- oder ENTITY-Deklaration wird
vor dem Parsen typisiert verweigert.** Das ist strenger als `defusedxml`, kostet
keinen Dependency, und passt zur Hausregel „typisierte Verweigerung, nie ein stilles
Weiter". Die Entity-Bombe scheitert dann nicht am Parser, sondern kommt gar nicht
erst zu ihm.

## Die Ehrlichkeits-Grenze: ein grünes Gate ist keine sichere Kante

Nach diesem Paket meldet `make security-check` auch für `scripts/` null Befunde.
Das beweist: **die bekannten Werkzeug-Befunde sind behandelt** — behoben oder
begründet unterdrückt. Es beweist **nicht**, dass die Kante sicher ist. bandit hat
den unbegrenzten Download nicht gefunden; er kam aus dem Fact-Lock. Ein statischer
Scanner findet, was er kennt.

Der Report des Pakets sagt das ausdrücklich, und die Unterdrückungen tragen ihren
Grund im Klartext an der Zeile — damit eine spätere Änderung, die den Grund
entkräftet, sichtbar wird statt unter einem alten `nosec` weiterzulaufen.

## Akzeptanz-Orakel (VOR dem Bau festgelegt)

1. `make security-check` erfasst `scripts` und beendet mit **Exit 0**.
2. **Genau vier** lokale Unterdrückungen, je mit Begründung an der Zeile: 2 × B310,
   1 × B404, 1 × B603. **Keine globale `scripts/`-Ausnahme**, kein `# nosec` ohne
   Test-ID, kein `# nosec` ohne Begründung.
3. **Null** Unterdrückungen für B314/B405 — die werden behoben, nicht stillgelegt.
4. Die Entity-Bombe aus dieser Ableitung wird typisiert verweigert (Test).
5. Eine Antwort über dem Größenlimit wird typisiert verweigert (Test).
6. Ein Dokument mit `<!DOCTYPE` wird typisiert verweigert, auch ohne Bombe (Test).
7. **Die reale Pipeline bleibt lauffähig:** `content-snapshot.json` und
   `resolution-snapshot.json` bleiben bitgleich — die Skripte werden nicht neu
   ausgeführt, aber ein Test fährt die geänderten Parser über die echten,
   committeten Antwortformen.

## Ausdrücklich NICHT in S1

Kein `defusedxml` und kein anderer neuer Dependency. **Keine globale Ausnahme für
`scripts/`.** Keine Umstrukturierung der Fetch-Skripte, keine Änderung ihrer
Egress-Rahmung (https + Zwei-Host-Allowlist + keyless bleiben wörtlich). Kein
erneuter Netzabruf, keine Änderung an den committeten Snapshots. Keine
Redirect-Behandlung und keine DNS-Rebinding-Abwehr — `urllib` folgt Redirects
standardmäßig, und ob die Allowlist **nach** einem Redirect erneut greifen muss, ist
eine eigene, größere Frage (unten benannt). Kein Modell, keine DB, keine Migration.

## Neu benannt, nicht Teil dieses Pakets

**Redirect-Verhalten.** `urllib.request.urlopen` folgt HTTP-Redirects automatisch.
Die Allowlist prüft die **angeforderte** URL, nicht die **endgültige**. Ein
Redirect von einem erlaubten Host auf einen fremden würde die Prüfung heute
umgehen. Beide erlaubten Hosts sind etablierte Metadaten-APIs, der Vektor setzt
also deren Kompromittierung voraus — aber die Lücke ist real und gehört als
eigenes, kleines Paket geschlossen (eigener `HTTPRedirectHandler`, der die
Allowlist auf jede Zwischenstation anwendet). Hier ausgeklammert, damit dieses
Paket klein und prüfbar bleibt.
