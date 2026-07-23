# Capability-Roadmap MRR — Entwurf zur Owner-Review (2026-07-24)

**Status:** ENTWURF, Owner-Vorlage — keine Entscheidung. Nichts hieraus wird
gebaut, bevor Frank die Reihenfolge und den ersten Nutzungsanlass bestimmt hat
(use-first-Doktrin vom 2026-07-22 gilt unverändert: jede Fähigkeit nur nach
konkretem Bedarf, als eigenes Paket mit benanntem Anlass).

**Evidenzbasis:** die drei Recherche-Records vom 2026-07-23/24
(`…-recherche-e2e-research-automation.md`, `…-nachrecherche-empirische-methoden.md`,
`…-primaerquellen-selbstoptimierung.md`) — 47 Quellen, 45 adversarial verifizierte
Claims, 5 refutierte, plus gekennzeichnete Primärquellen-Lektüre.

**Owner-Zielbild (Session 2026-07-23):** Meridian forscht streng empirisch in
zeitlich begrenzten Projekten (selbst gewählt oder von Frank/Ulysses/Studio
vorgeschlagen); zwei nächtliche Routinen — (1) Projektarbeit, (2) Meta-Forschung
zur Research-Automation mit Rückfluss in die eigene Entwicklung; Anschluss an die
e2e-Automation-Front (Nature s41586-026-10265-5); gestuft Nische → Breite → e2e.

## Die Position (aus der Evidenz, nicht aus Ambition)

Das Feld hat e2e-Demonstrationen, aber **keine verifizierte Autonomie**: Selbst-
Review statt unabhängiger Verifikation (Sakana), 57,9 % Synthese-Akkuratheit
(Kosmos), universelle Datenfabrikation bei leeren Datensätzen (7/7 Frontier-LLMs),
LLM-Judges < 85 %. Claim-Level-Auditability ist benannte Feldlücke; AAR
(Feb. 2026) schlägt als Standard vor, was MRR implementiert hat — inkl.
„Contradiction Transparency" als Metrik dessen, was FR-077 als Invariante
erzwingt. **MRRs Nische — nach vorliegender Evidenz unbesetzt: die erste
dokumentierte Implementierung verifizierbarer Maschinen-Forschung mit
Dissens-Erhaltung.** Vorne mitspielen heißt hier: nicht mehr Papers, sondern
nachprüfbare.

## Stufe 1 — Nische (Kandidaten-Pakete, je eigener Nutzungsanlass nötig)

### N1 · Validierungs-Harness für kriteriengeleitete Klassifikation

Das dokumentierte Validierungsprotokoll (Record II/A4) als MRR-Fähigkeit:
Goldstandard-Sets (≥20–30 Labels/Kategorie), Validierung erst nach
Kriterien-Finalisierung (als Objekt-Zustand erzwingbar), Kappa/F1 statt Accuracy,
Prompt-Stability-Analyse über Paraphrasen (Krippendorffs Alpha). Macht den
bereits demonstrierten Lauf-Typ (K1-T04) **publikationsfähig valide** — und der
Harness ist zugleich der eingefrorene Evaluator, den Routine 2 später braucht.
Evidenz: binäre/kriterielle Klassifikation ist „low-hanging fruit" (κ>0,5
erreichbar); LLM-Fehler ko-lokalisieren mit menschlichem Coder-Dissens —
Dissens-Erhaltung ist die dokumentiert korrekte Behandlung genau dieser Zone.

### N2 · Citation-/Claim-Verification-Audit als Lauf-Typ

Externe Forschungsoutputs (auch KI-generierte) mechanisch prüfen: existieren die
Referenzen, tragen die Quellen die Behauptung, sind Zahlen konsistent? Füllt die
dokumentierte 40–80-%-Zitationslücke des Feldes; mechanisch prüfbare Klasse
(extern verifizierbar, MRR-artig); bedient den Gegenstand „KI-Forschung selbst"
UND liefert Routine 2 ihr Material. Erster natürlicher Anwendungsfall: die
eigenen Recherche-Records auditieren (Selbstanwendung als Demonstration).

### N3 · Reproduzierbare deskriptive Sekundäranalyse mit Code-als-Anker

Deskriptive Statistik über offene/gesellschaftliche Datensätze, jeder Befund an
ausführbaren, gehashten Code geankert (die 85,5-%-Schicht — zuverlässigste im
Feld). **Grenze aus Record II:** deskriptiv ja; inferentielle Statistik und freie
Data-Science-Agenten liegen dokumentiert unter jeder Autonomie-Schwelle
(StatABench 68,6 %; DSBench 34,12 %) — inferentielle Schritte nur mit
menschlichem Gate.

## Stufe 2 — Breite (erst nach realer Stufe-1-Nutzung)

- Multi-Label-/kausale Kodierung NUR hybrid (dokumentierter Einbruch auf
  κ≈0,2–0,4; Kausalurteile κ~0,5 vs. menschliche ICC 0,86).
- Systematische Reviews/Meta-Analysen: Recherche-Lücke (Elicit/RobotReviewer-
  Validierung) erst schließen, dann entscheiden.
- Netzwerkanalyse/Survey-Auswertung: keine validierte Evidenz gefunden — vor
  einem Paket eigene Erhebung nötig.

## Stufe 3 — e2e mit Verifikations-Gates (Horizont)

- Interpretative Synthese ZULETZT (57,9-%-Schicht), immer als markierte,
  gegen-verifizierte Schätzung.
- **Das publikationsfähige Eigenexperiment:** kontrollierter Vergleich
  LLM-orchestriert vs. deterministisch orchestriert auf identischer
  Forschungsaufgabe — existiert im Feld nicht (Record I, offene Frage 3); MRR
  hat beide Bauarten im Zugriff und den Verifikations-Apparat für die Messung.

## Die zwei Routinen

**Routine 1 (Projektarbeit):** braucht neben den N-Fähigkeiten zwei
Infrastruktur-Stränge, die BEIDE bereits designierte Plan-Plätze haben:
E5/E6-Föderation (laut research-ecology-Doku die Transportschicht für Joint
Inquiries — „niemals ein Parallelbau") und die Agency-Anbindung (das
Meridian-Kollektiv initiiert Läufe selbst; bisher fuhr Engineering alle Läufe —
enc-2026-005 trennt die Rollen ausdrücklich). Erster Anlass könnte die erste
Joint Inquiry sein (Owner-Frage, ggf. Parallelsession).

**Routine 2 (Meta-Forschung), governance-fest aus Record III:** read-only-
Beobachtung des Felds (hash-verankerte Quellen, fail-closed — die
Fabrikations-Evidenz verlangt, dass Quellenausfall deterministisch endet, bevor
ein LLM ihn sieht); Vorschläge als Task-Packets vor dem menschlichen Gate;
als erste Selbstoptimierungs-Form ausschließlich GEPA-artige Prompt-/Kriterien-
Optimierung gegen den EINGEFRORENEN N1-Harness. **Kein Optimierer bewertet seine
eigene Optimierung** (DGM-Vorfall: Marker der Bewertungsfunktion entfernt trotz
expliziter Anweisung). Code-Selbstmodifikation bleibt ausgeschlossen, bis das
Feld Gegenmaßnahmen belegt.

## LLM-Entscheidung (Empfehlung an den Owner)

Kern bleibt deterministisch. LLMs als gehashte, geloggte, deklarierte Werkzeuge
einzelner Schritte, Output unabhängig verifiziert oder als Schätzung markiert —
**nie als Orchestrator** (die quantifizierte Versagensschicht), **nie als
alleiniger Judge** (<85 %; κ 0,19–0,51). Evidenzlage: korrelativ stark,
experimentell offen — genau das adressiert das Stufe-3-Eigenexperiment.

## Offene Owner-Entscheidungen (nichts davon drängt)

1. Reihenfolge/Zuschnitt N1–N3 und der jeweils benannte Nutzungsanlass.
2. Erste Joint Inquiry als Anlass für Routine 1 (und damit E5/E6) — verbunden mit
   der redaktionellen Schwerpunkt-Frage „joint-first" (eigene Session).
3. Ob Routine 2 als erstes reales nächtliches Deployment vor N1 gezogen wird
   (sie ist read-only und am risikoärmsten — aber auch sie braucht einen Anlass).
4. Unverändert offen aus früheren Records: K2-Tor-Wiedervorlage,
   Hammond-Adjudikation, erstes A4-Release, Artefakt-Blob-Dauerhaftigkeit
   (Befund 1), Öffnung des Repos (berührt die „source code open"-Byline der
   Site — On Record wartet darauf).
