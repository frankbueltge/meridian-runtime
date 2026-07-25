# R2-Ableitung: Routine 2 (Meta-Forschung), erstes Paket R2-T01 (2026-07-25)

**Status:** Entschieden — der Owner (Frank) hat in Session vom 2026-07-25 die
Entscheidung „welcher erste Zuschnitt von Routine 2 vor dem Hintergrund der
aktuellen Entwicklung Sinn macht" **und** die Benennung des Nutzungsanlasses
ausdrücklich an die Session delegiert („das musst du entscheiden, was sinn macht"
/ „musst auch du entscheiden") — dieselbe Delegation wie bei der N1-Reihenfolge am
2026-07-24. Dieses Dokument fixiert die Entscheidung und leitet daraus das erste
gebaute Paket **R2-T01** ab. Es ist der Governance-Commit vor dem Bau; der
Code-Merge nach `main` bleibt an eine ausdrückliche Owner-Freigabe gebunden.

## Die Voraussetzung ist erfüllt — aber nicht die für die *ganze* Routine 2

Record III (`2026-07-24-primaerquellen-selbstoptimierung.md`) macht **eine**
Governance-Voraussetzung für Routine 2 nicht verhandelbar: der Evaluator gehört
**außerhalb der Modifikationsfläche**, eingefroren; **kein Optimierer bewertet
seine eigene Optimierung** (DGM-Marker-Vorfall). Der eingefrorene N1-Evaluator
existiert jetzt (N1-T01, `mrr validate agreement`, read-only, deterministisch).

Der Roadmap-Entwurf (`2026-07-24-capability-roadmap-entwurf.md`, §Die zwei
Routinen) bündelt in Routine 2 aber **zwei** Dinge mit sehr unterschiedlicher
Reife:

1. **Read-only-Beobachtung des Felds** (hash-verankerte Quellen, fail-closed) →
   Vorschläge als Task-Packets vor dem menschlichen Gate.
2. Die **GEPA-artige Prompt-/Kriterien-Optimierungsschleife** gegen den
   eingefrorenen N1-Harness.

**Der Fact-Lock zeigt: (2) hat heute kein Substrat, an dem sich etwas bewegen
könnte.** Der eingefrorene Evaluator kann nur bewerten, was im Archiv liegt, und
das einzige Klassifikations-Material ist der model-collapse-Satz (18/18 perfekt →
κ trivial 1,0 bzw. undefiniert) und run2 (n=2, degeneriert — der Hammond-Dissens).
N1-T03 — der Prompt-Stability-/Klassifikator-Re-Run-Harness, der die Schleife
**speist** — ist benannt, aber **nicht gebaut** (verifiziert: nur N1-T01 und
N2-T01 als Pakete vorhanden). Prompts gegen eine bei 1,0 festgenagelte Metrik zu
optimieren wäre genau das „Theater", vor dem die N2-Ableitung warnt. **Die
GEPA-Schleife ist damit substrat-blockiert und wird NICHT jetzt gebaut.**

**(1) ist heute baubar** — read-only, no-network, deterministisch, gegen bereits
committete, hash-verankerbare Eingaben — und ist der risikoärmste Zuschnitt, den
der Roadmap-Entwurf selbst empfiehlt.

## Die Entscheidung

1. **Routine 2 wird als erstes reales (read-only) Nachtdeployment gezogen** —
   aber ehrlich zerlegt auf das heute Baubare: **nur R2-T01**.
2. **R2-T01 = die deterministische, no-network, *fail-closed* Beobachtungs-Intake-
   und-Integritäts-Gate über eine committete, hash-verankerte Feld-Charge**, die
   den **eingefrorenen N2-Evaluator wiederverwendet** (kein Nachbau). Sie holt
   **nicht** ab (Fetch = N2-T02), ruft **kein** LLM, emittiert **keinen**
   Selbstmodifikations-Vorschlag, optimiert **nichts**.
3. **use-first bindend** (`2026-07-22-nutzungsentscheidung-e9-vertagt.md`): R2 wird
   zerlegt; nur das Sub-Paket mit realem, heute erfüllbarem Anlass wird gebaut.
   R2-T02/T03 sind benannt, nicht abgeleitet.

## Der Nutzungsanlass (real, heute erfüllbar, reflexiv)

**Der benannte Beobachtungs-Gegenstand ist die Research-Automation-Front** — der
Anschluss, den der Roadmap-Entwurf ausdrücklich sucht („Anschluss an die
e2e-Automation-Front"). Konkret, heute: ihre **bereits committete, hash-verankerte
Charge** `corpora/e2e-survey/` (Manifest + Resolution-Snapshot, an N2-T01
committet). Routine 2s erste Feld-Beobachtung ist, **genau diese Charge
integritätszuprüfen und zu auditieren, fail-closed**, und das Ergebnis als
contestierbares Artefakt festzuhalten. Das ist derselbe reflexive Erst-Einsatz wie
bei N2-T01 (eine Survey, die vor Zitat-Fabrikation warnt, hält ihre eigenen Zitate
aus) — jetzt eine Stufe höher: die Charge selbst wird zum hash-verankerten
Beobachtungs-Objekt.

## Die zentrale Ehrlichkeits-Unterscheidung: Beobachtung ist nicht Optimierung

Das R2-Analogon zu N1s „Reliabilität ≠ Validität" und N2s „Existenz ≠
Bestätigung". R2-T01 **beobachtet** eine hash-verankerte Charge fail-closed und
lässt den eingefrorenen Evaluator darüber laufen. Es **schlägt nichts vor**
(Vorschlags-Emission ist R2-T02, hinter dem menschlichen Gate) und **optimiert
nichts** (die GEPA-Schleife ist R2-T03, substrat-gated hinter N1-T03 + einem Lauf
mit echter Per-Item-Streuung). Entscheidend: **R2-T01 enthält keinen Modell-
Schritt** — die Record-III-Grenze „Quellenausfall endet deterministisch, BEVOR ein
LLM ihn sieht" ist hier **strukturell trivial erfüllt**, weil hinter dem Gate (noch)
nichts Gefährliches steht. Genau deshalb ist jetzt der sicherste Zeitpunkt, das Gate
zu bauen: bevor der Optimierer existiert, nicht danach.

## Fact-Lock (erstverifiziert an den committeten Dateien und der realen DB)

- **Der eingefrorene N2-Evaluator ist live und wiederverwendbar:**
  `CitationAuditService().build_report(manifest_path, snapshot_path) ->
  CitationAuditReport` (DB-frei, no-network) — R2-T01 ruft ihn read-only auf,
  reimplementiert nichts.
- **Die Charge hat KEINE Per-Quelle-Hashes.** Manifest und Snapshot sind zwei
  committete JSON-Dateien; die einzige real vorhandene Integritäts-Verankerung ist
  der **sha256 der Dateien selbst** (Git ist für die committeten Bytes autoritativ).
  R2-T01s Gate verankert darum die **Datei-Hashes** — nicht einen Per-Quelle-Hash,
  den es nicht gibt. Das ist ehrlich und deckt exakt die Record-III-Anforderung
  („hash-verankerte Quellen, fail-closed"). Die realen Anker, an der Derivation
  berechnet:
  - `citations.manifest.json` → `sha256:8a706a96500fcd8177ed048f4a81ac605fa5aabc67cea7b5fe1ef1e90855691b`
  - `verification/resolution-snapshot.json` → `sha256:32b6b74f881a73b012c1d7ccd822f1090f1e4f3ecbd4b47c3af2a4db6472dfc0`
- **N2 echot den Snapshot-Hash im Format `sha256:<hex>`** — der Descriptor
  übernimmt dieselbe Konvention.
- **N2s typisierte Verweigerung `MissingResolutionError`** deckt die
  Manifest↔Snapshot-Vollständigkeit bereits ab (eine Zitat-ohne-Resolution → Exit 3);
  R2-T01 fügt darüber nur die **Hash-Anker-Prüfung** hinzu.
- **Kein `observe`/`field`/`routine2`-Service oder -CLI existiert** → R2-T01 legt
  `mrr observe field` neu an (wie N1-T01 `mrr validate agreement`, N2-T01 `mrr audit
  citations`). **Kein neuer Dependency** (Hashing = stdlib `hashlib`, wie im
  N2-Service bereits genutzt).
- **Spec/Muster-Anker:** AGENTS.md Regel 7 (kein Modell-Output autoritativ — hier
  gibt es keinen), Regel 8 (kein Executor verifiziert sein eigenes Ergebnis — der
  Evaluator ist wiederverwendet, nicht neu bewertet), das
  Statuswerte-nie-kollabieren-Verbot; die N2-Report-Projektion
  (`citation_audit_report.py`, `MRRModel`/`extra="forbid"`, fixer Ehrlichkeits-
  Header als `Literal[True]` + Modulkonstante) als exaktes Vorbild.

## Architektur-Platzierung (an N1/N2-Muster angedockt)

- `packages/domain/mrr/domain/field_observation.py` — **reine**, no-IO-Funktionen:
  die Descriptor-Eingabeform (dataclasses), die pure Anker-Prüfung
  (`check_anchor(declared_sha256, actual_sha256) -> AnchorCheckResult`), der
  **geschlossene** Status-Satz, und die typisierte `IntegrityGateError`
  (fail-closed-Verweigerung bei Mismatch). Nimmt bereits berechnete Hashes entgegen
  (das Datei-Lesen/Hashen liegt im Service — genau wie `citation_audit.py` „nimmt
  bereits geladene Werte").
- `packages/domain/mrr/domain/field_observation_report.py` — Pydantic-v2-Projektion
  (`MRRModel`, `extra="forbid"`): Charge-Identität + Integritäts-Gate-Ergebnisse +
  der **eingebettete** `CitationAuditReport` (Domain→Domain-Import) + fixer
  Ehrlichkeits-Header. **Kein** persistiertes Objekt, **kein** `schemas/*`-Spiegel,
  nie in den Objektspeicher (Projektion, rule 7).
- `services/control_plane/mrr/services/field_observation/service.py` — read-only,
  **öffnet KEINE Netz- und KEINE DB-Verbindung**: liest den Descriptor, berechnet
  den realen sha256 jeder deklarierten Eingabe (die einzige IO), lässt das pure Gate
  laufen; **bei Mismatch: typisierte `IntegrityGateError` VOR jedem Downstream-
  Schritt** (der N2-Aufruf wird dann gar nicht erreicht); bei Pass ruft es den
  wiederverwendeten `CitationAuditService.build_report(...)` und baut den Report.
- `services/control_plane/mrr/services/cli/field_observation_main.py` + **2 Zeilen**
  in `cli/main.py` → `mrr observe field`.

## Zerlegung (use-first: nur T01 jetzt)

- **R2-T01 (jetzt):** fail-closed Beobachtungs-Intake + Integritäts-Gate über
  committete, hash-verankerte Charge; frozen N2-Evaluator wiederverwendet;
  deterministisch, no-network, no-LLM, kein Vorschlag, keine Optimierung.
  Erst-Prüfling: die committete `corpora/e2e-survey/`-Charge (8/8 resolved,
  Anker matchen — Orakel für den Akzeptanztest).
- **R2-T02 (benannt, nicht abgeleitet):** der Vorschlags-Emitter (Beobachtung →
  Entwurfs-Task-Packets **vor** dem menschlichen Gate) **plus** der gated nächtliche
  Netz-Fetch, der neue Chargen erzeugt (hier speist N2-T02s gated Fetch ein). Erst
  hier wird Routine 2 tatsächlich „nächtlich/neu"; erst hier gibt es einen
  Downstream-Schritt, den das Gate schützt.
- **R2-T03 (benannt, nicht abgeleitet):** der GEPA-artige Prompt-/Kriterien-
  Optimierungs-Vorschlagsgenerator gegen den eingefrorenen N1-Harness — **gated auf
  N1-T03 UND einen Klassifikations-Lauf mit echter Per-Item-Streuung** (beides
  existiert nicht). Kein Optimierer bewertet seinen eigenen Evaluator; Vorschläge
  nur als Task-Packets vor dem Gate; keine automatische Übernahme; keine
  Code-Selbstmodifikation.

## Ausdrücklich NICHT in R2-T01

Kein Netzzugriff, kein DB-Zugriff, kein LLM/Modell-Schritt, kein persistiertes
Objekt, kein `schemas/**`, keine Migration, kein neuer Dependency, kein
Vorschlags-Artefakt, keine Optimierung, keine Änderung an N1s/N2s Service-Interna
(nur Import-Wiederverwendung). Integritäts-Gate + Beobachtungs-Report über die
wiederverwendete N2-Auswertung, mehr nicht — ehrlich beschriftet.

## Offene Owner-Entscheidungen (unverändert, nichts drängt)

Weiterhin offen und **nicht** durch diese Ableitung berührt: erste Joint Inquiry
als Routine-1-Anlass (E5/E6), K2-Tor-Wiedervorlage, erstes A4-Release, Befund 1
(Artefakt-Blob-Dauerhaftigkeit), N1-T02/T03, N2-T02/T03. R2-T02 (Fetch +
Vorschlags-Emitter) und R2-T03 (GEPA-Schleife) sind benannt und warten auf ihre
jeweilige Voraussetzung bzw. einen benannten Anlass.
