# N1-Ableitung: Validierungs-Harness, erstes Paket N1-T01 (2026-07-24)

**Status:** Entschieden — der Owner (Frank) hat in Session vom 2026-07-24 die
Reihenfolge des Roadmap-Entwurfs (`2026-07-24-capability-roadmap-entwurf.md`) an
die Session delegiert („mach einfach wie du denkst, du steckst da viel tiefer
drin"). Dieses Dokument fixiert die Entscheidung und leitet daraus das erste
gebaute Paket **N1-T01** ab. Es ist der Governance-Commit vor dem Bau; der
Code-Merge nach `main` bleibt an eine ausdrückliche Owner-Freigabe gebunden.

## Die Entscheidung

1. **N1 zuerst** (Validierungs-Harness), **N2 (Citation-/Claim-Audit) als
   Fast-Follow.** Begründung: N1 ist das einzige der drei Nischen-Pakete, das
   *ohnehin* gebraucht wird — Routine 2 ist ohne den **eingefrorenen** N1-Harness
   nicht governance-fähig (Record III: der Evaluator gehört außerhalb der
   Modifikationsfläche), und der bereits gelaufene K1-T04-Typ wird ohne N1 nicht
   prüfbar valide. Höchster Hebel, geringstes Bedauern.
2. **joint-first bleibt die benannte editoriale Richtung, ohne heute Föderation
   zu bauen.** Die Kadenz-Entscheidung der Parallelsession benennt den *späteren*
   Anlass für Routine 1 / E5/E6; sie löst keinen Infrastrukturbau aus. Guardrail
   (ein Experiment in der losen Sammlung, keine Wiederbelebung der „Akte der
   Gegenwart") ist intakt, weil N1 ein einzeln stehendes Instrument ist.
3. **use-first unverändert bindend** (`2026-07-22-nutzungsentscheidung-e9-vertagt.md`):
   N1 wird zerlegt; nur das Sub-Paket mit realem, heute erfüllbarem Anlass
   (N1-T01) wird jetzt gebaut. N1-T02/T03 sind benannt, nicht abgeleitet.

## Der Nutzungsanlass (real, heute erfüllbar)

Der K1-T04/K1-T06-Lauf enthält **zwei unabhängige Klassifikationen derselben
Gegenstände** in **derselben Aufgabe**: die Pipeline-Klassifikation und einen
**blinden, unabhängigen Zweitdurchgang** (`corpora/model-collapse/verification/`,
Verifikationsinstanz nach K1-T06, u. a. Ulysses — die Instanz sah die
Erstklassifikation nachweislich nicht, `blind-brief.md`). `comparison.md` behauptet
**„18/18 agreement"** — aber nur als **Prosa-Auszählung**: sie stützt sich auf einen
**nicht dokumentierten Label-Crosswalk** (blind `{instantiates/references-only/
qualifies}` ↔ Pipeline `{supports/contradicts}`), sie **poolt zwei verschiedene
Analyse-Aufgaben** (15 Werke Instanziierung; 3 Theorie-Papiere) in *eine* Zahl, und
sie meldet **rohe Übereinstimmung statt κ** auf einer stark schiefen Verteilung
(ein Mehrheitsklassifikator träfe ~14/18 blind).

**N1-T01 macht diese behauptete Übereinstimmung nachprüfbar:** ein deterministischer,
read-only-Lauf, der den Crosswalk zu einem **deklarierten, gehashten Eingabewert**
macht, **nach Analyse stratifiziert** (nicht poolt) und **κ, die Randverteilung/
Schiefe, eine Mehrheits-Baseline und eine Power-Marke** meldet. Er wandelt eine
*behauptete* Zahl in ein hash-verankertes, contestierbares Artefakt — genau die
Lektion „Kappa statt Accuracy" aus Record II/A4, angewandt auf die **eigenen** Daten
des Projekts. Zugleich ist der Metrik-Kern der **eingefrorene Evaluator**, den
Routine 2 später braucht, und der risikoärmste Fähigkeits-Typ (mechanisch,
read-only, gegen versiegelte/committete Datensätze).

**Ehrlich zur Größenordnung:** n = 18 (15 + 3) liegt **unter** der dokumentierten
Schwelle von 20–30 Labels/Kategorie (A4), und die Übereinstimmung ist auf diesem
Sample perfekt — die Schlagzeilen-κ ist damit trivial 1,0. Der Wert von N1-T01 ist
**nicht** eine überraschende Zahl, sondern (a) Auditierbarkeit/Reproduzierbarkeit
einer bereits behaupteten Aussage, (b) der wiederverwendbare Evaluator-Kern, (c) das
sichere read-only-Muster. Der nächste natürliche Eingang ist
`run2-corroboration-floor/` — dort ist Übereinstimmung nicht garantiert perfekt.

## Die zentrale Ehrlichkeits-Unterscheidung: Reliabilität ≠ Validität

Der blinde Zweitdurchgang ist eine **unabhängige Instanz**, kein menschlicher
Goldstandard. κ(Pipeline, blind) misst darum **Reliabilität/Reproduzierbarkeit
zwischen Instanzen**, **nicht Validität** gegen menschliche ground truth. Die
Records sind explizit: Stabilität ist **kein** Validitätsnachweis („Stubborn
Consistency", A3). N1-T01 **beschriftet seinen eigenen Output entsprechend** und
behauptet nirgends Validität. Validität gegen menschliche Gold-Labels ist Gegenstand
von N1-T02 (eigenes Paket, eigener Anlass, menschlicher Label-Aufwand).

## Fact-Lock (erstverifiziert an der realen DB und am committeten Korpus)

Gegen die Wegwerf-Postgres `postgresql://mrr@127.0.0.1:54329/mrr_test`, Schema
`mrr_k1t04_real_run_v2` (76 domain_events, Kette VERIFIED), und den committeten
Korpus geprüft:

- Die **Kriterien** sind ein *finalisiertes* Objekt: `ConceptCharter`-Eintrag
  `instantiate-vs-reference-v1` (Entscheidungsprozedur mit explizitem `qualifies`)
  — die „Validierung erst nach Kriterien-Finalisierung"-Bedingung (A4) ist hier
  bereits als Objektzustand gegeben, nicht erst herzustellen.
- Die **Pipeline-Klassifikation** liegt als `evidence_relation` in
  `corpora/model-collapse/corpus-entries.json` (18 Einträge; `supports`/
  `contradicts`; `applies_to_analysis` trennt Theorie [3] von Instanziierung [15]).
  Stichprobe gegen die versiegelten `EvidenceMatrix.rows[].extraction.
  classification_basis` in der DB: theory-shumailov → `supports` in beiden. Die
  committete Datei spiegelt die versiegelte Klassifikation (Git ist für die
  committete Eingabe autoritativ; die DB-Objekte sind für die versiegelten Bytes
  autoritativ — für dieses Sample konsistent).
- Der **blinde Zweitdurchgang**: `blind-returns.json`, `{works: 15, papers: 3}`,
  Label-Raum `{instantiates, references-only, qualifies}`; `qualifies` **0×**
  verwendet. Verknüpfbar über `item` (A1–A15, B1–B3) + `title` zu den
  `entry_id`s der corpus-entries; `comparison.md` ist die vorhandene manuelle
  Ausrichtungs-/Crosswalk-Tabelle und dient als **Orakel** (18/18) für den
  Akzeptanztest (κ pro Stratum muss 1,0 ergeben).
- **Kein Metrik-Code existiert** irgendwo (kein kappa/krippendorff/f1/sklearn);
  `numpy`/`scipy`/`scikit-learn`/`statsmodels` sind **keine** Abhängigkeiten. Das
  Repo bevorzugt handgeschriebene Domänenlogik → N1-T01 rechnet κ/α/F1 in
  geschlossener Form von Hand, **ohne** neue Abhängigkeit (kein neuer
  `security-check`-/Audit-Angriffspunkt).
- **Spec-Anker:** `MRR-MTH-013` (Modell-Output ist Vorschlag, autoritativ nur über
  deterministische Regeln + unabhängige Prüfung), `MRR-MTH-016`, `MRR-FR-077`
  (Dissens erhalten); `docs/spec/05_EVALUATION_AND_ACCEPTANCE.md` §8 (eingefrorene
  Evaluationsprofile, „no use of the test labels in prompts") und §10 (menschliche
  Adjudikation, „disagreement is not automatically error"); `08_RESEARCH_METHOD_
  KERNEL.md` §5 (systematic_evidence_synthesis).

## Architektur-Platzierung (an bestehende Konvention angedockt)

- `packages/domain/mrr/domain/agreement.py` — **reine** Funktionen (keine
  Framework-Deps, DB-frei, hypothesis-testbar): Konfusionsmatrix, beobachtete
  Übereinstimmung, Cohen-κ, gewichtetes κ (linear/quadratisch), Krippendorffs α
  (nominal), Mehrheits-Baseline, per-Kategorie P/R/F1.
- `packages/domain/mrr/domain/agreement_report.py` — Pydantic-v2-Projektionsmodell
  (`MRRModel`, `extra="forbid"`): stratifizierter Report + Crosswalk-Hash +
  Power-Marke + Ehrlichkeits-Header. **Kein** persistiertes `BaseObject`, **kein**
  `schemas/*.schema.json`-Spiegel, wird **nie** in den Objektspeicher geschrieben —
  eine Projektion wie der Research-Report (rule 7 / „never the primary research
  record").
- `services/control_plane/mrr/services/validation/service.py` — `ValidationService`:
  liest die zwei Klassifikationen + die deklarierte Ausrichtungs-/Crosswalk-Datei,
  baut den Report, rendert deterministisch JSON + Markdown (atomarer `os.replace`,
  NFR-012-Ordering, Exit 0/2/3 wie die anderen CLIs).
- `services/control_plane/mrr/services/cli/validation_main.py` +
  **zwei Zeilen** in `cli/main.py` (register + dispatch) → `mrr validate agreement`.
- Die deklarierte Ausrichtungs-/Crosswalk-Datei wird als **committetes,
  versioniertes Fixture** unter `corpora/model-collapse/verification/` abgelegt
  (nicht erfunden: ihre Zeilen sind die von `comparison.md`), gehasht und im Report
  ausgewiesen — die Übereinstimmung ist *relativ zu diesem deklarierten Crosswalk*
  definiert und damit contestierbar.

## Zerlegung (use-first: nur T01 wird jetzt gebaut)

- **N1-T01 (jetzt):** Agreement-Metrik-Kern + `mrr validate agreement`, read-only,
  stratifiziert, Crosswalk-als-Eingabe, Reliabilität explizit (nicht Validität),
  Power-Marke. Orakel: `comparison.md` (κ = 1,0 pro Stratum).
- **N1-T02 (benannt, nicht abgeleitet):** menschliches Gold-Label-Objekt mit
  Finalisierungs-Gate als Objektzustand + Validitäts-Metriken gegen Gold. Braucht
  menschlichen Label-Aufwand → eigener Anlass.
- **N1-T03 (benannt, nicht abgeleitet):** Prompt-Stability-Harness (Paraphrasen-Set,
  Re-Run des Klassifikators, Krippendorffs α über Paraphrasen). Berührt den
  LLM-Adapter; ist das Stück, das die GEPA-artige Optimierungsschleife von Routine 2
  speist. Größere Fläche → eigener Anlass.

## Ausdrücklich NICHT in N1-T01

Kein persistiertes Report-Objekt, kein `schemas/**`, keine Migration, kein
DB-Schreibzugriff, kein LLM-Aufruf, keine menschlichen Gold-Labels, keine
Prompt-Stability, kein neuer Dependency. Kein Pooling der Strata zu einer
Schlagzeilen-κ (die gepoolte Zahl wird höchstens mit „methodisch unzulässig, nur zur
Anschlussfähigkeit an comparison.md gezeigt" markiert, nie als Befund geführt).

## Offene Owner-Entscheidungen (unverändert, nichts drängt)

Aus dem Roadmap-Entwurf §Offene Owner-Entscheidungen weiterhin offen und **nicht**
durch diese Ableitung berührt: erste Joint Inquiry als Routine-1-Anlass (E5/E6),
Routine 2 als erstes nächtliches Deployment, K2-Tor-Wiedervorlage,
Hammond-Adjudikation, erstes A4-Release, Artefakt-Blob-Dauerhaftigkeit (Befund 1),
Repo-Öffnung (berührt die „source code open"-Byline von On Record).
