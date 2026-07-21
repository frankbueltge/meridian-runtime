# Design-Memo: Wer verifiziert die zwei realen Claims?

**Status:** Analyse-Memo für die reviewende Session, keine Entscheidung. Alle Zitate wörtlich
aus den genannten Dateien; nichts an den Repos wurde verändert (reine Leseanalyse).

**Auftrag/Kontext:** `meridian-runtime/docs/design/2026-07-21-k2-gate-decision.md` vertagt K2
u. a. mit der Begründung „unabhängige Verifikation der beiden realen Claims NICHT ERFÜLLT
(`verification_ids` beidseitig leer, by design)" (Zeile 27, 38f.). Der Session-Handoff
(`2026-07-21-session-handoff-tagessession.md` §4 Punkt 2) macht daraus eine eigene
Design-Frage: „Wer verifiziert die beiden realen Claims (contested/draft), ohne AGENTS-Regel 8
zu verletzen?"

---

## 1. Die zwei realen Claims (Kurzfassung aus PR #53, `gh pr view 53`)

Lauf `mrr synthesis run` (Schema `mrr_k1t04_real_run_v2`, byte-identisch reproduziert) über
zwei extern kuratierte Atlanten — Theorie-Atlas `../irrtum-als-methode/atlas/atlas.json`
(87 Quellen, **Eigentum des Ulysses/Atelier-Kollektivs**) und Werk-Atlas
`../frankbueltge.de/src/data/atlas/werke.json` (214 Einträge, site-eigen) — erzeugt:

| Claim | Finding-Status | Claim-Status | Ceiling | verification_ids |
|---|---|---|---|---|
| `model-collapse-mechanism-theory-confirmation` (3 Theorie-Paper, Positivkontrolle) | `supported` | **`draft`** | `associational_unadjusted` | `[]` |
| `instantiation-vs-reference-classification` (15 Werk-Atlas-Kandidaten) | `contested` (1 stützt / 13 widerlegt-verifiziert / 1 pending) | **`contested`** | `associational_unadjusted` | `[]` |

Beide Claims sind gültige `Claim`-Objekte nach Vertrag — s. §3 unten, warum das so ist, obwohl
keine Verifikation vorliegt. Wichtig für Kandidat (d): Der **Theorie-Atlas gehört Ulysses**,
nicht Meridian — Meridians Feld-Kollektiv hat mit dem Lauf nichts zu tun (bestätigt in
`frankbueltge.de/docs/design/2026-07-21-mrr-site-kopplung-vorschlag.md` §0: „Der PR #53 …
ist davon komplett getrennt entstanden: kein Kollektiv-Session, kein Journal-Eintrag, kein
Proposer/Skeptic/Interlocutor-Zyklus — sondern ein Engineering-Task-Packet").

Der Lauf enthielt bereits einen informellen Check: „An independent reviewer verified the
research finding by hand against the source atlases and re-ran the pipeline twice with
byte-identical results — no blocking findings, merge recommended" (PR #53, Abschnitt „Review
follow-up fixes"). Das war jedoch ein **Engineering-Code-Review** auf dem Merge-Baum
(vgl. Tagessession-Handoff §1: „unabhängiger Review-Agent auf dem Merge-Baum, alle Gates"),
**kein** im Runtime-Datenmodell erfasstes `VerificationResult` — deshalb bleiben
`verification_ids` trotzdem leer. Der Check ist substanziell geschehen, aber nicht formal
eingetragen. Das ist der Kern des „minimalen ehrlichen ersten Schritts" in §4.

---

## 2. Was die bestehende Verifikations-Maschinerie JETZT trägt

### 2.1 Die Objekte

- **`VerificationResult`** (`packages/contracts/mrr/contracts/verification_result.py`) —
  Pflichtfelder u. a. `target_id`/`target_kind` (claim/run/artifact), `reviewer_id`,
  `reviewer_role`, `independence_profile` (sechs Pflichtdimensionen), `verification_type`
  (source/numeric/skeptic/reproduction/other), `checks_performed`, `evidence_inspected`,
  `recommendation` (pass/fail/inconclusive), `confidence`, `rationale`,
  `conflicts_of_interest`, optional `adjudication_relation`. Kein `signature`-Feld — nur
  create-only, keine Mutation (Docstring Z. 83–92: Uneinigkeit wird durch ein **zweites**,
  separates Objekt + `adjudication_relation` bewahrt, nie durch Überschreiben).
- **`IndependenceProfile`** (dieselbe Datei, Z. 118–131) — sechs Pflichtfelder:
  `principal`, `model_family`, `prompt_family`, `retrieval_path`, `code_path`,
  `data_access_path`. **Alle sechs sind einfache Strings, selbst deklariert vom
  Reviewer** — der Vertrag selbst sagt es explizit: „Independence is DECLARED here, not
  validated" (Z. 68–81). Es gibt keine Kryptografie, kein Auth-Binding, keinen Registry-Zwang
  hinter diesen Strings.

### 2.2 Wer darf eins erzeugen — der mechanische Rule-8-Gate

`services/control_plane/mrr/services/verification/service.py`
(`VerificationService.record`, Z. 173–241): der **allererste** Schritt, vor jedem
Datenbank-Schreiben, ist der Self-Verification-Gate:

```
if verification.reviewer_id == claim.proposer_id: raise SelfVerificationError(...)
if run_executor_id is not None and verification.reviewer_id == run_executor_id: raise SelfVerificationError(...)
```

Das ist MRR-FR-070 / AGENTS.md Regel 8 direkt umgesetzt (Docstring Z. 1–24 zitiert Regel 8
wörtlich). Es prüft nur **Identitäts-Ungleichheit** von `reviewer_id` gegen
`claim.proposer_id`/`run_executor_id` — beides selbst wieder freie String-URNs, siehe §2.4.

### 2.3 Der Unabhängigkeits-Check — was er WIRKLICH prüft

`packages/domain/mrr/domain/independence.py` — eine reine, deterministische Funktion,
**separat** vom Rule-8-Gate (Docstring Z. 10–27: „This module does NOT re-implement … the
self-verification gate … This module answers a narrower, different question"). Die Formel
(Z. 87–94, wörtlich):

```
NOT ( verifier.principal == producer.principal
      AND verifier.model_family == producer.model_family
      AND verifier.prompt_family == producer.prompt_family
      AND verifier.code_path == producer.code_path )
```

Disqualifiziert wird **nur**, wenn BEIDES zutrifft: gleiches `principal` UND ein
unverändertes „reasoning path"-Triple (`model_family`, `prompt_family`, `code_path`).
`retrieval_path`/`data_access_path` zählen bewusst zum „evidence access", nicht zum
„reasoning path" (Z. 56–86 begründet das als eine von zwei plausiblen Lesarten von Domain
2.13 — als **offene Spezifikationsfrage geflaggt**, nicht entschieden). Bereits **eine**
abweichende Dimension im Triple genügt, um als „independent" zu gelten — auch bei
identischem `principal`.

Ein separater Dedup-Schlüssel (`MRR-FR-076`, alle sechs Felder) verhindert, dass dieselbe
Konfiguration mehrfach als unabhängige Review gezählt wird — aber auch dieser Schlüssel
vergleicht nur deklarierte Strings.

**Wichtiger Fund: Dieser Check ist nirgends verdrahtet.** Docstring Z. 164–179: „Not wired
into ClaimService's supported-gate … `has_independent_verification` … This module is
therefore delivered as a standalone building block only." `ClaimService.to_supported`
(`services/control_plane/mrr/services/claim/service.py`, Z. 469 ff.) verlangt zwar
`verification_ids: list[Urn]` als Parameter — prüft aber **nicht**, dass diese URNs auf
reale, bestehende `VerificationResult`-Objekte zeigen, geschweige denn auf welche mit
`recommendation == "pass"` oder auf eine Mindestzahl unabhängiger Reviews. Es ist eine reine
strukturelle Nichtleer-Prüfung (`Claim._supported_requires_evidence_and_verification`,
`packages/contracts/mrr/contracts/claim.py` Z. 95–114).

### 2.4 „Principal"/„actor" — keine echte Identität, nur ein CLI-Flag

`services/control_plane/mrr/services/cli/synthesis_main.py` Z. 129: `--actor` ist ein freies
CLI-Argument („Actor URN recorded on every domain event"), ohne Default außer `None`,
ohne Authentifizierung. `synthesis_orchestration.py` Z. 1355 setzt `"proposer_id": actor`
direkt aus diesem String; `executor_id=resolved_node_id` (Z. 1007) ist ebenfalls nur ein
konfigurierter Node-Bezeichner. **Es gibt keinen kryptografischen oder organisatorischen
Mechanismus, der `principal`/`proposer_id`/`executor_id`/`reviewer_id` an eine tatsächlich
verschiedene Person, Maschine oder Praxis bindet** — wer die CLI aufruft, deklariert frei,
wer er ist. Das ist die technische Grundlage für das „Independence-Laundering"-Risiko in §5.

### 2.5 Verifikation vs. Ruling vs. Replikation — drei verschiedene Dinge

- **`MethodRuling`** (Spec 08 §3) lizenziert die **Sprache/Ceiling** eines Claims
  („issued deterministically where rules suffice, by attributed human review where they do
  not") — ein anderes Gate als `VerificationResult`. Beide realen Claims tragen bereits
  `ruled_by`-Kanten (`method_ruling_ids` beide `issued`, PR #53) — die Ceiling-Frage ist
  geklärt, die Wahrheits-/Beleg-Frage (Verification) nicht.
- **`ReplicationPlan`/MRR-MTH-014** (Spec 08 §3, §7) ist explizit **Layer 3**: „specified only
  when a causal profile packet is derived … not before". Der Siebendimensionen-Vektor
  (`principal, code path, model lineage, prompt lineage, retrieval path, data path,
  execution node` — MTH-014, Spec 08 Z. 120–125) ist eine **Erweiterung** des bestehenden
  Sechsdimensionen-`IndependenceProfile` um genau eine neue Dimension (`execution node`) —
  aber als eigenes, noch **nicht gebautes** Vertragsobjekt, nur für künftige Kausal-Profile
  vorgesehen. **Für den aktuellen `systematic_evidence_synthesis`-Profil-Output gibt es kein
  aufrufbares `ReplicationPlan` — nur den bereits existierenden Stage-8-Verifikationspfad.**
  Spec 08 §5 selbst sagt das: „claims advance through the unchanged claim/verification
  lifecycle afterward" (Z. 182) — nicht durch einen Replikationspfad.

**Antwort auf Missionspunkt 3 (sind Verifikation und unabhängige Replikation getrennte
Stufen?):** Ja, begrifflich getrennt — aber nur eine der beiden Stufen hat heute ein
lauffähiges Objekt/Service für diesen Profiltyp. „Unabhängige Replikation" im MTH-014-Sinn ist
zurzeit ein **Abgrenzungsbegriff** (er beschreibt, was ein bloßer Re-Run NICHT ist —
`REPLICATION_NOT_INDEPENDENT`), kein für die K1-T04-Claims erreichbares Ziel. Das relevante,
tatsächlich einsetzbare Instrument ist ein `VerificationResult` mit einem ehrlich
unabhängigen `IndependenceProfile` — nicht ein `ReplicationPlan`.

---

## 3. Spec-Zitate zum Lifecycle: was für einen Statuswechsel wirklich verlangt wird

- **MRR-FR-062**: „A claim with status `supported` MUST have at least one valid support
  relation and no unresolved hard verification failure." (`01_SYSTEM_SPEC.md` Z. 163)
- **MRR-FR-070–077** (Stage 8, Z. 176–183): Rule 8, sechs Unabhängigkeitsdimensionen,
  Quellprüfung (MRR-FR-072), Zahlen-Nachrechnung (MRR-FR-073), Gegenbeweissuche
  (MRR-FR-074), deterministische Status-Folge bei Scheitern (MRR-FR-075), Dedup
  (MRR-FR-076), Uneinigkeit bewahren (MRR-FR-077).
- **Claim-Vertrag** (`claim.py` Z. 95–114): der EINZIGE hart durchgesetzte Zusammenhang ist
  „if status == 'supported': evidence_relations UND verification_ids je ≥ 1 Eintrag" — für
  `draft` und `contested` gilt **keine** entsprechende Anforderung. Das heißt technisch:
  **beide realen Claims sind bereits heute gültige, vertragskonforme Objekte ohne jede
  Verifikation** — der Druck, sie zu verifizieren, kommt nicht vom Datenmodell, sondern von
  der epistemischen Erwartung, die K2-Gate-Entscheidung selbst formuliert.
- **CLAIM_LIFECYCLE** (`lifecycles.py` Z. 398–421): einzig erlaubter Weg zu `supported` ist
  `under_review -> supported`; jeder nichtterminale Status kann jederzeit nach
  `review_required`/`withdrawn`/`superseded`. Der `contested`-Claim entstand direkt (Erzeugung
  außerhalb der State-Machine-Transition, nicht über einen `draft -> contested`-Übergang, den
  es so gar nicht gibt) — ein weiterer Beleg, dass die Maschinerie das Fehlen der Verifikation
  bei `contested` gar nicht bemerkt.
- **Spec 08 §5** (Z. 168–182): „A synthesis run mints claim candidates in `proposed` status
  and MUST NOT author its own supporting `VerificationResult`s (no executor approves its own
  result); claims advance through the unchanged claim/verification lifecycle afterward."
  (Terminologie-Anmerkung: die Spec-Prosa sagt „proposed", der tatsächliche `ClaimStatus`-Wert
  im Code ist `draft` — keine Diskrepanz in der Sache, nur in der Prosa-Bezeichnung.)

**Fazit:** Verifikation ist für `supported` HART verlangt (Vertragsvalidator); für `draft`
und `contested` ist sie NICHT verlangt, aber genau das ist die Lücke, die K2 benennt — die
beiden Claims stehen fest, aber ohne die epistemische Rückversicherung, die Stage 8 für den
nächsten Schritt (Richtung `supported` bzw. eine belastbarere `contested`-Einstufung)
vorsieht.

---

## 4. Kandidaten

### (a) Meridian-Praxis-Verifier (Feldsession)

**Mechanik:** Eine reguläre Meridian-Session (`field-research/PROTOCOL.md`) wählt den Move
„**verify**" — wörtlich definiert als „an independent re-check of an existing draft's
sources, statistics and claims, done by the Verifier outside a full gauntlet, without
shipping" (`PROTOCOL.md` Z. 119–121). Die Verifier-Rolle selbst: „every factual claim has a
real, retrievable URL or is marked conjecture; statistics are correct; no fabricated data —
checked **independently of the builder**" (Z. 163–164, aus dem Gauntlet-Abschnitt). Der
Verifier würde die 15 Werk-Klassifikationen + die 3 Theorie-Paper gegen Primärquellen
(Hammonds Ausstellungsdokumentation, die Nature/arXiv/COLM-Paper) neu prüfen.

**Bridge-Problem:** `field-research/` hat **keinerlei** Tooling, das `meridian-runtime`
aufrufen, dessen Postgres lesen oder ein `VerificationResult` schreiben könnte (geprüft:
`field-research/tools/` enthält nur `memory/` — CLI/Store für die eigene Erinnerung, nichts
Richtung MRR). Die Session müsste also (1) mit Shell-Zugriff in
`meridian-runtime/` wechseln, (2) `mrr verification record` o. ä. aufrufen (dieser CLI-Pfad
existiert aktuell nicht einmal — nur `mrr synthesis run` ist gebaut; ein neues Subcommand
wäre nötig) oder (3) ihr Urteil nur als Journal-Eintrag festhalten und ein Mensch überträgt
es händisch als `VerificationResult`. Es gibt **keinen fertigen Bridge-Mechanismus** — er
müsste als eigenes Engineering-Task-Packet gebaut werden.

**Rule-8-Frage:** Ja, mechanisch erfüllt — die Session hat weder `claim.proposer_id` noch
`run_executor_id` (beides Engineering-CLI-Actor-Strings aus K1-T04) inne; `reviewer_id` wäre
zwangsläufig verschieden. **Independence-Frage, ehrlich:** Der Session-Betreiber ist derselbe
Mensch (Frank), dieselbe physische Maschine, sehr wahrscheinlich dieselbe Modellfamilie
(Claude) wie beim K1-T04-Lauf und dessen informellem Merge-Review. Unter der deklarierten
`IndependenceProfile` würde vieles vermutlich als „independent" durchgehen (anderer
`code_path`, evtl. anderer `prompt_family`), aber das ist — wie §2.4 zeigt — reine
Selbstauskunft, kein erzwungener Unterschied. Was **substanziell** unabhängig wäre: wenn der
Verifier tatsächlich zu Primärquellen zurückgeht statt die Pipeline-Ausgabe zu wiederholen
(„computational reproduction … MUST NOT count as independent replication", MTH-014) — dann
ist der Check in der Sache unabhängig, auch wenn Eigentümer/Infrastruktur gemeinsam bleiben.
Das ist die Kernabwägung, nicht mechanisch auflösbar.

**Aufwand:** M–L (Bridge muss gebaut werden; die Session selbst ist ein normaler,
budgetierter Move).

### (b) Menschliche Verifikation durch Frank

**Mechanik:** Frank prüft die 18 Quellenbelege selbst (URL/Zitat/Statistik), trägt ein
`VerificationResult` mit `reviewer_id != proposer_id/executor_id` ein (mechanisch trivial
erfüllbar — Rule 8 checkt Agenten-/Lauf-Identitäten, kein menschliches Gegenstück existierte
bisher als `proposer_id`).

**Offensichtliche Schwäche:** Frank hat den Lauf selbst in Auftrag gegeben, das Task-Packet
approved, den Merge entschieden (Tagessession-Handoff §2: „Merges … + Wahl der nächsten
Arbeitsschritte" per Owner-Delegation). Er ist der einzige Mensch hinter praktisch jedem
Schritt der Kette — Auftraggeber, Reviewer der Reviewer-Agenten, jetzt auch Verifizierer.
Das ist genau die Konstellation, die Peer-Review als „nicht unabhängig" einstuft, auch wenn
kein einziges `IndependenceProfile`-Feld technisch kollidiert.

**Aufwand:** S (kein Bridge-Bau nötig — Frank kann direkt einen `VerificationResult` via CLI
oder Skript eintragen, sobald ein Verification-Recording-Pfad existiert; aktuell existiert
nicht einmal die CLI dafür, nur der Service — auch hier fehlt ein kleines Stück Tooling).

### (c) Zweite, strukturell getrennte Engineering-Session

**Mechanik:** Eine frische Worktree-Session (anderer Branch/Kontext) derived die
Klassifikation unabhängig aus den gepinnten Atlas-Snapshots neu — nicht durch Re-Run der
gleichen Pipeline, sondern durch eigene Lektüre der 18 `decisive_move`-Texte und eigenes
Urteil, ob „instantiiert" vs. „referenziert nur" zutrifft.

**Die entscheidende Grenze:** MRR-MTH-014 sagt ausdrücklich: „An identical code-and-data
rerun is computational reproduction and MUST NOT count as independent replication"
(Spec 08 Z. 123–125, Fehlercode `REPLICATION_NOT_INDEPENDENT`). Das genau ist bereits
passiert — der Merge-Review-Agent „re-ran the pipeline twice with byte-identical results"
(PR #53) — das ist Reproduktion, keine Verifikation im Stage-8-Sinn. Eine ECHTE Verifikation
nach (c) müsste die Quellen **neu und unabhängig vom Extraktionscode** lesen (MRR-FR-072:
„retrieve or locally inspect the cited source"), nicht die Pipeline erneut ausführen. Wo
genau die Grenze zwischen „unabhängige Verifikation" (reicht für Stage 8) und „unabhängige
Replikation" (MTH-014, für Kausal-Profile, hier nicht gebaut) verläuft, ist selbst eine der
Kernfragen dieses Memos (siehe §6, Urteilsfrage 2).

**Aufwand:** M (kein neues Bridge-Objekt nötig, die Session bleibt im selben Repo; aber echte
Unabhängigkeit verlangt bewusst NICHT dieselbe Codepipeline zu nutzen — mehr Aufwand als ein
Reproduktions-Re-Run).

### (d) Cross-Praxis-Verifikation (Ulysses/Atelier)

**Mechanik:** Ulysses (`irrtum-als-methode/`) prüft die Zitate/Quellen — naheliegend, weil
Ulysses den **Theorie-Atlas selbst kuratiert hat** (87 Quellen, „provenance-verified" laut
PR #53) und damit über Sachkenntnis zur Quellenlage verfügt, die weder Meridian noch
Engineering per se haben.

**Eigentümer-Spannung, ehrlich benannt:** Für den Theorie-Atlas-Claim ist Ulysses nicht
„unabhängig von der Datenquelle" — Ulysses IST die Datenquelle (Kurator des Atlas). Eine
Verifikation durch den Atlas-Kurator selbst prüft eher „hat die Pipeline unseren Atlas korrekt
gelesen" als „ist die zugrunde liegende Quellenlage richtig" — eine andere, engere Frage als
echte Quellenverifikation. Für den Werk-Atlas-Claim (site-eigen, nicht Ulysses' Eigentum) wäre
Ulysses dagegen eine genuin externe Praxis ohne Beteiligung an Produktion oder Kuration.

**Ökologie-Bezug:** `field-research/PROTOCOL.md` „The ecology — encounters run both ways":
„Offers, not orders … Conditions bind only through acceptance" (Z. 288–293) — eine
Cross-Praxis-Verifikationsanfrage an Ulysses wäre ein **Encounter**, kein Auftrag; Ulysses
kann annehmen, ablehnen oder eigene Bedingungen stellen. Genau diese Frage steht bereits offen
in `frankbueltge.de/docs/design/2026-07-21-mrr-site-kopplung-vorschlag.md` §5 Frage 3: „Ist
die Verwendung von Ulysses' Atlas als Ground-Truth für eine Meridian-Aussage ein Fall für
einen formellen 'Encounter' (The Middle)?" — die Verifikationsfrage und die dortige
Encounter-Frage hängen zusammen, sind aber nicht identisch (dort geht es um Erlaubnis zur
Nutzung als Grundlage, hier um die Prüfung des Ergebnisses).

**Bridge-Problem:** identisch zu (a) — kein technischer Weg von `irrtum-als-methode/` zu
`meridian-runtime` existiert; müsste ebenfalls gebaut werden.

**Aufwand:** L (Bridge + Encounter-Verhandlung + inhaltliche Prüfung).

---

## 5. Risiken

**Verifikations-Theater** (ein Rubber-Stamp-`VerificationResult`, das nur die Formfelder
füllt, ohne echte Prüfung): strukturell begünstigt, weil `checks_performed`,
`evidence_inspected`, `rationale` zwar Pflichtfelder sind, aber ihr **Inhalt** nicht
maschinell gegen eine echte Quellenprüfung verifiziert wird — ein Reviewer kann plausibel
klingende Strings eintragen, ohne die Quelle tatsächlich geöffnet zu haben. MRR-FR-072
verlangt es normativ, aber nichts im Code erzwingt es. Jeder Kandidat trägt dieses Risiko;
(b) und (c) am stärksten, weil ein einzelner Akteur ohne Gegenkontrolle handelt; (a) und (d)
mildern es etwas durch die jeweils eigene Praxis-Governance (Gauntlet bzw. Ulysses' eigenes
Verfahren), aber auch die prüfen nur, was sie selbst für nötig halten.

**Independence-Laundering** (derselbe Mensch/dieselbe Infrastruktur, anderes Etikett): das
zentrale, in §2.2–2.4 belegte Risiko. `IndependenceProfile` ist reine Selbstauskunft
(„DECLARED here, not validated"); `principal`/`proposer_id`/`executor_id` sind freie
CLI-Strings ohne Auth-Bindung. Jeder Kandidat, bei dem am Ende Frank der einzige
verantwortliche Mensch hinter Produktion UND Prüfung bleibt — was auf (a), (b) und (c) alle
zutrifft, da field-research, meridian-runtime und jede Worktree-Session von ihm betrieben
werden —, kann die mechanische Unabhängigkeitsformel erfüllen, ohne die dahinterliegende
Intention (domain 2.13: „a reviewer cannot satisfy independence if it shares the same
execution principal and unaltered reasoning path") tatsächlich zu erfüllen. Einzig (d)
durchbricht das auf der Personen-/Praxis-Ebene wirklich (Ulysses ist keine von Frank direkt
geführte Einzelinstanz, sondern eine eigenständig geführte Kollektiv-Praxis mit eigenem
Protokoll) — trägt dafür die in (d) beschriebene Eigentümer-Verstrickung beim Theorie-Atlas.

**Mitigation, die für alle Kandidaten gilt:** die Qualität der Unabhängigkeit hängt am Ende
weniger vom deklarierten `IndependenceProfile` als davon ab, ob der Prüfer tatsächlich zu
Primärquellen zurückgeht (MRR-FR-072/074) statt die Pipeline-Ausgabe zu goutieren — das ist
prüfbar (steht im `rationale`/`checks_performed`-Text), auch wenn es nicht erzwingbar ist.

---

## 6. Kompakte Kandidaten-Tabelle

| Kandidat | Mechanik | Rule-8 (mechanisch) | Independence-Verdikt (ehrlich) | Aufwand |
|---|---|---|---|---|
| (a) Meridian-Verifier | Feldsession, Move „verify", Primärquellen-Recheck | erfüllt (andere `reviewer_id`) | Deklarativ ja; substanziell nur wenn wirklich auf Primärquellen zurückgegangen wird, nicht auf die Pipeline-Ausgabe — sonst Reproduktion, keine Verifikation | M–L (Bridge fehlt komplett) |
| (b) Frank persönlich | Direkte menschliche Prüfung, `VerificationResult` per Hand/CLI | erfüllt (kein `proposer_id`-Match) | Schwach — Frank war Auftraggeber, Approver und Merge-Entscheider der gesamten Kette | S (kleine Tooling-Lücke: kein Recording-CLI-Pfad heute) |
| (c) Getrennte Engineering-Session | Neue Worktree-Session, Neulektüre der 18 Quellen ohne Pipeline-Re-Run | erfüllt | Grenzfall — MTH-014 verbietet reinen Code-Rerun als „independent"; nur echte Neu-Lektüre zählt, Linie unscharf | M |
| (d) Ulysses/Atelier | Cross-Praxis-Encounter, externe Prüfung | erfüllt | Für Werk-Atlas stark unabhängig; für Theorie-Atlas geschwächt (Ulysses = Atlas-Kurator selbst) | L (Bridge + Encounter-Verhandlung) |

**Was die bestehende Maschinerie bereits trägt (Zusammenfassung):** Ein `VerificationResult`
kann heute technisch von jedem `reviewer_id` erzeugt werden, der nicht wörtlich gleich
`claim.proposer_id`/`run_executor_id` ist (VerificationService.record, Rule-8-Gate zuerst
geprüft, vor jedem Schreiben) — unabhängig davon, ob dahinter tatsächlich ein anderer Mensch,
eine andere Praxis oder nur ein anderer String steht. Der feinere
Unabhängigkeits-Dimensionscheck (`independence.py`) existiert, ist aber (a) rein
deklarativ/unvalidiert und (b) nirgends in den `ClaimService`-Gate verdrahtet — er würde,
selbst wenn benutzt, nur die Zahl der laut Selbstauskunft unabhängigen Reviews zählen, nie
prüfen, ob diese Auskunft stimmt. Für `supported` ist mindestens ein
`VerificationResult`-Eintrag hart verlangt (Vertragsebene); für `draft`/`contested` — der
Status der beiden realen Claims — ist gar keine Verifikation contractually erzwungen; der
Bedarf ist rein epistemisch/governance-getrieben, wie die K2-Gate-Entscheidung selbst
formuliert. Ein CLI-Recording-Pfad für `VerificationResult` existiert aktuell nicht (nur der
Service) — jeder Kandidat braucht mindestens ein kleines Tooling-Stück, bevor überhaupt
irgendjemand einen Eintrag schreiben kann.

---

## 7. Die 2–3 echten Urteilsfragen, an denen die Design-Entscheidung hängt

1. **Was muss „principal"/„unabhängig" konkret heißen, wenn am Ende fast immer derselbe
   Mensch (Frank) hinter jeder Instanz steht?** Die Runtime selbst beantwortet das nicht —
   `IndependenceProfile` ist Selbstauskunft, keine erzwungene Trennung. Die Session muss
   entscheiden, ob „andere Praxis mit eigenem Protokoll/Governance" (wie Meridian oder
   Ulysses, Kandidaten a/d) als hinreichende Unabhängigkeit zählt, obwohl der letztverantwort-
   liche Mensch identisch bleibt — oder ob nur eine Prüfung zählt, die nachweislich zu
   Primärquellen zurückgeht, unabhängig davon, wer sie durchführt.

2. **Wo genau verläuft die Linie zwischen „Verifikation" (Stage 8, für diesen Profiltyp
   real einsetzbar) und „unabhängiger Replikation" (MTH-014, Layer 3, für Kausal-Profile
   noch gar nicht gebaut)?** Ein reiner Pipeline-Re-Run ist explizit disqualifiziert
   (`REPLICATION_NOT_INDEPENDENT`) — aber wie viel Neu-Prüfung der Primärquellen reicht, um
   als „Verifikation" (nicht „Replikation") durchzugehen? Das Memo kann die Frage nicht
   auflösen, weil die Spec selbst keine trennscharfe Regel für „genug anders" liefert
   (vgl. `independence.py`s eigene geflaggte „reasoning path"-Unschärfe, §2.3).

3. **Reicht der minimale ehrliche erste Schritt — den bereits geleisteten, aber nur
   informellen Merge-Review (der laut PR #53 tatsächlich Quellen gegen den Atlas geprüft hat)
   nachträglich als echtes `VerificationResult` zu formalisieren — oder verlangt K2 mehr
   (eine komplett neue, von der Produktionskette getrennte Prüfinstanz, Kandidat a/c/d)?**
   Das ist zugleich die günstigste Option (kein neuer Bridge-Bau nötig für den reinen
   Formalisierungs-Akt, nur ein Recording-Schritt) und die mit dem größten
   Verifikations-Theater-Risiko, weil sie denselben Check nur nachträglich labelt, statt einen
   neuen durchzuführen.

---

*Quellen (alle wörtlich zitiert, Dateipfade wie im Text genannt):
`meridian-runtime/AGENTS.md`, `docs/spec/01_SYSTEM_SPEC.md`, `docs/spec/02_DOMAIN_MODEL.md`,
`docs/spec/03_API_AND_EVENTS.md`, `docs/spec/08_RESEARCH_METHOD_KERNEL.md`,
`docs/design/2026-07-21-k2-gate-decision.md`,
`docs/design/2026-07-21-session-handoff-tagessession.md`,
`docs/design/2026-07-21-research-method-kernel-rework.md`,
`packages/contracts/mrr/contracts/{verification_result,claim,common}.py`,
`packages/domain/mrr/domain/{independence,exceptions,lifecycles}.py`,
`services/control_plane/mrr/services/verification/service.py`,
`services/control_plane/mrr/services/claim/service.py`,
`services/control_plane/mrr/services/cli/{synthesis_main,synthesis_orchestration}.py`,
PR #53 (`gh pr view 53`); `frankbueltge.de/src/content/field/PROTOCOL.md`,
`frankbueltge.de/docs/design/2026-07-21-mrr-site-kopplung-vorschlag.md`;
`field-research/` (Meridian-Praxisrepo, `tools/` geprüft — kein MRR-Bridge vorhanden);
`irrtum-als-methode/atlas/atlas.json` (Ulysses-Atlas, Existenz/Eigentümerschaft bestätigt).*
