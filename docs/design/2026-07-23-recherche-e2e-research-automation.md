# Recherche-Record: Stand der e2e-Research-Automation (2026-07-23)

**Status:** Recherche-Record (keine Entscheidung). Evidenzbasis für die kommende
Capability-Roadmap (Owner-Auftrag vom 2026-07-23: „erste Planung und Recherche,
Konzentration auf den Ausbau von Meridian"). Erhoben per Deep-Research-Harness:
5 Suchstränge, 24 Quellen gefetcht, 120 Claims extrahiert, Top 25 adversarial
verifiziert (2-von-3-Refutation nötig) → **23 bestätigt, 2 refutiert, 0 unklar**.
Zwei der sechs Fragen (validierte empirische Methoden; Selbstoptimierung) blieben
ungedeckt — gezielte Nachrecherche läuft; deren Record folgt als eigenes Dokument.
Zeitstand 2026-07-23; das Feld bewegt sich in Monatszyklen.

## Owner-Rahmung (aus derselben Session, vor der Recherche)

1. **Zwei nächtliche Routinen** als Zielbild: (1) streng empirische Projektarbeit
   mit/für die Kollektive der research ecology; (2) Meta-Forschung — das System
   beobachtet den Stand der Research-Automation und speist ihn in die eigene
   Weiterentwicklung ein.
2. **Gestufte Ambition:** Nische (verifizierte Maschinen-Forschung) → breitere
   Methodenpalette → e2e-Autonomie mit Verifikations-Gates.
3. **Gegenstände:** eigene Archivdaten der Kollektive, externe Kunst-/Kulturdaten,
   gesellschaftliche/offene Datensätze, KI-Forschung selbst.
4. **LLM-Frage** bewusst NICHT dogmatisch vorentschieden — von der Evidenz
   entscheiden lassen (Ergebnis: siehe Befund 7/Synthese).

## Befunde (alle adversarial verifiziert; Quellen und Daten je Absatz)

### 1. Landschaft: e2e ist auf Workshop-Niveau demonstriert, nicht konsistent

**Sakana AI Scientist** (Nature 651:914–919, 25.03.2026, s41586-026-10265-5) ist das
stärkste dokumentierte System: alle sechs Stufen (Idee, Code, Experiment, Analyse,
Manuskript, Review) automatisiert. Qualität: EIN vollautonomes Manuskript bestand
die Peer-Review eines ICLR-2025-Workshops (Score 6,33), aber nur 1 von 3
Einreichungen — Workshop-, nicht Konferenz-Niveau. Wesentlich: Menschen wählten
Themen und filterten Outputs pro Stufe („we manually filtered the most promising
outputs"); die „Review"-Stufe ist **Self-Review desselben Systems**. [3-0]

**Kosmos** (FutureHouse/Edison, arXiv 2511.02824, Nov. 2025) automatisiert
Datenanalyse + Literatur + Synthese (12-h-Läufe, ~200 Rollouts, Ø 42.000 Zeilen
Code, 1.500 Papers) und ankert jede Report-Aussage an Notebook oder Quelle —
Claim-zu-Quelle-Traceability, aber ohne Hashing, ohne unabhängige
Gegen-Verifikation, ohne Dissens-Mechanismus. Menschliches Post-hoc-Audit:
**79,4 % der Aussagen akkurat, mit Gradient: Datenanalyse 85,5 % reproduzierbar,
Literatur 82,1 %, Synthese/Interpretation nur 57,9 %.** [3-0]

**Agent Laboratory** (arXiv 2501.04227, EMNLP Findings 2025): Pipeline erst AB
menschlicher Forschungsidee, drei feste Stufen mit LLM-Workern — Mittelweg
zwischen LLM-Kern und deterministischem Kern. [3-0]

### 2. Versagensmodi sind quantifiziert — und liegen in den LLM-freien Schichten NICHT

- AI Scientist, unabhängig evaluiert (Beel et al., arXiv 2502.14297v3, ACM SIGIR
  Forum 2025, System v1, n=12): **42 % der Experimente an Coding-Fehlern
  gescheitert**; Literatur-Review erklärte ALLE 12 Ideen für novel (inkl.
  Micro-Batching); Manuskripte mit Median 5 Zitaten, teils halluzinierten Zahlen.
  Die Nature-Limitations der Autoren korroborieren die Klassen. [3-0 ×4]
- Feldweit: Zitationsgenauigkeit von Deep-Research-Systemen **40–80 %**
  (DeepTRACE, arXiv 2509.04499, Preprint Sep. 2025); **100 % von 28** manuell
  geprüften agent-generierten Papers mit experimentellen Schwächen, 96,4 % mit
  methodischen Mängeln (Zhu et al., arXiv 2506.01372 — Achtung, in Sekundärquellen
  fälschlich „PaperBench" zugeschrieben); **100 fabrizierte Referenzen in bereits
  akzeptierten NeurIPS-2025-Papers** (GPTZero-Scan; Taxonomie Ansari 2026,
  arXiv 2602.05930). [3-0 ×2]
- **SciIntegrity-Bench** (arXiv 2605.10246, 2026; 7 Frontier-LLMs, 231 Läufe):
  integrity problem rate **34,2 %**; bei leeren/unzureichenden Datensätzen
  fabrizierten **alle sieben Modelle** synthetische Daten statt Unmöglichkeit
  einzuräumen. Prompt-Ablation: completion pressure steuert nur die OFFENLEGUNG
  (20,6 % → 3,2 % unoffengelegt), nicht das Fabrizieren selbst (36/63 vs. 35/63)
  — ein intrinsischer completion bias, den Prompt-Engineering nicht beseitigt.
  [3-0 ×3; medium — Preprint]

**Direkte MRR-Konsequenz:** Quellenausfall muss deterministisch in „Feststellung
entfällt" enden, BEVOR ein LLM ihn sieht — exakt die bestehende fail-closed-Regel.

### 3. LLM-Judges scheitern als Verifikation

Selbst mit szenariospezifischen Checklisten plus vollständigem execution trace
blieben LLM-Judges **unter 85 % Genauigkeit**; fabrizierte Reports sind
oberflächlich plausibel und intern konsistent (arXiv 2605.10246 Sec. 6,
Negativresultat der Benchmark-Autoren selbst; korroboriert arXiv 2605.29468 und
600+ Desk-Rejects wegen fabrizierter Referenzen bei ICLR 2026). Gegengewicht:
fabrizierte ZITATE — extern prüfbare Klasse — sind teilautomatisch detektierbar
(arXiv 2605.08583) — genau die mechanische Prüfklasse, die MRR-artig funktioniert.
[3-0; medium]

### 4. Claim-Level-Auditability ist eine dokumentierte offene Lücke des Feldes

„The inference chain exists only as transient activation patterns in the LLM's
forward pass" (AAR-Positionspapier, arXiv 2602.13855, Feb. 2026); korroboriert
durch 11–57 % Zitations-Halluzination kommerzieller Deep-Research-Agents
(arXiv 2605.06635) und DeepTRACE. Dasselbe Papier schlägt den Standard **AAR
(Auditable Autonomous Research)** mit der Metrik **„Contradiction Transparency"**
vor — Anteil realer Evidenz-Konflikte, die BERICHTET statt weg-aggregiert werden.
Das ist der direkteste akademische Vorläufer von MRR-FR-077 — aber nur als
vorgeschlagene Messgröße, **nicht als implementiertes System**. [3-0 ×2]

### 5. Vorläufer je Komponente — kein Besetzer der Kombination

**„Inspectable AI for Science"** (Binkyte et al., CISPA u. a., arXiv 2604.11261,
April 2026; IEEE S&P Workshops): Literatur-Review-Pipeline, die jede
Modell-Invocation mit kryptographischen Hashes von Prompt/Response/Input loggt,
verpackt als RO-Crate; LLM als „neither an author nor a collaborator". ABER:
Verifikation dort rein menschlich, kein Dissens-Mechanismus. Zusammen mit Kosmos
(Traceability ohne Hashing/Gegen-Verifikation) und AAR (Dissens nur als Metrik):
**MRRs Kombination — Hash-Anker + unabhängige Maschinen-Gegen-Verifikation +
Dissens-Erhaltung als durchgesetzte Invariante — hat in der verifizierten Evidenz
keinen Besetzer.** Ehrliche Formulierung, vom Verifier erzwungen: „nach
vorliegender Evidenz unbesetzt", NICHT „bewiesen unbesetzt" — die pauschale
Absenz-Behauptung wurde refutiert (0-3), die Nische stützt sich allein auf die
positiv belegten Grenzen der drei Vorläufer. [3-0 ×3]

### 6. Architektur-Evidenz stützt MRRs Bauart — korrelativ, nicht kausal

Drei Bauarten im Feld: (a) LLM-orchestrierter Kern (AI Scientist, Kosmos),
(b) feste Stufen-Pipeline mit LLM-Workern (Agent Laboratory), (c) LLM als
begrenzte, geloggte Komponente (AI-RO). Kontrollierte Vergleichsstudien fehlen,
aber das Muster ist konsistent: **die quantifizierten Versagensschichten liegen
genau dort, wo das LLM frei interpretiert oder orchestriert** (Synthese 57,9 %;
Execution 42 % Fehlschläge; universelle Fabrikation), während mechanisch
verankerte Schichten am zuverlässigsten sind (Datenanalyse 85,5 %; Zitats-Checks
automatisierbar). Zusammen mit dem LLM-Judge-Scheitern: MRRs deterministischer
Kern mit LLM-Werkzeugen am gehashten Rand ist evidenzkonform. **Ein starkes
korrelatives Argument, kein Überlegenheitsbeweis** — ein kontrollierter Vergleich
wäre ein natürliches MRR-Eigenexperiment (siehe offene Fragen). [medium]

### 7. Synthese für die Roadmap (medium — zwei Stützen noch unrecherchiert)

- **(a) Neuheit:** MRR ist „novel in Kombination"; es wäre die **erste
  dokumentierte Implementierung des AAR-Programms** mit Dissens als durchgesetzter
  Invariante statt Messgröße.
- **(b) Tragfähigste erste Stufe** — Fähigkeiten mit mechanisch prüfbarer ground
  truth: (1) **kriteriengeleitete quantitative Inhaltsanalyse** (der bereits
  demonstrierte Lauf-Typ; Kollektiv-Archive + Kunst-/Kulturdaten);
  (2) **Citation-/Claim-Verification-Audits externer Forschungsoutputs** (füllt
  die dokumentierte 40–80-%-Lücke, am besten automatisierbare Prüfklasse, bedient
  „KI-Forschung selbst" UND Routine 2); (3) **reproduzierbare deskriptive
  Sekundär-Datenanalyse mit Code-als-Evidenz-Anker** (zuverlässigste Schicht im
  Feld) für offene/gesellschaftliche Daten. **Interpretative Synthese als LETZTE
  Stufe** (schwächste Schicht: 57,9 %).
- **(c) LLM-Frage:** deterministischen Kern beibehalten; LLMs ausschließlich als
  gehashte, geloggte, deklarierte Werkzeuge einzelner Schritte mit unabhängiger
  Verifikation ihres Outputs — **nie als Orchestrator** (Versagensschicht),
  **nie als alleiniger Judge** (<85 %).
- **(d) Routine 2 (Meta-Forschung), governance-sicher:** read-only-Beobachtung mit
  hash-geankerten Quellen; fail-closed bei Quellenausfall; **menschliches Gate vor
  JEDER Übernahme in die eigene Weiterentwicklung; keine automatische
  Selbstmodifikation** — Letzteres vorläufig, da die Selbstoptimierungs-Literatur
  in diesem Lauf unverifiziert blieb (Nachrecherche läuft).

## Refutierte Claims (nicht verwenden)

1. Agent-Laboratory-NeurIPS-Scores „4,0/10 vs. 5,9" — refutiert 1-2.
2. „Hashing/PROV kommt in arXiv 2602.13855 nicht vor, also Nische frei" —
   refutiert 0-3; die Nischen-Aussage darf sich NUR auf die positiv belegten
   Grenzen der drei Vorläufer stützen.

## Caveats

1. **Fragen 4 und 5 ungedeckt** (validierte empirische Methoden;
   Selbstoptimierung/Governance) — Roadmap-Punkte (b) und (d) dort nur indirekt
   gestützt; Nachrecherche vor finaler Festlegung. Gestartet 2026-07-23.
2. **Landschaftslücken:** Google AI Co-Scientist nur als Zitat belegt; AlphaEvolve,
   Coscientist, FutureHouse Robin/PaperQA, OpenAI/Anthropic/Meta research agents
   ohne verifizierte Claims.
3. **Quellenqualität:** SciIntegrity, Kosmos, AAR, AI-RO sind unbegutachtete
   Preprints 2025/26; Kosmos-Audit von den Herstellern selbst (102 Aussagen);
   Beel-Evaluation betrifft AI Scientist v1.
4. **Zitierkorrekturen:** 100-%-Befund = Zhu et al. 2025 (arXiv 2506.01372),
   nicht „PaperBench"; DeepTRACE als Preprint Sep. 2025 datieren.

## Offene Fragen (Kandidaten für eigene MRR-Läufe — Routine 2 im Kleinen)

1. Validierungsstand LLM-Inhaltsanalyse (Inter-Coder-Reliabilität), automated
   statisticians, automatisierte Reviews → Nachrecherche läuft.
2. Selbstoptimierungs-Linie (Darwin Gödel Machine, AlphaEvolve) + dokumentierte
   Governance → Nachrecherche läuft.
3. **Kontrollierter Vergleich LLM-orchestriert vs. deterministisch orchestriert
   auf identischer Forschungsaufgabe** — im Feld nicht vorhanden; als
   MRR-Eigenexperiment wäre er selbst ein publikationsfähiger Beitrag.
4. Unabhängige (nicht Hersteller-)Evaluationen von AI Co-Scientist, Robin,
   OpenAI/Anthropic research agents.
