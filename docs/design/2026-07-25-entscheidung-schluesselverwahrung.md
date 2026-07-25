# Entscheidung: private Schlüssel leben als GitHub-Secret (2026-07-25)

**Status:** Entschieden — vom Owner (Frank) persönlich, in Session am 2026-07-25,
auf die zusammengeführte Vorlage aus `docs/design/2026-07-25-n2-t03-derivation.md`
(„Zusammengeführte Owner-Entscheidung: wo lebt privates Schlüsselmaterial?").

## Die Entscheidung

**Privates Schlüsselmaterial wird als GitHub-Secret verwahrt.** Das gilt für
beide bisher gestoppten Stränge:

1. **E5-T09 (Föderation):** Meridians eigenes Signaturschlüsselpaar. Die
   **öffentliche** Hälfte kommt ins Repo (sie ist der Vertrauensanker, den die
   Gegenseite deklarieren muss); die **private** Hälfte geht in ein GitHub-Secret
   und niemals ins Repo.
2. **N2-T03c (Modell-Schritt):** der Provider-Key eines künftigen konkreten
   `ModelAdapter` — ebenfalls GitHub-Secret, nie im Repo, nie in einem Prompt
   oder Vermerk (AGENTS Regel 11).

Damit ist die Verwahrungsfrage für beide Stränge **geschlossen**. Was jeweils
noch fehlt, ist unten benannt.

## Was daraus folgt — ehrlich, weil es die Bauform ändert

Ein GitHub-Secret ist **nur aus einem Workflow lesbar**. Das ist keine
Nebenwirkung, sondern verschiebt beide Fähigkeiten von der lokalen Kommandozeile
in die nächtliche Routine:

- **`mrr federation outbox write` wird ein Workflow-Schritt, kein lokaler
  Operator-Befehl.** E5-T08 hat den Adapter bewusst zustandslos gebaut (volle
  Pfade je Aufruf statt verstecktem Wurzelverzeichnis) — das passt, aber die
  Signatur entsteht künftig in Actions. Lokal bleibt alles fahrbar, **außer**
  dem Signieren.
- **Ein Modell-Schritt läuft in Actions, nicht am Schreibtisch.** Das ist
  deckungsgleich mit Routine 2, die ohnehin als nächtlicher Workflow entworfen
  ist (Capability-Roadmap, „Die zwei Routinen").
- **Die Signaturvollmacht ist genau so weit wie der Schreibzugriff aufs Repo.**
  Wer einen Workflow ins Repo pushen kann, kann als Meridian signieren. Das ist
  eine reale Eigenschaft dieser Wahl und wird hier festgehalten statt beschönigt.
  Sie ist zugleich **kohärent**: Git ist in diesem System ohnehin autoritativ für
  Code, Schemata, Prompts, Policies (AGENTS, „Source-of-truth discipline"), und
  die beiden Praktiken tauschen über öffentliche Repositories aus (E5-T08). Die
  faktische Autorität lag also schon vorher beim Repo-Schreibzugriff; diese
  Entscheidung macht sie für Signaturen nur explizit.

## Was noch fehlt (nicht Gegenstand dieser Entscheidung)

- **Die Schlüsselerzeugung selbst** ist ein Akt in E5-T09, nicht hier. Sie
  geschieht lokal; die private Hälfte wird ins GitHub-Secret eingetragen und
  danach lokal gelöscht, die öffentliche committet.
- **Ulysses' Schlüssel bleibt Ulysses' Sache.** Unverändert gilt die Grenze aus
  `docs/design/2026-07-25-e5-t08-derivation.md`: **Meridian erzeugt niemals einen
  Schlüssel für eine fremde Praxis.** Ein Schlüsselpaar für Ulysses zu erzeugen
  wäre die Fälschung genau der Unabhängigkeit, die das System behauptet. E5-T09
  ist damit Meridian-seitig entsperrt und Ulysses-seitig weiterhin offen — die
  andere Praxis muss in **eigener** Session veröffentlichen.
- **R1-T01** (erste Joint Inquiry am offenen Hammond-Dissens) setzt beide Hälften
  voraus und bleibt unberührt.
- **N2-T03c** bleibt zusätzlich durch die eigene Evidenz blockiert (LLM-Judges
  < 85 %, κ 0,19–0,51): die Verwahrungsfrage war nicht der einzige Grund, den
  Modell-Schritt zu vertagen, und ihre Beantwortung hebt den anderen nicht auf.
  N2-T03a/b werden modellfrei gebaut.
