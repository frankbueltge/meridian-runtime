# N2-T02-Ableitung: Zitat-Audit der Recherche-Records + Verankerungs-Integrität (2026-07-25)

**Status:** Entschieden — der Owner (Frank) hat in Session vom 2026-07-25 N2-T02 als
nächsten Bau beauftragt („Citation-Audit auf die ~47 Record-Zitate +
Verankerungs-Integrität … ggf. zerlegen") und die Zerlegung ausdrücklich an die
Session delegiert. Dieses Dokument fixiert den Fact-Lock, korrigiert die
Zuschnitts-Annahme an der Realität und leitet daraus **N2-T02a** und **N2-T02b**
ab. Es ist der Governance-Commit vor dem Bau; der Code-Merge nach `main` bleibt an
eine ausdrückliche Owner-Freigabe gebunden.

## Der Fact-Lock hat die Aufgabenstellung in zwei Punkten korrigiert

### Korrektur 1: Die „47 Record-Zitate" gibt es als prüfbare Menge nicht

Die 47 stammen aus dem Roadmap-Entwurf („47 Quellen, 45 adversarial verifizierte
Claims"). Der Fact-Lock an den Record-Dateien zeigt: **47 ist eine Fetch-Zahl,
keine Zitat-Zahl.** Record I nennt im Kopf „24 Quellen gefetcht", Record II
„23 Quellen" — 24 + 23 = 47. **Diese Fetch-Listen sind nirgends im Repository
committet**; sie existieren als Daten nicht und können folglich nicht auditiert
werden.

Was die Records tatsächlich maschinell prüfbar machen, wurde erstverifiziert
ausgezählt: **21 distinkte Identifier, inline im Text** (20 arXiv-IDs + die
Nature-Artikel-ID). Das ist die reale Prüfmenge; sie steht als
`corpora/research-records/citations.manifest.json` (an diesem Governance-Commit,
handtranskribiert, mit Datei+Zeile je Zitat).

**Die Lücke zwischen 47 gefetcht und 21 identifiziert ist selbst ein Befund über
die Records** — er wird benannt und im Manifest festgehalten, nicht geglättet.
Ihn zu schließen hieße, dass der Recherche-Harness seine Fetch-Liste mitcommittet;
das ist eine eigene Frage und nicht Gegenstand dieses Pakets.

**Eine Quelle wird ausdrücklich ausgeschlossen:** Record III §B2 (AlphaEvolve,
Google DeepMind) ist **ohne jeden maschinenprüfbaren Identifier** zitiert — kein
arXiv, kein DOI, keine URL. Sie steht darum nicht im Manifest. Ihr einen
Identifier zu erfinden wäre exakt die Fabrikation, die dieses Audit aufdecken soll.
Der Ausschluss ist benannt, nicht still.

### Korrektur 2: Das Verankerungs-Substrat liegt in den Dumps, nicht in einer DB-Tabelle

Es gibt **keine** Tabellen `source_records` oder `evidence_anchors`. Die Persistenz
ist generisch: eine Tabelle `objects` mit `kind` + JSONB-`body`. Beide Real-Run-
Schemata liegen als committete `pg_dump`-Dateien unter `archive/dumps/`. Das
Substrat ist real und reicher als erwartet (Zahlen unten). **Der Bezug
`EvidenceAnchor.source_record_id` existiert wirklich** und ist offline auflösbar,
ebenso `Claim.evidence_relations` / `counterevidence_relations` →
`EvidenceAnchor`.

## Fact-Lock (erstverifiziert an den realen Dateien, Dumps und APIs)

### Zitat-Seite (N2-T02a)

- **21 Identifier** inline über die drei Records; **21/21 lösen auf**, bei einem
  keyless Abruf an der Ableitung (arXiv-API `id_list`, ein Batch-Query; Crossref
  REST für die Nature-DOI). Titel passen zu den Labels.
- **Kein Zitat der Records ist fabriziert.** Für Records, deren Gegenstand die
  dokumentierte 40–80-%-Fabrikationslücke ist, ist das der reflexive Befund —
  dieselbe Bewegung wie N2-T01 an der /e2e-automation-Survey, eine Ebene weiter:
  jetzt hält die **Evidenzbasis der Roadmap** ihre eigenen Zitate aus.
- **8 der 21 sind bereits durch N2-T01 abgedeckt** (die Survey zitiert dieselben
  Werke); **13 sind neu**. Die Überschneidung ist erwünscht: derselbe Identifier,
  zweimal unabhängig aufgelöst, ist ein Kreuz-Check gegen den älteren Snapshot.
- **Der eingefrorene N2-T01-Evaluator passt unverändert.** Das Manifest parst
  fehlerfrei durch den realen `_parse_manifest` (21 Einträge, 0 malformed,
  genau 1 `claimed_title`). **N2-T02a ändert an N2-T01 keine Zeile.**
- **`claimed_title` nur einmal, aus belegtem Grund.** Die Records zitieren mit
  System-/Kurznamen, nicht mit Titeln; nur `„Inspectable AI for Science"` steht in
  Anführungszeichen als Titelbehauptung (Prefix-Match ✓). Ein Kurzname darf
  **nicht** als Titelbehauptung gelten: Record III schreibt „Darwin Gödel
  Machine", arXiv registriert „Darwin **Godel** Machine" — als Titelvergleich
  ergäbe das `title_mismatch`, also eine **Falschbeschuldigung wegen einer
  Resolver-Transliteration**. Der Primärbefund dieses Ziels ist darum EXISTENZ,
  wie schon bei N2-T01.
- **Versionierte arXiv-IDs sind real:** Record I zitiert `2502.14297v3`. Die
  arXiv-API gibt die Version im `id`-Feld zurück (`…/abs/2502.14297v3`,
  erstverifiziert) — der Abruf muss die angeforderte ID **inklusive Version**
  matchen, nicht normalisieren.
- **Kein HTTP-Client im Dependency-Baum** (kein `httpx`, kein `requests`) → der
  Abruf benutzt stdlib `urllib.request`, kein neuer Dependency.

### Verankerungs-Seite (N2-T02b) — die Orakel-Zahlen, offline berechnet

| | `mrr_k1t04_real_run_v2` | `mrr_run2_corroboration_floor_v1` |
|---|---|---|
| Objekt-Zeilen | 67 | 125 |
| SourceRecord / EvidenceAnchor / Claim | 18 / 17 / 4 | 36 / 34 / 8 |
| Anker → SourceRecord **nicht auflösbar** | **0** | **0** |
| Claim → Anker: Referenzen / **nicht auflösbar** | 45 / **0** | 90 / **0** |
| Anker ohne `snapshot_hash` bzw. `content_hash` | 0 / 0 | 0 / 0 |
| SourceRecords, auf die **kein** Anker zeigt | 1 | 2 |
| Anker, auf die **kein** Claim zeigt | 0 | 0 |

- Datei-Anker (die einzige real existierende Verankerung; Git ist für die
  committeten Bytes autoritativ), an der Ableitung berechnet und im Descriptor
  gepinnt:
  - `mrr_k1t04_real_run_v2.sql` → `sha256:273db207188e2ed6ac89484e6ff2b59a73e930cff3766d4d27164b9d1f565ed0`
  - `mrr_run2_corroboration_floor_v1.sql` → `sha256:79338a597d708fbe0d6d34f571bd880d961e6b4fc2289e47659354be9ebeb131`
- Der unverankerte SourceRecord ist in beiden Dumps dasselbe Werk („The Next
  Biennial Should Be Curated by a Machine") — eine Korpus-Quelle, die am Ende
  keine Evidenz getragen hat.
- **Externe Auflösbarkeit des Archivs ist dünn und darf nicht mit Verankerung
  verwechselt werden:** von den 18 distinkten SourceRecords sind **3 Papers**
  (arXiv) und **1 DOI**; **15 sind `curated-artwork-record`** mit gewöhnlichen
  Web-URLs, die weder arXiv noch Crossref kennt. Ein externer Auflösungslauf über
  das Archiv wäre also größtenteils `unverifiable` — er ist **nicht** Teil von
  T02b (siehe „Ausdrücklich NICHT").
- **Die lokale Postgres war an der Ableitung erreichbar** (127.0.0.1:54329 nimmt
  Verbindungen an) und wird **trotzdem nicht** gelesen — sie ist die ausdrücklich
  als Wegwerf-Instanz betriebene (Befund 1, E9-Vertagung). Gelesen werden die
  committeten Dumps: reproduzierbar für jeden mit dem Repo, ohne DB-Verbindung.

## Die zentrale Ehrlichkeits-Unterscheidung: Verankerung ist nicht Belegkraft

Das T02b-Analogon zu N1s „Reliabilität ≠ Validität", N2-T01s „Existenz ≠
Bestätigung" und R2-T01s „Beobachtung ≠ Optimierung". Ein auflösbarer Anker
beweist, dass der Anker auf einen **real archivierten** SourceRecord zeigt — er
beweist **nicht**, dass die Quelle die Behauptung **trägt** (das bleibt N2-T03).

Daraus folgt die zweite, härtere Trennung, die der Report nie kollabieren darf
(AGENTS-Verbot gegen zusammengefasste Statuswerte):

- **`anchor_dangling`** (Anker zeigt ins Leere) und **`claim_reference_dangling`**
  (Claim zeigt auf einen nicht existierenden Anker) sind **Integritäts-
  Verletzungen** — die Maschinen-Fassung von AGENTS' „letting an agent cite a
  source it did not retrieve and anchor".
- **`source_unanchored`** (kein Anker zeigt auf diesen SourceRecord) und
  **`anchor_unreferenced`** (kein Claim zeigt auf diesen Anker) sind
  **Beobachtungen, keine Verletzungen** — eine mitgeführte Korpus-Quelle, die
  keine Evidenz getragen hat, ist kein Fehler.

Beides in einen „Integritätsfehler"-Topf zu werfen wäre genau der verbotene
Kurzschluss — und würde hier sofort 1 bzw. 2 falsche Verletzungen melden.

## Die Zerlegung (und warum sie zwei Pakete sind, nicht eines)

Die beiden Hälften der Aufgabe haben **unterschiedliche Risikoprofile**, und genau
daran wird geschnitten:

- **N2-T02a berührt das Netz** (der reproduzierbare, gated Abruf). Sie ändert
  dafür **keine** Zeile Laufzeit-Code: das Audit läuft mit dem unveränderten,
  eingefrorenen `mrr audit citations` über das neue Manifest + den erzeugten
  Snapshot. Neu gebaut wird **nur die Pipeline** — das, wofür N2-T02 benannt war
  („gated Fetch-Skript, das den Snapshot reproduzierbar erzeugt").
- **N2-T02b berührt das Netz nicht** und ist reine, deterministische
  Offline-Auswertung über committete Bytes — dafür baut sie neuen Domain-,
  Service- und CLI-Code.

Ein gemeinsames Paket würde diese Grenze verwischen: der Egress-schwere Teil wäre
nicht mehr separat prüf- und ablehnbar. Getrennt gilt: **das einzige Artefakt, das
je aus dem Netz ins Repo gelangt, ist ein Snapshot aus T02a — und er wird gelesen,
nie von T02b berührt.**

## Egress-Rahmung (bindend für N2-T02a)

AGENTS Regel 11 verbietet „unrestricted network egress". Der Abruf ist darum als
**gated Pipeline** gebaut, nicht als Laufzeit-Fähigkeit:

1. **Außerhalb der Laufzeit:** das Skript liegt unter `scripts/`, nicht in
   `packages/**` oder `services/**`. Kein Laufzeit-Pfad kann es importieren; das
   Audit-Werkzeug bleibt no-network.
2. **Host-Allowlist, hart:** ausschließlich `export.arxiv.org` und
   `api.crossref.org`, `https` erzwungen. Jede andere URL ist eine typisierte
   Verweigerung, kein Warnhinweis. (Das ist zugleich die saubere Antwort auf
   bandit B310 — die Scheme-Prüfung ist echt, nicht kosmetisch.)
3. **Keyless:** beide APIs sind offen. **Kein Secret, kein Token, keine
   Credentials** — nichts, was in einen Vermerk oder ein Log lecken könnte.
4. **Run-once-commit:** einmal ausgeführt, Ergebnis als Snapshot committet (Git =
   Archiv). Reproduzierbar: dieselben Queries erzeugen denselben Snapshot.
5. **Nichts wird interpretiert:** das Skript schreibt auf, was die APIs
   zurückgeben. Kein Modell, keine Bewertung, keine Ergänzung. Ein
   nicht-auflösender Identifier wird als `resolved: false` festgehalten, niemals
   still übergangen oder „repariert".

## Architektur-Platzierung

**N2-T02a** — `scripts/fetch_citation_resolutions.py` (neu, gated, außerhalb der
Laufzeit) + `corpora/research-records/citations.manifest.json` (an diesem
Governance-Commit) + `corpora/research-records/verification/resolution-snapshot.json`
(vom Skript erzeugt, am Bau committet). **Keine Änderung** an `mrr.domain
.citation_audit*` oder `mrr.services.citation_audit.*`.

**N2-T02b** — gespiegelt am R2-T01-Muster:
- `packages/domain/mrr/domain/anchoring_integrity.py` — reine, no-IO-Funktionen:
  die geschlossenen Status-Sätze, die Auflösungs-Prüfungen, die Deckungs-Analyse.
- `packages/domain/mrr/domain/anchoring_integrity_report.py` — Pydantic-v2-
  Projektion (`MRRModel`, `extra="forbid"`) mit fixem Ehrlichkeits-Header. **Kein**
  persistiertes Objekt, **kein** `schemas/*`-Spiegel (Regel 7).
- `packages/domain/mrr/domain/archive_dump.py` — der **strikte** COPY-Block-Parser
  (rein, nimmt bereits gelesenen Text): findet den `objects`-Block, indiziert
  Spalten **über den Header-Namen**, nie über feste Positionen; `\N` → `None`;
  alles Unerwartete ist eine typisierte Verweigerung, nie ein stilles Überspringen.
- `services/control_plane/mrr/services/anchoring_integrity/service.py` — read-only,
  **öffnet keine Netz- und keine DB-Verbindung**: liest den Descriptor, hasht jeden
  deklarierten Dump, **fail-closed vor jeder Auswertung**, parst, wertet aus.
- `services/control_plane/mrr/services/cli/anchoring_integrity_main.py` + 2 Zeilen
  in `cli/main.py` → `mrr audit anchoring`.

**Bewusst NICHT wiederverwendet:** R2-T01s `check_anchor` nimmt `role: BatchRole`
mit dem geschlossenen Satz `{"manifest","snapshot"}`. Dumps sind weder das eine
noch das andere. Diesen geschlossenen Satz aufzuweiten, nur damit die
Wiederverwendung bequem wird, hieße **einen fremden Invarianten aufzuweichen** —
verboten. T02b spiegelt das Muster mit eigenem, offenem Dump-Satz (die Zahl der
Dumps wächst mit jedem Lauf). Wiederverwendung dort, wo sie real ist; Spiegelung
dort, wo die Form abweicht.

## Ausdrücklich NICHT in N2-T02a/b

Kein LLM, kein Modell-Schritt, keine Support-Prüfung (N2-T03), keine
Zahlen-Konsistenz (N2-T03), kein persistiertes Objekt, kein `schemas/**`, keine
Migration, kein neuer Dependency, kein DB-Zugriff, kein Vorschlags-Artefakt
(R2-T02), keine Optimierung (R2-T03), keine Änderung an N1s/N2-T01s/R2-T01s
Interna. **Kein externer Auflösungslauf über die Archiv-SourceRecords** — 15 von
18 sind Web-URLs, die keine Metadaten-API kennt; das Ergebnis wäre
überwiegend `unverifiable` und würde eine Prüfung vortäuschen, die nicht
stattfindet. **Kein Nicht-Null-Exit bei gefundenen Verletzungen** — das Audit
berichtet, es urteilt nicht über den Lauf (ein Gate-Exit gehört zur nächtlichen
Routine R2-T02, die es noch nicht gibt); Exit-Semantik gespiegelt von N2-T01
(0 Erfolg / 2 Eingabe / 3 Verweigerung).

## Offene Owner-Entscheidungen (unverändert, nichts drängt)

Weiterhin offen und **nicht** durch diese Ableitung berührt: erste Joint Inquiry
als Routine-1-Anlass (E5/E6), K2-Tor-Wiedervorlage, erstes A4-Release, Befund 1
(Artefakt-Blob-Dauerhaftigkeit), N1-T02/T03, **N2-T03** (Support-Prüfung — das
schwere, LLM/menschliche Stück), R2-T02 (Fetch + Vorschlags-Emitter), R2-T03
(GEPA-Schleife, substrat-gated).
