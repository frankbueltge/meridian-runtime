# Ableitung N1-T05: der Literaturkanal — aus Quellen wird eine gestellte Frage

**Status:** Ableitung mit Fact-Lock. **Kein Bau in dieser Notiz.** Das Paket
`task-packets/N1-T05.yaml` folgt aus ihr; gebaut wird erst danach.

**Anlass:** Schritt 3 der verbindlichen Reihenfolge des Owners vom 2026-08-01
(**1. Maßstab → 2. modellgestützte Einordnung → 3. Literaturkanal →
4. Kopplung**). Schritte 1 und 2 stehen: der Maßstab ist eingefroren und
registriert, und seit N1-T04 (PR #85) existiert ein System, das einordnet und
gemessen werden kann. Schritt 3 gibt diesem System seinen ersten Abnehmer und
der Praxis ihre nächste Frage.

**Betriebsvorgabe des Owners, unverändert:** *„nichts lokal, alles läuft online
in GitHub Actions und automatisch — aber anlassgetrieben, nie auf einer Uhr.
Keine dritte nächtliche Routine, kein Nightly, das dasselbe neu rechnet."*
§6 zieht die Folgen.

---

## 1. Fact-Lock — nachgerechnet am 2026-08-02, nicht nachgelesen

Beide Ausgangsdokumente (`2026-08-02-stand-und-was-fehlt.md`,
`2026-08-02-n1-t04-ableitung-modellgestuetzte-einordnung.md`) sind als
Ausgangslage gelesen worden, nicht als Auftrag. Was prüfbar war, wurde erneut
ausgeführt.

### Was hält

| Behauptung | Wie geprüft | Ergebnis |
|---|---|---|
| `CorpusEntry` steht in `synthesis_executor.py`, `extra="forbid"` | Datei gelesen | **hält** — Klasse `:223`, `model_config` `:242`, `evidence_relation` `:247`, `verification_status` `:248`, `extraction` `:251` |
| Für `CorpusEntry` gibt es **kein** JSON-Schema | `ls schemas/` (31 Dateien), Suche nach `corpus` | **hält** — kein `corpus-entry.schema.json`; das Pydantic-Modell ist die einzige Definition. Nächster Verwandter ist `schemas/source-record.schema.json`, das die halbe Feldliste spiegelt, aber nicht den Eintrag |
| `research-run.yml` findet heute nichts | Auswahllogik aus `:57-109` gegen den Live-Baum neu ausgeführt | **hält** — `pending = []`; `e2e-claims` und `model-collapse` als beantwortet übersprungen, die fünf übrigen Verzeichnisse wegen fehlender Eingaben |
| `mrr export ro-crate` verlangt eine erreichbare Postgres | `export_main.py` gelesen | **hält** — `--database-url` mit `required=True` `:185`; Erreichbarkeitsprüfung `:316-327`, Fehlertext `:321`. (Der Handoff nennt „185/316"; die Prüfung beginnt bei `:316`, ihre Meldung steht auf `:321` — Zahl stimmt, Zeile ist die Blockgrenze) |
| `ECOLOGY_TOKEN` existiert | `gh secret list` | **hält** — angelegt 2026-08-01T22:22:28Z, neben `GEMINI_API_KEY` und `MERIDIAN_SIGNING_KEY` |
| `gold-classification.yml` ist das Muster | Datei gelesen | **hält** — kein `schedule:`, `push:` auf genau die eigenen Eingaben, `--expect-sha256` gegen `FROZEN.json`, Abbruch auf dem Default-Branch statt Push auf `main` (`:222-235`) |
| Die Messung: 0,5439 gegen Boden 0,4211, κ 0,3084 | `report-gemini-3.5-flash-lite.md` gelesen | **hält, exakt** — dazu κ_w linear 0,3426, quadratisch 0,3964, α 0,2465, False-Support 1/56, n=57 |
| Zweiter Lauf 0,5263 | `git show cc6df74:…report…` | **hält** — dazu κ 0,2792. Streuung 1,76 Prozentpunkte Accuracy, 0,029 κ |
| Null von sechzig unentscheidbar | Vorhersagedatei gegen Goldsatz gezählt | **hält** — Gold hat drei |
| `field-watch.yml` trägt denselben Guard-Fehler | `:98-106` gelesen | **hält** — `git diff --quiet -- corpora/field-watch/` vor `git add`; heute unauffällig, weil `seen.json` bei jedem Fund mitwächst und getrackt ist |
| `--expect-sha256` bleibt im CLI optional | `validation_main.py` gelesen | **hält** — nur der Workflow pinnt |

### K1 — Der Literaturkanal hat heute kein Material. Die Wache liefert nichts.

**Behauptet** (Owner-Prompt, 2026-08-02): *„Die Wache (field-watch.yml) führt
bereits das Register — die neuen Quellen kommen von dort, nicht aus dem
Nichts."*

**Das Register führt sie, aber es ist leer.** Nachgerechnet:

- `corpora/field-watch/observations/` ist **leer** — lokal und auf `origin/main`
  (`git ls-tree -r origin/main` kennt unter `corpora/field-watch/` genau zwei
  Dateien: `searches.v1.json` und `seen.json`).
- Die Wache lief **zweimal**, beide Male erfolgreich (`gh run list`):
  30721188911 am 2026-08-01T22:24:29Z, 30732647866 am 2026-08-02T04:35:49Z.
- Beide meldeten dasselbe (Lauf-Log 30732647866):
  `{"date": "2026-08-02", "new": 0, "note": "sweep completed, nothing new — no
  observation written", "searches_completed": 14}`.

**Kein Defekt, sondern ein leeres Feld.** Geprüft, weil ein strukturell blinder
Sucher genauso aussähe: `scripts/watch_field.py:219-228` fragt arXiv mit
`sortBy=submittedDate&sortOrder=descending` ab — die Wache sieht die neuesten
Einreichungen zuerst, nicht die relevantesten. Der Datumsboden
(`watch_from = "2026-08-01"`, `:247`, `:261`) ist erst dreißig Stunden alt. In
diesem Fenster hat nichts die vierzehn eingefrorenen Anfragen **und** den
Einschlussfilter passiert.

**Folge, und der Owner hat sie entschieden (2026-08-02):** Der erste Korpus
kommt aus einer **bewusst gezogenen Charge des Rückstands**, nicht aus dem
Warten. `corpora/field-watch/seen.json` benennt genau diesen Weg als den
erlaubten:

> *„backlog is drawn deliberately (as the candidate pool was), never dribbled in
> by a nightly pretending it is fresh."*

Das Material liegt bereit: `candidate-pool.v1.json` führt 353 Kandidaten, davon
**60 gezogen** (der Goldsatz) und **293 ungezogen**. Der Kanal bekommt damit
zwei Eingänge — die Wache, sobald sie etwas meldet, und die bewusste Ziehung —
und beide münden in denselben Konverter.

### K2 — Ein Korpus-Eintrag ist eine **eingeordnete** Quelle. Deshalb steht Schritt 3 hinter Schritt 2.

`CorpusEntry` verlangt als Pflichtfelder unter anderem:

```
evidence_relation:      Literal["supports","contradicts","qualifies","contextualizes"]   :247
claim_relevant_finding: str = Field(min_length=1)                                        :250
claim_type:             Literal["observational","interpretive"]                          :246
```

Ein Eintrag ist damit **kein bloßer Quellennachweis**. Er trägt eine Einordnung
und eine Lesart. Der Docstring sagt es selbst (`:224`): *„a small,
**human-curated** source excerpt shaped to become one `SourceRecord` plus one
`EvidenceMatrixRow`"*.

Das ist der Grund für die Reihenfolge des Owners. Ein Literaturkanal, der einen
Korpus füllen soll, braucht etwas, das einordnet — und genau das hat N1-T04
gebaut. **Schritt 3 ist der erste Abnehmer von Schritt 2.**

### K3 — `verification_status` ist die Quellen-Achse, nicht die Einordnungs-Achse

Beim Bau naheliegend zu verwechseln, mit teuren Folgen. Nachgelesen in
`packages/contracts/mrr/contracts/evidence_matrix.py:8-11`:

> *MRR-MTH-015: every `EvidenceMatrix` row MUST **anchor a resolvable source**
> with a verification status and a source family; unverifiable rows are marked,
> never dropped.*

`verification_status` sagt: **ist der Auszug verankert und auflösbar** —
geholt, gehasht, committet. Es sagt **nichts** darüber, ob die Einordnung
geprüft wurde. Die bestehenden Korpora belegen die Lesart in ihrem eigenen
`retrieval_method` (`corpora/e2e-claims/corpus-entries.json`): *„direct read of
the pinned, hash-verified abstract snapshot at
`corpora/e2e-survey/verification/content-snapshot.json`"* — und setzen dazu
`verification_status: "verified"`.

**Zwei Achsen, und sie dürfen nicht vermischt werden:**

| Achse | Feld | Wer setzt es | Prüfbar durch |
|---|---|---|---|
| Quelle verankert? | `verification_status` | der Fetch, deterministisch | Hash gegen den committeten Snapshot |
| Was sagt die Quelle zur Behauptung? | `evidence_relation` | der Klassifikator (N1-T04) | die Messung: 0,5439 gegen Boden 0,4211 |

Ein Eintrag darf also `verified` heißen, sobald sein Auszug verankert ist —
**auch wenn seine Einordnung ein Modellvorschlag ist.** Das ist keine
Umgehung; es ist das, was das Feld laut MTH-015 bedeutet.

**Und es ist kein Freibrief.** Der Kanal setzt `verified` **nur** dort, wo der
Fetch tatsächlich einen Auszug geholt und gehasht hat. Wo er scheitert, steht
`unverifiable` mit Grund — der Validator `:268-280` erzwingt den Grund ohnehin.
Der Kanal erfindet keinen Auszug.

### K4 — Ohne verankerte Quellen fährt der Lauf ins Leere, und zwar still

Nachgerechnet in `synthesis_executor.py:688-707`:

```python
verified_rows = [row for row in group_rows if row.entry.verification_status == "verified"]
if len(verified_rows) < min_included_sources:
    return _AnalysisResult(..., outcome="insufficient_evidence", ...)
```

`corpora/e2e-claims/protocol-parameters.sidecar.json` setzt
`min_included_sources: 3`. Ein Korpus, dessen Einträge alle `pending` tragen,
erzeugt also **keinen Claim**, sondern `insufficient_evidence` — ein korrektes,
fail-closed Ergebnis, aber eben kein Befund.

Bemerkenswert und für §4 tragend: `supporting_rows`/`contradicting_rows`
(`:709-714`) werden über **alle** `group_rows` gebildet, nicht über
`verified_rows`. Die Schwellen für `supported`/`contested` (`:723-724`,
`eligibility_rules`: `supported` ≥ 2 unabhängige Familien, `contested` ≥ 1)
rechnen also mit jeder Einordnung, auch der eines unverankerten Eintrags. Wer
die Achsen aus K3 vermischt, merkt das nicht — der Lauf sagt nichts.

---

## 2. Die Entscheidung des Owners, und was sie mechanisch bedeutet

**Owner, 2026-08-02, auf die vorgelegte Wahl:** der Modellvorschlag **fährt
direkt** — der Kanal committet den Korpus, `research-run.yml` fährt ihn in der
nächsten Nacht. Die dritte der drei vorgelegten Optionen, gewählt mit der
Gegenrede danebenstehend („widerspricht Regel 7"). Das ist eine informierte
Entscheidung und sie wird gebaut.

**Was sie kostet, in einem Satz, damit es niemand später herleiten muss:**
Bei κ 0,3084 und Accuracy 0,5439 gegen einen Boden von 0,4211 ist **etwa jede
zweite vorgeschlagene Relation falsch**, und die Schwellen aus K4 rechnen mit
genau diesen Relationen. Der `supported`/`contested`/`unsupported`-Ausgang des
Laufs erbt diese Fehlerrate.

**Was das Paket dagegen tut — nicht als Abschwächung, sondern damit die Zahl
nicht verschwindet:**

1. **Der Korpus deklariert sich selbst.** `extraction` ist
   `dict[str, str]` (`:251`) und in den bestehenden Korpora bereits der Ort der
   Begründung (`extraction.classification_basis`). Jeder Eintrag trägt dort
   zusätzlich `classification_provenance`: Modellname, Prompt-Hash, Verweis auf
   das Vorschlags-Artefakt, und die gemessene Accuracy samt Boden. Kein Leser
   des Korpus kann übersehen, woher die Relation kommt.
2. **`retrieval_method` sagt die Wahrheit über den Auszug** und nur über ihn.
3. **Das Vorschlags-Artefakt wird mitcommittet**, mit Begründung, `decided_by`
   und Antwort-Hash je Fall — dieselbe Datei, die N1-T04 ohnehin erzeugt.
4. **Die Ausgabe des Laufs erbt den Vermerk.** Wo eine Zahl aus diesem Korpus
   berichtet wird, steht die Fehlerrate daneben. Das ist dieselbe Ehrlichkeit,
   mit der N1-T04 sein „nothing here was verified" in die eigene
   Lauf-Zusammenfassung schreibt.

**Regel 7, ehrlich beziffert statt wegdefiniert.** AGENTS.md Regel 7 lautet
*„No model output may directly become authoritative state."* Die
Quellenhoheit-Tabelle derselben Datei sagt: *Git ist autoritativ für Code,
Schemata, Prompts, Policies* — ein Korpus in Git ist eine **Eingabe**, die
gestellte Frage, nicht der Befund; autoritativ wird erst der Claim in der
Datenbank. Aber die Einordnung fließt über `_classify_analysis` in genau diesen
Claim, und damit bestimmt Modell-Output mit, ob er `supported` oder `contested`
heißt. **Das ist eine echte Spannung zu Regel 7, sie wird nicht aufgelöst, und
sie wird nicht versteckt.** Sie steht hier, sie steht im Paket unter
`specification_gaps`, und sie steht im Korpus selbst.

## 3. Was gebaut wird

Ein Kanal in vier Gliedern, jedes einzeln prüfbar, keines im Netz außer dem
ersten:

1. **Die Ziehung** (`scripts/draw_backlog.py`, außerhalb der Runtime, Muster
   `fetch_source_content.py`): zieht aus `candidate-pool.v1.json` eine benannte
   Charge nach einer **reproduzierbaren, urteilsfreien Regel** — dem Präzedenzfall
   der Goldsatz-Ziehung nachgebaut (`draw_rule`: *„sorted by sha256('mb-cls-v1' +
   arxiv_id), first 60 taken … no judgement, no selection for an expected
   label"*), hier mit eigenem Präfix und unter Ausschluss der 60 bereits
   gezogenen. Nimmt alternativ eine Beobachtungsdatei der Wache entgegen; beide
   Eingänge liefern dieselbe Liste von arXiv-Kennungen.
2. **Die Verankerung** (`scripts/fetch_source_content.py`, **unverändert
   wiederverwendet**): holt je Kennung den Abstract über die bestehende
   Zwei-Host-Allowlist, hasht ihn, schreibt einen
   `source-content-snapshot.v1`. Kein neuer Netzpfad, keine neue Fähigkeit.
3. **Die Einordnung** (`mrr classify relations`, **unverändert
   wiederverwendet**): schlägt je Auszug eine Relation vor, mit Begründung,
   `decided_by` und Disposition `downgraded-to-proposal`.
4. **Der Konverter** (`services/control_plane/mrr/services/literature/`, neu):
   fügt Snapshot und Vorschläge zu `corpus-entries.json` zusammen, das
   `CorpusEntry` **exakt** trifft, und erzeugt die vier übrigen Eingabedateien,
   die `research-run.yml` verlangt.

**Nicht angefasst:**

- `synthesis_executor.py` — der Kanal schreibt eine Eingabedatei, er ändert den
  Executor nicht. Sein falsches `verified` im Extraktions-Arm bleibt der offene
  Befund aus N1-T04 §8.1, eigenes Paket.
- `mrr classify relations`, `gold_service.py`, `agreement*.py` — fertig,
  werden benutzt.
- `searches.v1.json`, `seen.json`, `mb-cls-*` — eingefroren.
- Kriterien und Goldsatz-Labels — unantastbar.

## 4. Die fünf Dateien, die eine Frage ausmachen

`research-run.yml:63-70` verlangt fünf Dateien, sonst ist ein Verzeichnis kein
Synthese-Korpus (nachgerechnet: genau daran scheitern heute fünf der sieben
Verzeichnisse unter `corpora/`):

| Datei | Felder (aus `corpora/e2e-claims/`) | Woher im Kanal |
|---|---|---|
| `question-model.proposal.json` | `raw_question`, `claim_type_sought`, `scope`, `load_bearing_terms` | Vorlage des Pakets, die Frage benannt |
| `concept-charter.proposal.json` | `entries` | Vorlage, die tragenden Begriffe |
| `method-protocol.proposal.json` | `extraction_fields`, `inclusion_criteria`, `exclusion_criteria`, `sensitivity_variations`, `planned_analyses`, `kill_conditions` | Vorlage; deklariert, dass Einordnungen modellvorgeschlagen sind |
| `corpus-entries.json` | Array von `CorpusEntry` | **der Konverter** |
| `protocol-parameters.sidecar.json` | `protocol_id`, `protocol_lock_content_hash`, `inclusion_filter`, `eligibility_rules`, `kill_conditions`, `non_applicability_conditions` | Vorlage; `min_included_sources` bindet an K4 |

Erst wenn alle fünf liegen und das Verzeichnis nicht in `archive/answered.json`
steht, ist `pending` nicht mehr leer.

**Kein JSON-Schema, also ein Test.** Für `CorpusEntry` gibt es keine
Schema-Datei (K1). Der einzige Weg, „trifft exakt" zu behaupten, ohne es zu
behaupten, ist der bestehende Präzedenzfall
`tests/unit/corpora/test_model_collapse_fixtures.py:121`
(`CorpusEntry.model_validate(entry)` über jeden Eintrag). Der Kanal bekommt
denselben Test über seinen eigenen Korpus, und `extra="forbid"` macht ihn
scharf: ein Feld zu viel ist ein Fehlschlag, kein stilles Durchreichen.

## 5. Determinismus, wo er möglich ist

- **Ziehung und Konverter** sind vollständig deterministisch und offline
  getestet: gleiche Eingaben → byte-identische Ausgabe. Kein Wanduhr-Zeitstempel
  in einer geschriebenen Datei (N1-T02s Invariante gilt weiter; der Anlass steht
  im Handoff §5).
- **Die Verankerung** ist ein Netzakt und wird als committeter Snapshot
  archiviert — dasselbe Muster wie der Goldsatz.
- **Die Einordnung** ist nicht reproduzierbar. Die gemessene Streuung ist
  bekannt und benannt: 0,5439 gegen 0,5263 über dieselben eingefrorenen
  Eingaben, 1,76 Prozentpunkte. Jede behauptete Verbesserung muss sich dagegen
  behaupten.

**Kontamination bleibt unausschließbar.** Die Auszüge sind arXiv-Abstracts, die
im Training des Modells gelegen haben können. Steht im Artefakt, steht im
Korpus, steht überall dort, wo eine Zahl daraus berichtet wird.

## 6. Betrieb: online, automatisch, anlassgetrieben — und keine dritte Nacht

`.github/workflows/literature-channel.yml`, gebaut nach
`gold-classification.yml`:

- **Kein `schedule:`.** Die stehenden Regeln binden: *„Keine dritte nächtliche
  Routine"*, *„Kein Nightly, das dasselbe neu rechnet."* Es gibt bereits zwei
  (`research-run.yml` 04:40 UTC, `field-watch.yml` 01:10 UTC), und dieser
  Workflow rechnet nichts neu.
- **Auslöser 1 — `workflow_dispatch`:** die bewusste Ziehung. Eingaben:
  Chargengröße, Chargenname, Modellname, `dry_run`. Das ist der Weg, den der
  erste Lauf nimmt.
- **Auslöser 2 — `push` auf `corpora/field-watch/observations/**`:** die Wache
  meldet etwas, der Kanal wandelt es. Weil die Wache direkt auf `main` schreibt
  (Owner bestätigt, `field-watch.yml:108-135`), ist das ein echter Auslöser und
  keine Attrappe. Er feuert heute nie, weil das Verzeichnis leer ist — und wird
  in der Nacht feuern, in der das Feld nicht still ist.
- **Nie auf `main`.** Ein Korpus ist eine gestellte Frage und eine Behauptung
  über Quellen; er kommt als Pull Request. Gebaut wird dabei ausdrücklich der
  Riegel, den `gold-classification.yml:222-235` nachträglich brauchte: wenn
  `GITHUB_REF_NAME` der Default-Branch ist, wird nicht committet.
- **Erst stagen, dann fragen.** Der Guard-Fehler aus N1-T04 §8.6
  (`git diff --quiet` sieht keine untracked Dateien; er warf einmal eine fertige
  Messung weg und meldete Erfolg) wird hier nicht wiederholt: `git add` vor
  `git diff --cached --quiet`. Ein neuer Korpus besteht ausschließlich aus neuen
  Dateien — hier wäre der Fehler nicht harmlos, sondern tödlich.
- **Ohne Schlüssel: lauter Abbruch.** Kein `GEMINI_API_KEY` → das Kommando
  scheitert und schreibt nichts.

Und dann, ohne dass jemand etwas drückt: `research-run.yml` findet in der
folgenden Nacht `pending = ["<charge>"]`, fährt den Lauf auf eigener Postgres,
sichert den Dump und legt ihn als Pull Request vor. Der Kreis schließt sich
genau dort, wo der Owner ihn haben wollte.

## 7. Abgrenzung — was N1-T05 NICHT tut

- **Keine Änderung an `CorpusEntry`.** Das Ziel ist, das Modell exakt zu
  treffen, nicht es zu erweitern. `evidence_relation` bleibt, wo es ist.
- **Keine neue Netzfähigkeit.** Der Fetch ist der bestehende, mit seiner
  bestehenden Allowlist. Es entsteht kein zweiter Egress-Pfad.
- **Keine Änderung an der Wache.** Ihre Suchen bleiben eingefroren, ihr Register
  bleibt ihres. Der Kanal liest ihre Ausgabe, er schreibt sie nicht.
- **Keine Kopplung an die Verfassung.** Schritt 4, Repo-Grenze, unberührt.
- **Keine Schwellenwerte.** Nichts hier setzt einen. Sie kommen aus einem
  Encounter, nicht aus einem Bau (N1-T02 R5).
- **Keine Selbstoptimierung.** Nichts hier ändert Prompt oder Kriterien aufgrund
  eines Messergebnisses (`2026-07-24-primaerquellen-selbstoptimierung.md`).

## 8. Offene Befunde, weitergereicht statt nebenbei behoben

Unverändert offen aus N1-T04 §8 und dem Handoff, von diesem Paket **nicht**
berührt:

1. Der Extraktions-Arm stempelt `verified` in einem Zweig, den er unter seiner
   eigenen `redaction_policy="hashes_only"` nie erreicht
   (`synthesis_executor.py:483`; `generate_structured` prüft
   `raw_response_text`, unter dieser Policy verboten). Sein Test besteht nur,
   weil das Fake `raw_permitted` zurückgibt. Eigenes Paket.
2. `--expect-sha256` bleibt im CLI optional; nur Workflows pinnen.
3. `field-watch.yml` trägt den Guard-Fehler weiter (`:98-106`), heute harmlos.
4. Kein stabiler Fingerabdruck eines Befunds; `output_hash` ist über Läufe
   hinweg instabil (frisch gemünzte ULIDs).
5. Die Korroborations-Regel aus dem Encounter ist nicht implementiert.
6. Das Modell erklärte null von sechzig Fällen für unentscheidbar (Gold: drei),
   obwohl `R-undecidable-is-a-finding` im Prompt steht.

**Neu und hier festgehalten:**

7. **Lauf 2 der Messung liegt nicht auf `main`.** `cc6df74` (0,5263, κ 0,2792)
   sitzt auf `origin/feat/n1-t04-modellgestuetzte-einordnung` und wurde
   **nach** dem Merge von PR #85 (`e31095f`) gepusht. Auf `main` steht damit
   nur Lauf 1 (0,5439), und **nichts auf `main` sagt, dass ein zweiter Lauf
   1,76 Prozentpunkte darunter lag.** Wer die Streuung zitieren will, zitiert
   heute einen Branch. Eigener, kleiner Vorgang.

## 9. Offene Punkte dieses Pakets

1. **Chargengröße.** `min_included_sources: 3` ist die Untergrenze für
   überhaupt einen Befund; `e2e-claims` fährt mit 8 Einträgen. Zu klein ist
   `insufficient_evidence`, zu groß verbrennt Kontingent (eine Klassifikation
   je Eintrag, 5 s Pause, Free-Tier). Vorschlag: **16** — deutlich über der
   Kill-Schwelle, unter einer Viertelstunde Klassifikationszeit, und klein
   genug, dass ein Mensch den PR wirklich liest.
2. **Die Frage selbst.** Der Kanal liefert Quellen; welche Behauptung sie
   prüfen sollen, steht in `question-model.proposal.json` und ist eine
   inhaltliche Setzung. Naheliegend, weil das ganze Material darauf gezogen
   wurde: dieselbe Behauptung, gegen die der Goldsatz gelabelt wurde
   (*„Systems that automate the research cycle end to end verify their own
   outputs independently of the component that produced them"*) — dann ist der
   Lauf zugleich eine dritte, unabhängige Lesart desselben Feldes. Bleibt
   Owner-Entscheidung.
3. **Ob der Lauf veröffentlicht wird.** Der Korpus landet als PR, der Lauf als
   PR, der Merge ist ein Owner-Akt. Wie bei allem anderen.
