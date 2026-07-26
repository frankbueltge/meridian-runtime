# Fact-Lock: Praxis-Identität und Schlüssel — was wirklich fehlt (2026-07-26, Abend)

**Status:** Fact-Lock vor der Ableitung, Owner-Vorlage. **Kein Bau, keine
Änderung am Code.** Geprüft wurde die tragende Behauptung des Session-Vorschlags
(a): „Praxis-Identität — Practice = 0 in beiden Real-Runs; blockiert die gesamte
Föderation."

Die Zählung stimmt. Die Schlussfolgerung nicht. Was daneben auffiel, ist
gewichtiger als die geprüfte Behauptung.

## Befund 0 — die Zählung stimmt

`grep -o "Practice" archive/dumps/*.sql` ergibt **0** in beiden Real-Run-Dumps.
Der Vertrag existiert vollständig (`packages/contracts/mrr/contracts/practice.py`,
`schemas/practice.schema.json`, `examples/practice.example.json`); eine reale
Instanz existiert nirgends im Repository.

## Befund 1 — Practice blockiert Meridians Zug NICHT

Die drei Föderations-Unterkommandos verlangen Verschiedenes:

| Kommando | verlangt Practice? | Form |
|---|---|---|
| `mrr federation envelope sign` | nein | `--sender-practice-id` ist ein **String** |
| `mrr federation outbox write` | nein | `--sender-practice-id` ist ein **String** |
| `mrr federation inbox accept` | **ja** | `--trusted-sender-practice` ist eine **Datei** |

Meridians Senden ist heute vollständig fahrbar, ohne dass ein einziges
Practice-Objekt existiert: drei Bezeichner-Strings und eine PEM-Schlüsseldatei
genügen. Was das fehlende Practice-Objekt blockiert, ist **Ulysses' Annahme** —
`inbox accept` verlangt die Practice des *Absenders* als Eingabe.

**Das Practice-Objekt ist nicht Meridians Blockade, sondern Meridians Lieferung
an die Gegenseite.** Die Wegkarte führt es unter Punkt 4 korrekt als
„öffentlicher Schlüssel veröffentlicht"; die Zuspitzung „blockiert die gesamte
Föderation" verschiebt die Blockade an die falsche Stelle und macht sie zugleich
kleiner, als sie ist: sie ist keine Selbstbeschreibung, sie ist eine
Schnittstelle.

## Befund 2 — der Schlüssel-Handgriff ist halb so lang wie angenommen, und das ist das Problem

Die Wegkarte veranschlagt Punkt 3 („Signaturschlüssel Ed25519") mit „Frank,
5 Min". Geprüft:

- `generate_ed25519_keypair()` hat **null Aufrufer außerhalb der Tests** — 35
  Testdateien, kein Produktionspfad, kein Skript, kein Kommando.
- Die 14 registrierten `mrr`-Unterkommandos enthalten **weder `keys` noch
  `practice`** (`run, synthesis, verification, export, report, release, validate,
  audit, observe, anchoring, support, artifacts, federation`).
- `packages/crypto/mrr/crypto/keys.py` kennt **kein PEM**. Es kann Rohbytes
  base64-kodieren (`encode_public_key`) und daraus einen `kid` ableiten
  (`derive_key_id`) — mehr nicht.
- Die verbrauchende Seite will dagegen PEM: alle drei Ladepfade
  (`federation_main.py:296`, `synthesis_main.py:90`, `main.py:97`) rufen
  `serialization.load_pem_private_key`.

Daraus folgt die genaue Lage: Frank **kann** in fünf Minuten einen Schlüssel
erzeugen — `openssl genpkey -algorithm ed25519` liefert exakt die PEM-Form, die
alle drei Ladepfade erwarten. Aber in dem Moment, in dem er den Schlüssel
*benutzen* will, braucht er zwei Werte, die **kein Kommando im Repository
herstellt**:

1. den `kid` für `--key-id` und für `keys[].kid` der Practice
   (`derive_key_id` = `"kid:" + base64(sha256(rohe 32 Bytes))`),
2. die base64-kodierten Rohbytes der öffentlichen Hälfte für
   `keys[].encoded_public_key`.

**Der Schlüssel ist im Augenblick seiner Entstehung unbenutzbar.** Das ist zum
fünften Mal dasselbe Muster, das die Wegkarte selbst beschreibt: die Schicht ist
vollständig, die Außenkante fehlt. Diesmal an der Schlüssel-/Identitätskante.

## Befund 3 — eine stille Falle in `envelope sign`

`_run_envelope_sign_command` reicht `--key-id` **unverändert und ungeprüft** an
`build_signed_envelope` weiter. Es gibt keine Gegenprobe, ob der übergebene
`kid` zur übergebenen `--key-file` gehört.

Folge: ein von Hand geratener oder abgeschriebener `kid` erzeugt ein
**syntaktisch einwandfreies, korrekt signiertes Envelope**, das der Empfänger
bei der Signaturprüfung verwirft, weil er unter diesem `kid` keinen passenden
Schlüssel findet. Der Fehler zeigt sich also erst auf der Gegenseite, in einem
fremden Lauf, an einem fremden Tag.

Das ist kein Defekt dieser Schicht — sie ist bewusst payload- und
schlüssel-agnostisch. Es ist der Grund, warum der `kid` **abgeleitet und nicht
eingetippt** gehört, und damit das eigentliche Argument für Befund 2.

## Befund 4 — die Real-Runs haben Wegwerf-Identitäten benutzt

Die Practice-URNs der beiden Läufe:

| Dump | Practice-URNs |
|---|---|
| `mrr_k1t04_real_run_v2` | `01KY1SNY86X0GDE2N9TVZKT4YF` (132×), `01KY1SNYATEVDJGYNGFRZ6S18Q` (8×) |
| `mrr_run2_corroboration_floor_v1` | `01KY35BNKB3C05NTXYHEKC97SX` (126×), `01KY35CRPPECQGP0G6K5T7PDYE` (108×), `01KY1SNY86X0GDE2N9TVZKT4YF` (12×), `01KY35CRPPECQGP0G6K5T7PDYF` (8×), `01KY35BNQBC4JXSAA24PT58NMM` (8×) |

Die ULIDs sind pro Lauf frisch gezogen (Zeitpräfixe `01KY1SNY…` = 21. Juli
07:36, `01KY35…` = 21. Juli 20:20). **Es gibt keine stabile Meridian-Practice-ID
im Archiv.** Jeder Lauf hat sich eine eigene erfunden.

Das ändert den Charakter der anstehenden Entscheidung: eine Practice zu
veröffentlichen ist **nicht** die nachgereichte Beschreibung einer bestehenden
Identität, sondern die **erstmalige Stiftung einer dauerhaften**. Sie wird zu
keinem existierenden Archivobjekt passen.

## Befund 5 — die Archivsignaturen sind gegen jede gültige Practice unprüfbar

Sämtliche neun Signaturen in beiden Dumps tragen Platzhalter-Schlüsselnamen:

```
6 × "key_id": "node-key-1"
3 × "key_id": "origin-key-1"
```

`PublicKeyDescriptor` erzwingt dagegen `kid == derive_key_id(encoded_public_key)`,
und `derive_key_id` liefert **immer** die Form `kid:<base64>`. Ein
schema-gültiges Practice-Objekt kann daher **niemals** einen Schlüssel namens
`node-key-1` führen.

**Die vorhandenen NodeManifest- und Origin-Signaturen sind strukturell gegen
keine gültige Practice verifizierbar** — nicht weil ein Schlüssel verloren ging,
sondern weil sie nie zu einem gehörten. Das ist die exakte Entsprechung des
A1-Befunds auf der Signaturebene: dort waren die Anker aufgelöst und uneinlösbar,
hier sind die Signaturen wohlgeformt und unprüfbar.

Die Unterscheidungsreihe setzt sich fort: Reliabilität ≠ Validität, Existenz ≠
Bestätigung, Verankerung ≠ Belegkraft, Transport ≠ Vertrauen, Anwesenheit ≠
Support, aufgelöster Anker ≠ einlösbarer Anker — und jetzt: **eine gültige
Signatur ≠ eine zurechenbare Signatur.**

## Was daraus für die Aufgabe folgt

Vorschlag (a) bleibt richtig, aber in korrigiertem Zuschnitt. Das Paket ist
nicht „ein Practice-Objekt anlegen", sondern:

**Ein Kommando, das aus einer PEM-Schlüsseldatei und owner-gesetztem Inhalt ein
selbstsigniertes, schema-gültiges Practice-JSON herstellt** — mit `kid` und
`encoded_public_key` **abgeleitet, nie eingetippt** (Befund 2 und 3), über die
unveränderte `mrr.domain.hashing_policy` signiert (der Vertrag verlangt es
wörtlich: „never a second hashing/signing implementation").

Nutzungsanlass im Sinne der use-first-Doktrin, benannt: **Ulysses braucht diese
Datei als `--trusted-sender-practice`.** Ohne sie kann die Gegenseite Meridians
Sendung nicht annehmen. Das ist kein Vorratsbau.

Reihenfolge, korrigiert:

1. **Owner-Inhalt** für die Practice-Felder (unten, ausfüllbar) — nicht ratbar.
2. **Bau des Kommandos** — ohne Schlüssel baubar und testbar (Testschlüssel
   genügen), wie bei E5-T10.
3. **Franks Handgriff:** `openssl genpkey` lokal → Kommando erzeugt Practice-JSON
   → private Hälfte ins GitHub-Secret, danach lokal löschen; öffentliche Hälfte
   committet (Entscheidung vom 2026-07-25, bindend).
4. **Empfänger-ID vereinbaren**, dann R1-T01.

## Das ausfüllbare Formular — Owner-Entscheidung Nr. 3

Jedes Feld unten ist Pflicht, sofern nicht als optional gekennzeichnet. Die
Vokabulare sind aus dem Vertrag gelesen, nicht vorgeschlagen.

| Feld | Typ / Vokabular | Wert |
|---|---|---|
| `name` | Freitext, nicht leer | ? |
| `description` | Freitext, nicht leer | ? |
| `governance_contacts` | Liste von Strings (Beispiel nutzt `mailto:`) | ? |
| `supported_policy_versions` | Liste von Strings; real vergeben: `policy-2026-07-01` (81×), `-07-19`, `-07-21`, `-07-26` | ? |
| `capability_registry_endpoint` | **optional**, URL oder weglassen | ? |
| `disclosure.max_disclosure` | genau eines von `INTERNAL` \| `PARTNER_RESTRICTED` \| `PUBLIC` | ? |
| `disclosure.trust_statement` | Freitext, darf leer sein | ? |
| `keys[0].valid_from` / `valid_until` | Zeitfenster, `valid_from < valid_until` | ? |
| `id` / `practice_id` | neue ULID-URN, **frisch gestiftet** (Befund 4) | wird erzeugt |

**Warnung zu `governance_contacts`:** Die Adresse `frank@bueltge.de` gehört einer
**anderen realen Person** (`github.com/bueltge`) und darf hier nicht eingetragen
werden. Verbindliche Kontaktadresse ist `f.bueltge@gmail.com`, sofern Frank nicht
ausdrücklich eine andere nennt.

## Was dieser Fact-Lock NICHT behauptet

- **Nicht**, dass E5-T10 unvollständig wäre. Der Envelope-Weg ist gebaut und
  belegt; er hängt an keinem dieser Befunde.
- **Nicht**, dass die Real-Runs wertlos sind. Ihre internen Bezüge lösen
  lückenlos auf (N2-T02b). Unprüfbar ist die *Zurechnung nach außen*, nicht der
  innere Zusammenhang.
- **Nicht**, dass die Archivsignaturen gefälscht sind. Sie sind echt und
  wohlgeformt — sie gehören nur zu keinem veröffentlichten Schlüssel.
- **Nicht**, dass damit die Frage nach dem Forschungsgegenstand berührt wäre
  (offene Owner-Entscheidung Nr. 1). Sie bleibt offen und unberührt.
