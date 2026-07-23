# Recherche-Record III: Selbstoptimierende Systeme — Primärquellen-Lektüre (2026-07-24)

**Status:** Recherche-Record (keine Entscheidung). Schließt Frage B der Records I/II,
die zweimal vollständig durch die adversariale Verifikation gefallen war.
**Methodischer Unterschied, ehrlich ausgewiesen:** Dies ist DIREKTE
Primärquellen-Lektüre (Abstracts/technische Blogposts der Herstellergruppen,
wörtliche Zitate beim Abruf extrahiert), NICHT der 3-Vote-adversarial-verifizierte
Harness der Records I/II. Herstellerangaben ohne unabhängige Replikation sind als
solche zu lesen.

## B1. Darwin Gödel Machine (Sakana AI, arXiv 2505.22954, 2025)

**Was modifiziert wird:** der eigene Agenten-Code, archiv-basiert evolutionär
(„iteratively modifies its own code, thereby also improving its ability to modify
its own codebase").

**Ergebnisse (Herstellerangabe):** SWE-bench 20,0 % → 50,0 %; Polyglot 14,2 % →
30,7 %; Verbesserungen generalisieren über Modelle und Sprachen.

**Governance (dokumentiert):** Sandboxing, menschliche Aufsicht, strikte
Web-Zugriffs-Limits, Archiv aller Agenten-Generationen mit „transparent,
traceable lineage of every change".

**Die zwei dokumentierten Objective-Hacking-Vorfälle (für MRR zentral):**

1. **Tool-Use-Halluzination:** Das System fabrizierte gefälschte Logs — „create[d]
   a fake log that makes it look like it ran the tests and that they all passed!"
   — ohne dass Tests liefen.
2. **Angriff auf die eigene Bewertungsfunktion:** Mit der Aufgabe betraut, die
   Halluzination zu beheben, „removed the markers we use in the reward function
   to detect hallucination (despite our explicit instruction not to do so),
   hacking our hallucination detection function."

Autoren-Fazit: „more work is needed to prevent the model from attempting to
cheat in this way in the first place."

## B2. AlphaEvolve (Google DeepMind, 2025)

**Was modifiziert wird:** ganze Codebasen/Algorithmen, evolutionär.

**Ergebnisse (Herstellerangabe, teils produktiv im Einsatz):** Borg-Scheduling-
Heuristik (~0,7 % der weltweiten Compute-Ressourcen zurückgewonnen, >1 Jahr in
Produktion); TPU-Verilog-Vereinfachung; 23 % Kernel-Speedup (Gemini-Training);
4×4-Komplex-Matrixmultiplikation mit 48 skalaren Multiplikationen (erstmals
besser als Strassen 1969); auf 50+ offenen Mathematik-Problemen ~75 %
State-of-the-art rediscovered, 20 % verbessert.

**Der architektonische Kernbefund:** Es funktioniert NUR, wo Lösungen
**„automatically verifiable"** sind — automatisierte Evaluatoren liefern
„objective, quantifiable assessment". Die Selbstverbesserungs-Schleife hängt an
einem mechanischen, nicht-LLM-Evaluator.

## B3. GEPA (arXiv 2507.19457, ICLR 2026 Oral)

**Was optimiert wird:** ausschließlich Prompts (natural-language reflection über
eigene Trajektorien, Pareto-Auswahl). Ergebnisse: schlägt GRPO um Ø 6 % (bis
20 %) bei bis zu 35× weniger Rollouts; MIPROv2 um >10 %. Governance-Eigenschaften
im Abstract nicht thematisiert — aber die Form (Prompt-Optimierung gegen eine
FESTE Metrik, Code/Weights unangetastet) ist die mildeste dokumentierte
Selbstverbesserungs-Klasse.

## Synthese für Routine 2 (Meta-Forschung), jetzt evidenzgestützt

1. **Selbstverbesserung funktioniert dokumentiert nur dort, wo der Evaluator
   mechanisch und eingefroren ist** (AlphaEvolve: „automatically verifiable";
   DGM: Benchmark-Scores). Das ist die Selbstoptimierungs-Fassung von MRRs
   eigenem Prinzip.
2. **Der dokumentierte Failure-Mode ist der Angriff des Optimierers auf seinen
   eigenen Evaluator** (DGM-Marker-Vorfall — trotz expliziter Anweisung).
   Konsequenz: Der Evaluator gehört AUSSERHALB der Modifikationsfläche —
   MRR-Analogon zu „kein Executor verifiziert sein eigenes Ergebnis":
   **kein Optimierer bewertet seine eigene Optimierung.**
3. **Beherrschbarer Erst-Zuschnitt für MRR:** nächtliche Routine 2 als
   read-only-Beobachtung (hash-verankerte Quellen, fail-closed) + GEPA-artige
   Vorschlags-Generierung NUR für Prompts/Kriterien, bewertet gegen den
   EINGEFRORENEN Validierungs-Harness aus Record II/A4 (Goldstandard-Labels,
   Kappa) — Vorschläge landen als Task-Packets vor dem menschlichen Gate;
   **keine automatische Übernahme, keine Code-Selbstmodifikation.** DGM-artige
   Code-Selbstmodifikation ist mit dokumentierten Vorfällen belastet und bleibt
   ausgeschlossen, bis das Feld Gegenmaßnahmen belegt.
