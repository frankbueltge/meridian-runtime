# N2-Ableitung: Citation-/Claim-Audit, erstes Paket N2-T01 (2026-07-24)

**Status:** Entschieden — der Owner (Frank) hat in Session vom 2026-07-24 N2 als
nächsten Bau freigegeben („mach doch beide und erst a": zuerst N2, danach die
Hammond-Adjudikation) und den Nutzungsanlass gesegnet (die eigenen, jetzt
öffentlichen Quellen prüfen). Governance-Commit vor dem Bau; der Code-Merge nach
`main` bleibt an eine ausdrückliche Owner-Freigabe gebunden.

## Vorgeschichte: warum N2 und nicht „N1-T01 auf run2"

Der geplante nächste Schritt war „N1-T01 auf `run2-corroboration-floor`" für ein
erstes echtes κ mit Streuung. Der Fact-Lock hat das **widerlegt**: run2 ist kein
Per-Item-Klassifikations-Datensatz, sondern **der Hammond-Dissens selbst** —
`VerificationResult`-Objekte mit pass/fail-Empfehlungen zu *einem* Claim
(works-hammond-v3-model-collapse), stabil über beide Sensitivitäts-Mirror:
blind **pass** (0,85), Ulysses **fail** (0,80). Als Agreement-Aufgabe wären das
n=2, beide Rater degeneriert (je eine Kategorie) → κ undefiniert. N1-T01 darauf
zu zwingen wäre Theater; run2 gehört zur **Hammond-Adjudikation** (danach). Im
aktuellen Archiv gibt es keinen Klassifikations-Lauf mit echter Per-Item-Streuung
— N1-T01 wartet auf einen künftigen härteren Lauf. N2 ist der produktive Bau mit
realem Anlass.

## Die Entscheidung

1. **N2 zuerst gebaut**, danach die Hammond-Adjudikation (Owner-Reihenfolge).
2. **use-first bindend:** N2 wird zerlegt; nur das Sub-Paket mit realem, heute
   erfüllbarem Anlass (N2-T01) wird jetzt gebaut. N2-T02/T03 sind benannt, nicht
   abgeleitet.

## Der Nutzungsanlass (real, heute erfüllbar, reflexiv)

Die öffentliche `/e2e-automation`-Survey (heute live) zitiert **8 Primärquellen**.
Ihr eigener Gegenstand ist, dass KI-Forschungssysteme Zitate fabrizieren (die
dokumentierte Feldlücke: Zitations*genauigkeit* 40–80 %, mithin 20–60 %
fehlerhafte Zitate). **Eine Survey, die vor Zitat-Fabrikation warnt,
muss ihre eigenen Zitate aushalten** — das ist der reflexive erste Einsatz von N2.

**Fact-Lock (diese Session real durchgeführt):** alle 8 Identifier gegen die
offenen Metadaten-APIs aufgelöst — 7 arXiv-IDs über die arXiv-API, die Nature-DOI
über Crossref. **Ergebnis: 8/8 lösen auf**, die aufgelösten Titel passen zu den
Labels/dem Record (Nature-DOI → „Towards end-to-end automation of AI research",
Nature 651:914–919, exakt wie im Record). Das reale Ergebnis ist committet als
`corpora/e2e-survey/verification/resolution-snapshot.json`; das Zitat-Manifest
(wörtlich von der Seite transkribiert) als `corpora/e2e-survey/citations.manifest.json`.

## Die zentrale Ehrlichkeits-Unterscheidung: Existenz ≠ Bestätigung

Das N2-Analogon zu N1s „Reliabilität ≠ Validität". N2-T01 prüft, ob die zitierte
Referenz **existiert** (Identifier löst zu einem realen registrierten Werk auf)
und ob der **Titel** stimmt. Es prüft **NICHT**, ob die Quelle die Behauptung
**trägt**, für die sie zitiert wird (das ist N2-T02, Support-Prüfung — schwerer,
LLM/menschlich), und **NICHT**, ob eine ihr zugeschriebene Zahl konsistent ist
(N2-T03). „8/8 lösen auf" heißt: die Papiere existieren und sind korrekt betitelt
— **nicht**, dass die Behauptungen der Survey gedeckt sind. Der Report beschriftet
sich entsprechend und behauptet keine Support-Verifikation. Existenz ist die
mechanisch prüfbare Klasse (extern verifizierbar, MRR-artig); genau sie füllt die
Zitationslücke (Zitations*genauigkeit* 40–80 %).

## Architektur-Platzierung (an bestehende Konvention + N1-Muster angedockt)

- `packages/domain/mrr/domain/citation_audit.py` — **reine**, no-network-Funktionen:
  Identifier-Wohlgeformtheit (arXiv-ID-Muster `YYMM.NNNNN`, DOI `10.\d+/…`, URL),
  Titel-Normalisierung + -Vergleich, Status-Klassifikation je Zitat.
- `packages/domain/mrr/domain/citation_audit_report.py` — Pydantic-v2-Projektion
  (`MRRModel`, `extra="forbid"`): je-Zitat-Verdikte + Summen + Ehrlichkeits-Header
  (prüft Existenz+Titel, NICHT Support/Zahlen) + Snapshot-Hash. Kein persistiertes
  Objekt, kein `schemas/*`-Spiegel, nie in den Objektspeicher (Projektion, rule 7).
- `services/control_plane/mrr/services/citation_audit/service.py` — read-only,
  **öffnet KEINE Netzverbindung**: liest Manifest + committeten Resolution-Snapshot,
  klassifiziert, rendert deterministisch JSON+Markdown (atomarer `os.replace`,
  NFR-012, Exit 0/2/3).
- `services/control_plane/mrr/services/cli/citation_audit_main.py` + 2 Zeilen in
  `cli/main.py` → `mrr audit citations`.
- **Statuswerte nie kollabiert** (AGENTS-Regel gegen `unknown/not_found/
  contradicted/failed`-Vermischung): `resolved` / `not_found` (potenzielle
  Fabrikation) / `title_mismatch` (potenzielle Fehlzuschreibung) / `unverifiable`
  (nur Metadaten/paywalled) / `malformed` (Identifier ill-geformt).

**Netz-Trennung wie im Repo-Muster:** der Netz-Abruf (Auflösung) ist der gated
Fetch, EINMAL ausgeführt und als Snapshot committet (Git = Archiv); das Audit-
Werkzeug selbst ist deterministisch und no-network — es liest nur den Snapshot.
Reproduzierbar: dieselben API-Abfragen erzeugen denselben Snapshot.

## Fact-Lock (MRR-Zitierkonvention)

`corpora/model-collapse/corpus-entries.json` zeigt die MRR-Identifier-Form:
`identifiers: {doi, repository_id (arXiv-URL), archive_id, local_asset_id}`. Das
Manifest übernimmt `arxiv`/`doi` konsistent. `schemas/source-record.schema.json`
existiert; N2-T01 baut aber KEIN persistiertes Objekt (die Survey-Zitate sind
keine MRR-SourceRecords — externe Prosa-Zitate). Kein `audit`-Service/CLI
vorhanden → N2-T01 legt `mrr audit citations` neu an (wie N1-T01 `mrr validate
agreement`). Kein Metrik-/Audit-Code existiert; kein neuer Dependency (Muster-
Prüfung + Titel-Vergleich sind handgeschrieben, wie bei N1).

## Zerlegung (use-first: nur T01 jetzt)

- **N2-T01 (jetzt):** Existenz- + Titel-Audit über committetes Manifest + Snapshot,
  deterministisch, no-network. Erster Prüfling: die 8 Zitate der `/e2e-automation`-
  Survey (8/8 real aufgelöst — Orakel für den Akzeptanztest).
- **N2-T02 (benannt):** gated Fetch-Skript, das den Snapshot reproduzierbar
  erzeugt (Netz, pipeline-artig); Ausweitung auf die ~47 Record-Zitate und die
  model-collapse-SourceRecords; interne Verknüpfungs-Integrität (jeder
  EvidenceAnchor → realer SourceRecord; „zitiert-aber-nicht-verankert" nach
  AGENTS verboten).
- **N2-T03 (benannt):** Support-Prüfung (trägt die Quelle die Behauptung?) +
  Zahlen-Konsistenz — LLM/menschlich, das schwere Stück; speist Routine 2.

## Ausdrücklich NICHT in N2-T01

Kein Netzzugriff im Werkzeug, kein persistiertes Objekt, kein `schemas/**`, keine
Migration, kein LLM, keine Support-Prüfung, keine Zahlen-Prüfung, kein neuer
Dependency. Existenz + Titel, mehr nicht — ehrlich beschriftet.

## Offene Owner-Entscheidungen (unverändert)

Danach: **Hammond-Adjudikation** (run2: blind-pass vs. Ulysses-fail — adjudizieren
oder Dissens stehen lassen, wie On Record es tut). Weiterhin offen: Routine 2 als
erstes Nachtdeployment, erste Joint Inquiry (Routine 1 / E5/E6), K2-Tor, erstes
A4-Release, Artefakt-Blob-Dauerhaftigkeit (Befund 1).

---

**Korrektur 2026-07-25 (aus der N2-T03-Ableitung).** Die Zahl 40–80 % war an
zwei Stellen als *Lücke* bzw. Fabrikationsrate zugeschrieben. DeepTRACE
(arXiv 2509.04499) misst sie laut Abstract wörtlich als „citation **accuracy**
ranging from 40--80% across systems" — die **Genauigkeit**, nicht die Lücke; die
Lücke wäre 20–60 %. Die Stelle ist richtiggestellt. Herleitung, alle Fundstellen
und der Quellbeleg stehen in `docs/design/2026-07-25-n2-t03-derivation.md`.
Die Fundstelle in `corpora/e2e-survey/citations.manifest.json` bleibt auf
Owner-Entscheidung vom 2026-07-25 **unverändert**, weil ihr sha256 in
`corpora/e2e-survey/observation-batch.v1.json` als Integritäts-Anker gepinnt ist —
die Abweichung ist dort bewusst stehen gelassen und hier vermerkt, nicht behoben.
