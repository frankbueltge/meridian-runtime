# Ableitung N1-T02: der eingefrorene Goldstandard — der Maßstab vor jeder Selbstveränderung

**Status:** Ableitung mit Fact-Lock. **Kein Bau in dieser Notiz.** Das Paket
`task-packets/N1-T02.yaml` folgt aus ihr; gebaut wird erst danach.

**Anlass:** Der Owner am 2026-08-01: Meridian soll automatisierte empirische
AI-Research wirklich betreiben und sich aus eigenen Recherchen weiterentwickeln —
zusätzliche nächtliche Routine, keine Einmalsache. Verbindliche Reihenfolge:
**1. Maßstab → 2. modellgestützte Einordnung → 3. Literaturkanal → 4. Kopplung.**

Der Owner hat die Reihenfolge selbst begründet: *„Ohne Schritt 1 produzieren 3 und 4
eine Zahl ohne Maßstab und eine Praxis, die sich selbst zustimmt."* Diese Ableitung
behandelt ausschließlich Schritt 1.

---

## 1. Fact-Lock — vier Korrekturen am Handoff vom 2026-08-01

Der Handoff (`2026-08-01-handoff-selbstentwickelnde-praxis.md`) ist die Ausgangslage,
nicht der Auftrag. Er wurde gegen den Code geprüft. Vier seiner Behauptungen sind
falsch oder unscharf; die Belege sind gelesene Zeilen, keine Ableitungen.

### K1 — Die Föderation trägt längst beliebige Nutzlasten

**Behauptet** (Handoff §3, Review vom 2026-07-31 §5, Wegkarte vom 2026-07-26 Punkt 5):
`CorrectionNotification` sei der einzige Payload-Typ, den die Föderation tragen könne,
und es gebe keinen Weg vom Objekt ins Envelope.

**Widerlegt.** `mrr federation envelope sign` existiert und ist ausdrücklich
payload-agnostisch:

- `services/control_plane/mrr/services/cli/federation_main.py:466` —
  *„Payload-agnostic: no --payload-kind is special-cased."*
- `federation_main.py:483` — `--payload-kind` ist *„a free-form, non-empty tag
  identifying the carried payload's shape."*
- Einzige Bedingung: die Nutzlast trägt ihren eigenen `content_hash`
  (`federation_main.py:475-480`, sonst `EnvelopePayloadMissingContentHashError`).

Die Lücke, die die Wegkarte als Punkt 5 beschrieb, hat E5-T10 geschlossen und I1-T01
an die Kommandozeile gebracht — `AGENTS.md` führt beide in seiner eigenen Tabelle der
geschlossenen Außenkanten. Der Handoff hat die eigene Doku nicht nachgezogen.

**Folge, und sie ist groß:** Die Owner-Entscheidung vom 2026-08-01, den Maßstab über
einen Encounter mit einer anderen Praxis setzen zu lassen, ist **nicht durch fehlenden
Transport blockiert.** Sie kehrt die Reihenfolge nicht um.

Offen bleibt allein Ulysses' Node-ID und dessen Vertrauenserklärung — eine Absprache,
kein Bau (Wegkarte Punkt 8/9).

### K2 — Der Modellschritt schützt `evidence_relation`, aber anders als behauptet

**Behauptet** (Handoff §4.2): der modellgestützte Schritt schlage „nur die zwei
Prosafelder" vor; Beleg `synthesis_executor.py:710/713`.

- **Zeilen falsch.** 710/713 liegen in `_classify_analysis`, wo `evidence_relation`
  *gelesen* wird. Der Modellschritt ist
  `services/node_runtime/mrr/services/node_runtime/synthesis_executor.py:431-488`.
- **„nur zwei Felder" falsch.** Das Feldset ist datengetrieben: `:974` liest
  `extraction_fields` aus dem Protokollkörper, `:491-503` baut daraus dynamisch ein
  Pydantic-Modell (`create_model`, `extra="forbid"`). Es sind heute diese zwei, weil
  beide committeten Korpora sie so deklarieren — nicht, weil Code das erzwingt.
- **Schlussfolgerung richtig, und stärker als der Handoff weiß.**
  `evidence_relation` ist ein Top-Level-Feld auf `CorpusEntry` (`:247`), `extraction`
  ist ein davon getrennter Dict (`:251`), und `ExtractionOutcome` (`:413-421`) kann
  typseitig keinen Entry tragen. Selbst wer `"evidence_relation"` in
  `extraction_fields` schriebe, erzeugte nur einen gleichnamigen Schlüssel *im
  extraction-Dict*; `_classify_analysis` liest weiter `row.entry.evidence_relation`.
  Der Schutz ist strukturell, nicht konventionell.

**Folge für Schritt 2:** modellgestützte Einordnung ist ein *neuer Pfad*. Der
vorhandene Modellarm kann nicht dorthin erweitert werden — die Typgrenze ist der
Schutz und darf nicht aufgeweicht werden.

### K3 — „verified" ist die Namenskollision, nicht die Datenverfälschung

**Bestätigt** (Handoff §5): bei Modellerfolg wird überschrieben und das Ergebnis
`verification_disposition="verified"` gesetzt (`:477-483`). Vier Schärfungen, die die
Bewertung ändern:

1. Ersetzt wird der **gesamte** `extraction`-Block, nicht nur `classification_basis`
   — und Felder, für die das Modell `null` liefert, fallen ersatzlos weg
   (`:478-482`, `if value is not None`). Kuratierter Text kann also gelöscht werden.
2. `_verification_disposition` ist **write-only.** Genau zwei Fundstellen im ganzen
   Baum, beide in dieser Datei: die Konstante `:202`, der Schreibzugriff `:611`.
   Nichts liest sie.
3. Es ist ein **anderes Feld** als `CorpusEntry.verification_status` (`:248`), und nur
   dieses zweite steuert die Unabhängigkeitszählung
   (`packages/domain/mrr/domain/source_independence.py:98`).
4. Die Claim-Prosa wird aus dem **kuratierten** Top-Level-Feld
   `claim_relevant_finding` gebaut (`:929-943`), nie aus der Modell-`extraction`.

Das reale Risiko ist heute nicht ein verfälschter Befund, sondern eine Falle für den
nächsten, der „verified" liest und es für die belastbare Verifikation hält.

**Owner-Entscheidung 2026-08-01: „Nie überschreiben, nie verified."** Der
Modellvorschlag landet in einem eigenen Feld neben dem kuratierten Text, der
kuratierte Text bleibt maßgeblich, die Disposition heißt, was sie misst
(schema-valid). Das entspricht `AGENTS.md` Regel 7. Umsetzung gehört zu Schritt 2,
nicht in dieses Paket.

### K4 — Die nächtliche Routine hat aktuell nichts zu tun

Der Workflow (`.github/workflows/research-run.yml`, cron `40 4 * * *`) ist korrekt
gebaut und braucht keinen Knopfdruck. Aber die Auswahllogik (Zeilen 57-109) wurde
gegen den Live-Stand nachgerechnet: beide vollständigen Korpora stehen in
`archive/answered.json`, also `pending = []`. Der erste echte Schedule-Lauf
überspringt den `run`-Job vollständig.

Das ist kein Defekt — der Workflow ist absichtlich so gebaut, dass nur eine *neue*
Frage etwas auslöst. Es heißt nur: **die Routine läuft leer, bis Schritt 3 sie mit
einer Frage versorgt.** Der Handoff sagt „Läufe fahren nächtlich von selbst" ohne
diesen Zusatz.

Nebenbefund, nicht im Handoff: `corpora/archive-integrity/anchoring-batch.v1.json`
prüft 2 der 3 committeten Dumps. Lauf 3 (`e2e-claims`, 2026-08-01) liegt außerhalb der
nächtlichen Integritätsprüfung. Eigener, kleiner Vorgang.

### Was der Handoff richtig hat

Ungeprüft übernommen wird nichts; folgendes wurde geprüft und **bestätigt**:
`GeminiModelAdapter` hat null Produktiv-Aufrufstellen; N1-T02/N1-T03 existieren nicht;
N1-T01 misst Reliabilität und **nicht** Validität, typseitig erzwungen
(`agreement_report.py:216`, `measures_reliability_not_validity: Literal[True]`);
`output_hash` ist über Läufe hinweg instabil, weil frisch gemünzte
`protocol_id`/`question_id`-ULIDs in den gehashten Ausgang eingehen (`:996-1003`,
`identity.py:40`), und es existiert **kein** stabiler inhaltsadressierter
Fingerabdruck eines Befunds.

---

## 2. Der zweite Befund: der Maßstab entsteht nicht aus dem Nichts

Der Handoff liest sich, als sei Schritt 1 ein Neubau („Braucht kein Modell und kein
Netz. Kann sofort gebaut werden."). Das stimmt in der Sache, unterschlägt aber, wie
viel bereits steht. Am Repository geprüft:

| Was der Maßstab braucht | Was existiert |
|---|---|
| Label-Isolation | `benchmarks/meridianbench/harness.py:38-63` — `BenchmarkCase.input`/`.expected`; `SystemUnderTest` ist `Callable[[InputT], OutputT]`, das Label ist **typseitig unerreichbar**, nicht bloß ungenutzt |
| Kennung des eingefrorenen Sets | `benchmarks/meridianbench/promotion.py:63-79` — `EvaluationProfile.fixture_set_id` |
| Fail-closed | `promotion.py:81-91` + `:119-134` — ein `None`-Metrikfeld lässt sein Target **durchfallen**, nie stillschweigend durch |
| Schwellen von außen, ADR-änderbar | `benchmarks/meridianbench/targets.py` — inkl. `FALSE_SUPPORT_ON_MB_CIT_TARGET ≤ 0.02` |
| Entscheidung, die nichts vollzieht | `promotion.py:137-169` — `decide_promotion`, rein, „enacts nothing" |
| Konfusionsmatrix, Kappa, gewichtetes Kappa, Krippendorff-Alpha | `packages/domain/mrr/domain/agreement.py` |
| **Per-Kategorie P/R/F1 gegen einen benannten Referenz-Rater** | `agreement.py:432` `CategoryPrf`, `:451` `per_category_prf(..., reference=)` — genau die Validitätsmetrik |
| Majority-Baseline | `agreement.py:258` `majority_baseline(..., reference=)` |
| Gehashte, committete Eingabe statt Code | Muster `corpora/model-collapse/verification/agreement-crosswalk.v1.json` (N1-T01 derived_decision (b)) |
| Under-Power ehrlich melden | N1-T01s `below_power`-Flag |

Und das Vorhaben ist im Repo bereits entworfen. Die Capability-Roadmap vom
2026-07-24 beschreibt N1 als *„Goldstandard-Sets (≥20–30 Labels/Kategorie),
Validierung erst nach Kriterien-Finalisierung (als Objekt-Zustand erzwingbar),
Kappa/F1 statt Accuracy"* — und sagt im selben Absatz: *„der Harness ist zugleich der
eingefrorene Evaluator, den Routine 2 später braucht."* **Routine 2 ist genau die
selbstentwickelnde Routine, die der Owner will.**

Die Evidenz dafür steht in `2026-07-24-primaerquellen-selbstoptimierung.md`:
Selbstverbesserung funktioniert dokumentiert nur, wo der Evaluator mechanisch und
eingefroren ist (AlphaEvolve: „automatically verifiable"), und der dokumentierte
Fehlermodus ist der Angriff des Optimierers auf seinen eigenen Evaluator
(DGM: Marker der Bewertungsfunktion entfernt trotz expliziter Anweisung).

**Konsequenz:** N1-T02 ist ein Paket, kein Programm. Keine Metrik wird neu erfunden;
`agreement.py` wird unverändert benutzt.

---

## 3. Was gemessen wird

Gemessen wird die **eine** Entscheidung, die heute kein Modell trifft und die ein
automatischer Literaturkanal (Schritt 3) treffen müsste:

> Stützt diese Quelle die Aussage, oder widerspricht sie ihr?

Also `evidence_relation ∈ {supports, contradicts, qualifies, contextualizes}` je
Beleg, gegen die gesperrten Begriffsdefinitionen des jeweiligen Charters.

**Warum vier Klassen, aber Zielwerte auf zwei:** `_classify_analysis` zählt
ausschließlich `supports` und `contradicts` (`synthesis_executor.py:192-197`, mit
eigenem Kommentar: *„qualifies/contextualizes rows are still included in the matrix
but count toward neither the supporting nor the contradicting independence bucket"*).
Die beiden anderen bleiben trotzdem im Labelraum, weil der gefährlichste Fehler eines
Klassifikators genau dort liegt: ein `qualifies` als `supports` zu lesen bläht die
Stützung auf und hebt die Deckelung an, die den Claim begrenzen soll.

**Warum die 26 vorhandenen Einträge nicht das Gold sind:** sie sind der
Messgegenstand, nicht sein Maßstab. Sie stammen aus derselben Kuratierung, die geprüft
werden soll, sie sind öffentlich samt Begründung committet, und mit 18+8 Einträgen
liegen sie ohnehin weit unter 20–30/Kategorie.

**Umfang:** ~60 Fälle in `mb-cls-v1`. Das liegt unter dem Roadmap-Kriterium und wird
**als `below_power` gemeldet**, nicht kaschiert — Präzedenz N1-T01, derived_decision
(g): *„Der below-power-Flag ist verpflichtend, nicht kosmetisch."*

---

## 4. Wie eingefroren wird

„Eingefroren" muss nachprüfbar sein, nicht behauptet. Drei Schichten:

1. **Inhaltsadresse.** `EvaluationProfile.fixture_set_id` wird
   `mb-cls-v1@sha256:<hash der fixture-datei>`. Jedes Messergebnis trägt damit mit
   sich, *wogegen* es gemessen wurde. Das Feld existiert bereits und ist heute ein
   freier String — es bekommt eine Form, keine neue Struktur.
2. **Reihenfolge-Gate.** Kriterien gesperrt → dann gelabelt → dann gemessen.
   Erzwungen über Lock-Hash und Zeitstempel nach dem Muster von
   `MethodProtocol.protocol_lock_content_hash`. Wer nachträglich an den Kriterien
   dreht, bekommt eine Weigerung, keine Messung. Das ist die technische Fassung des
   Roadmap-Satzes „Validierung erst nach Kriterien-Finalisierung, als Objekt-Zustand
   erzwingbar".
3. **Unveränderlichkeit.** `mb-cls-v1` wird nie editiert; eine Änderung ist `v2` —
   dieselbe Regel wie für die Archiv-JSONs. Ein Skript in CI schlägt fehl, wenn sich
   der Hash einer bereits registrierten Version ändert.

---

## 5. Wie gemessen wird

Ausschließlich mit `agreement.py`, Gold als benannter Referenz-Rater:

| Größe | Funktion |
|---|---|
| Ausrichtung, fail-closed bei fehlendem Item | `align_ratings` (`:157`) — wirft `MismatchedRatersError` statt still zu paaren |
| Konfusionsmatrix über die vier Klassen | `confusion_matrix` (`:191`) |
| Accuracy | `observed_agreement` (`:241`) — bei Gold als einem der Rater ist p_o die Accuracy |
| **Immer daneben:** Mehrheits-Boden | `majority_baseline(..., reference="a")` (`:258`) |
| Chance-korrigiert | `cohen_kappa` (`:289`), `weighted_kappa` (`:326`), `krippendorff_alpha_nominal` (`:390`) |
| Je Klasse gegen Gold | `per_category_prf(..., reference="a")` (`:451`) |

Neu ist genau eine abgeleitete Größe: die **False-Support-Rate** — der Anteil der
Fälle mit Gold ≠ `supports`, die als `supports` gelesen wurden. Sie ist aus der
Konfusionsmatrix berechnet, nicht neu gemessen, und spiegelt das bereits vorhandene
`FALSE_SUPPORT_ON_MB_CIT_TARGET`.

**Warum ein neuer Report-Typ nötig ist:** `AgreementReport` trägt
`measures_reliability_not_validity: Literal[True]` (`agreement_report.py:216`) und
kann per Konstruktion nicht `False` werden — das war N1-T01s wichtigste
Ehrlichkeitsgrenze und bleibt unangetastet. Der neue `GoldValidityReport` trägt
spiegelbildlich `measures_validity_against_gold: Literal[True]` samt Gold-sha256,
Kriterienversion und **Label-Herkunft**.

Die Metrik ist symmetrisch zu N1-T01; nur die Bedeutung ändert sich, weil ein Rater
menschlich oder praxisfremd gesetztes Gold ist. Genau diese Bedeutungsdifferenz ist
der Grund, warum sie zwei Typen und nicht ein Flag sind.

---

## 6. Wer den Maßstab setzt

**Owner-Entscheidung 2026-08-01, für Labels *und* Schwellen: ein Encounter über The
Middle.** Nicht der Owner selbst, nicht die Literatur, nicht Meridian.

Der Weg braucht keinen neuen Apparat (K1):

1. **Kommission.** Meridian stellt zusammen: die gesperrten Kriterien, N verankerte
   Quellauszüge, die Frage. Content-gehasht, mit `mrr federation envelope sign`
   signiert (`payload_kind: "GoldLabelCommission"` — ein freier Tag, kein neuer Typ).
2. **Zustellung** über den Kanal, den Ulysses' Sessions tatsächlich lesen
   (`atelier/REQUESTS.md`, gerendert unter `/atelier/requests`). Das signierte
   Envelope ist die maschinenprüfbare Fassung desselben Akts, kein zweiter Weg.
3. **Ulysses labelt blind** — nur Auszüge und Kriterien, nie Meridians eigene
   Einordnung. Andernfalls ist es kein unabhängiger Rater, sondern eine Bestätigung.
4. **Rücklauf** als committete, gehashte Datei. Meridian liest sie als gepinnte
   Eingabe und editiert sie nie (Muster `agreement-crosswalk.v1.json`).
5. **Registrierung** im Encounter-Register.

**Der Einwand, ausgesprochen statt verschwiegen:** Ulysses ist selbst eine maschinelle
Praxis. „Nicht von der zu messenden Praxis gesetzt" ist damit erfüllt; „extern zur KI"
nicht. Das ist schwächer als menschliches Gold oder publizierte Literatur. Es hat
dafür eine Eigenschaft, die keine andere Variante hat: die Uneinigkeit zweier Praxen
ist selbst messbar (κ zwischen ihnen) und wird damit ein Befund statt eines blinden
Flecks. Der Owner hat die Variante in Kenntnis dieses Einwands gewählt.

**Entkopplung.** Der Messapparat hängt an keiner fremden Session. Er wird gegen
synthetische Fixtures vollständig getestet und **weigert sich fail-closed zu
berichten, solange keine hash-gepinnte Gold-Datei vorliegt.** Der Inhalt aus dem
Encounter kommt als eigener Commit hinein und ersetzt die synthetische Fixture.

---

## 7. Abgrenzung — was N1-T02 NICHT tut

- **Kein Modellaufruf.** Kein `ModelAdapter`, kein Netz, kein Schlüssel. Der Apparat
  misst *irgendein* System, das eine Einordnung liefert; welches, entscheidet Schritt 2.
- **Kein Literaturkanal**, keine Korpus-Erzeugung, keine neue nächtliche Routine.
- **Keine Kopplung an die Verfassung.** Schritt 4, und er überquert eine Repo-Grenze
  (siehe §8).
- **Kein neuer Payload-Typ** in der Föderation — `--payload-kind` ist frei (K1).
- **Keine Änderung an `agreement.py` oder `agreement_report.py`.** N1-T01s
  Reliabilitätsgrenze bleibt, wie sie ist.
- **Keine automatische Übernahme.** `decide_promotion` vollzieht nichts und wird
  nichts vollziehen; das bleibt so.

---

## 8. Was danach kommt, und wo die eigentliche Schwierigkeit liegt

Schritt 4 (Kopplung: Befund → Änderung an Instrument oder Verfassung) hat ein
Bauteil, das bisher niemand benannt hat: **die Selbstamendierung und der Gauntlet
liegen in der Praxis, nicht im Werkzeug.**

- `field-research/PROTOCOL.md` kennt die Selbstamendierung bereits und hat sie
  mehrfach real vollzogen (*„The collective may develop this protocol further itself —
  document every change in the journal with a rationale"*, plus datierte
  „adopted session N"-Einschübe).
- Der Gauntlet (`PROTOCOL.md`, Abschnitt „The gauntlet — the ship threshold") ist eine
  rollenbasierte Prüfung durch Sub-Agenten (Verifier / Skeptic / Interlocutor) mit
  einer harten Regel, die für Schritt 4 zentral ist: *„The verdict is only good for the
  exact state it was run on."*

Beides existiert also — aber in `field-research`, während der Maßstab in
`meridian-runtime` liegt. Die Kopplung ist deshalb kein Feature, sondern eine
Repo-Grenzüberschreitung. Sie ist zu entwerfen, wenn Schritt 1 steht, nicht vorher.

---

## 9. Offene Punkte

1. **Ulysses' Node-ID und Vertrauenserklärung** — Absprache, kein Bau. Für die
   Kommission genügt zunächst der Kanalweg; die kryptografische Zurechenbarkeit des
   Rücklaufs ist ein Nachrüstschritt, kein Blocker.
2. **Die konkreten Schwellenwerte** kommen aus dem Encounter. Bis sie vorliegen,
   stehen die Target-Konstanten auf `None` und lassen ihre Checks **durchfallen** —
   fail-closed, nicht „vorläufig bestanden".
3. **Woher die ~60 Quellauszüge stammen.** Sie müssen frisch und außerhalb der
   beiden gefahrenen Korpora liegen. `scripts/fetch_source_content.py` kann sie
   holen (arXiv/Crossref, Abstract-Ebene, hash-verankert) — es ist ein
   Handbetrieb-Skript und bleibt einer.
