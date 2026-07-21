# Session-Handoff — Tagessession nach dem ersten echten Lauf (2026-07-21, abends)

**Status:** Übergabedokument für eine frische Session; selbstständig lesbar.
Vorgänger: `2026-07-21-session-handoff-first-real-run.md` (Nachtsession, PRs #40–#53).

## 1. Was in dieser Session passiert ist

Vier PRs, alle im vollen Zyklus (Packet ableiten → Review/Approval durch die
Hauptsession mit dokumentierter reviewer_resolution → Implementierung im
Worktree-Agenten → unabhängiger Review-Agent auf dem Merge-Baum, alle Gates →
Findings gefixt → grüner CI-Check → Merge):

- **PR #54 / E9-T00** (`b07d1ab`): Pre-Hardening-Batch, sieben Review-Follow-ups
  aus E6/K0/K1. Kernstücke: `correction.notification_received`-Event (schließt
  die „empfangen und no-op'd ohne Spur"-Audit-Lücke; Keying auf
  `notification_id`/Revision 1 per Reviewer-Entscheid), maxItems-512-Bound
  (DoS), Re-Notification-Spec-Entscheid, dokumentierte Eventual-Consistency
  (§5.3-Lesart provisorisch, E9-T01-Wiedervorlage), zwei Testlücken,
  EvidenceCrateSealer-Erweiterung (Crates tragen source_records/
  evidence_anchors/proposed_claims jetzt selbst). Eine Stop-Condition
  (Property-Test-Fixture ohne record_event) per Amendment `980d2dd` aufgelöst.
- **PR #55 / E1-T03b** (`a3a78ba`): ADR-0010 Schritt 1 — optionales
  `classification`-Feld auf `baseObject` als SEPARATES sechswertiges
  `BaseObjectClassification`-Literal (verhindert TaskBundle-Schema/Pydantic-
  Drift), keine Migration (per Round-Trip bewiesen), Hash-Rückwärtskompatibilität
  bewiesen. Empirische Zählung: 29 Entities / 26 BaseObject-Subklassen /
  25 erben + TaskBundle verengt + 3 bewusste Nicht-Subklassen.
- **PR #56 / E9-T00b** (`4e19810`): DRY-Konsolidierung — fünf Trust-Resolver
  auf `mrr.domain.trust_resolution` (injizierte Fehler-Konstruktoren via
  Protocols, Bedingungsreihenfolge byte-erhalten, mechanisch gegen den alten
  Code bewiesen), neunzehn identische UoW-Binder-Kopien auf einen expliziten
  Re-Export (`as X as X`, mypy-no_implicit_reexport-fest); 39 abhängige
  Dateien nachweislich unangetastet. Netto ±0 Zeilen.
- **PR #57 / K1-T03b** (`b3ca172`): MRR-MTH-018 wird AUSGEFÜHRT — Variationen
  re-runnen nur die vier Klassifikationsstufen (Extraktion fixiert, Spy-Test
  beweist unveränderte Callable-Aufrufe), Sidecar-Artefakt pro Variation,
  Ergebnisse als optionales `EvidenceMatrix.sensitivity_analysis_results`
  (kein neues Objekt, keine Migration), streng berichtend (nie Claim-wirksam),
  symmetrischer Fail-Closed-Coverage-Check. **Wichtigste Episode des Tages:**
  Der Check deckte auf, dass der ECHTE K1-T04-Korpus selbst
  `["model-collapse-mechanism-v1"]` deklariert, ohne dass je Parameter
  autoriert wurden — die zwei Real-Run-E2E-Tests wurden per Ruling (Amendment
  in K1-T03b.yaml) ehrlich auf Fail-Closed umgestellt (umbenannt, mit
  Begründungs-Docstrings). Grandfather-Ausnahme und Schnellschuss-Noop-Variation
  ausdrücklich verworfen. Das versiegelte Archiv `mrr_k1t04_real_run_v2` ist
  unberührt (Review-Fingerprint: objects=64, edges=7, domain_events=73,
  Digest identisch vor/nach kompletter Suite).

Außerdem, ohne PR:

- **K2-Tor entschieden — VERTAGT** (`docs/design/2026-07-21-k2-gate-decision.md`,
  `97ce3e9`): drei Wiedervorlage-Trigger; MTH-018-Ausführung (erledigt, #57)
  und unabhängige Claim-Verifikation vor neuen Claim-Formen.
- **Site-Kopplungs-Entscheidungsvorlage** committet in frankbueltge.de
  (`docs/design/2026-07-21-mrr-site-kopplung-vorschlag.md`, `7328223`):
  §2-Gate PARTIAL, vier Optionen, Empfehlung „noch nicht" (Urheberschafts-
  und Encounter-Fragen offen). **Werk-Entscheidung des Owners, aussteht.**
- **Governance-Selbstkorrekturen** (alle zitierbar): E9-T00s Zitierfehler in
  E9-T00bs Derivation korrigiert; E1-T03b-Annotation (i) per Amendment
  `eb7bc4d` ZURÜCKGEZOGEN (Domain→Contracts-Importe sind präzedent und
  lint-legal — zwölf+ bestehende Dateien; Zyklen zählen auf Modul-, nicht
  Paket-Ebene; die künftige MTH-012-Wiring-Task darf den direkten Import
  nutzen); K1-T03bs interne allowed_paths-Inkonsistenz
  (evidence_matrix/service.py) per Review aufgelöst.

Suite-Stand: **2.110 Tests** (1.371 unit + 111 property + 414 contract +
196 integration + 18 e2e), alle grün auf `b3ca172`.

## 2. Delegation (WICHTIG für eine neue Session)

Der Owner hat dieser Session am 2026-07-21 im Chat delegiert: Merges
reviewter, vollständig grüner PRs („Selbst mergen") und die Wahl der
nächsten Arbeitsschritte („entscheide du das bitte"). Dokumentiert im
Governance-Commit `55687ee`. **Diese Delegation ist session-gebunden — eine
neue Session holt sie NEU ein**, wie schon der Vorgänger-Handoff verlangte.

## 3. Infrastruktur

Wegwerf-Postgres 16 läuft weiter auf 127.0.0.1:54329 (User `mrr`, DB
`mrr_test`, timezone=UTC — Pflicht wegen Event-Hash-Kette). Neustart-Rezept
im Vorgänger-Handoff §2. Das versiegelte Schema `mrr_k1t04_real_run_v2`
liegt darin — NIE anfassen. Alle Session-Worktrees sind entfernt, alle
Branches gemergt und gelöscht; einziges Checkout ist main.

## 4. Nächste Arbeit, in empfohlener Reihenfolge

1. **Echte Alternativ-Operationalisierung der Model-Collapse-Frage autorieren**
   (zweiter `ConceptCharterEntry` + Variations-Parameter) — Forschungs-Inhalt,
   nicht Engineering; Owner-/Praxis-Beteiligung sinnvoll. Entsperrt die
   sinnvolle Sensitivitäts-Analyse auf dem echten Korpus UND stellt die
   Vorwärts-Reproduktion des Real-Laufs wieder her (aktuell bewusst
   fail-closed, siehe #57).
2. **Claim-Verifikations-Design**: Wer verifiziert die beiden realen Claims
   (contested/draft), ohne AGENTS-Regel 8 zu verletzen? Eigene Design-Frage,
   vor neuen Claim-Formen (K2-Entscheid).
3. **ADR-0010 Schritte 2/3 + MTH-012-Wiring** — mit dem KORRIGIERTEN
   Schichtungs-Stand (Amendment `eb7bc4d`): der direkte Import ist legal.
4. **E7/E8** (Roadmap): E8 (Exporte) ggf. vor E7, falls die Site-Kopplung
   kommt — Exporte würden genau das Claim-Landscape transportfähig machen.
5. **K2-Wiedervorlage** nur bei einem der drei Trigger aus der
   K2-Entscheidungsnotiz.
6. **E9-T01** (Threat-Model) — muss u. a. das dokumentierte
   Eventual-Consistency-Fenster aus E9-T00 Item 4 ausdrücklich wieder aufmachen.

## 5. Offene Urteilsfragen (unverändert aus dem Vorgänger, soweit nicht erledigt)

- E9-T00s Item-1-Keying und §5.3-Lesart: ENTSCHIEDEN (siehe #54/Packet).
- MTH-018-Ausführung: ERLEDIGT (#57).
- Weiter offen: `executor_task_family`-Cross-Check; Re-Notification-MECHANISMUS
  (nur Semantik-Entscheid existiert); CorrectionResponse-Rücktransport (E6-Lücke,
  E6-T07-Kandidat); Site-Kopplung (Owner); die fünf offenen Fragen der
  Site-Vorlage (nur Owner).

## 6. Bindende Regeln (unverändert)

AGENTS.md-Disziplin; ein Packet pro Branch/PR; nie ohne Owner-Go (bzw. neu
eingeholte Delegation) auf main mergen; keine KI-Produkt-Credits in Git;
Git-Identität `Frank Bültge <f.bueltge@gmail.com>` (NIE `frank@bueltge.de` —
andere reale Person); Subagenten default Sonnet; Lizenz noncommercial;
Archiv-Schemata und committete Tages-/Forschungsartefakte unantastbar.
