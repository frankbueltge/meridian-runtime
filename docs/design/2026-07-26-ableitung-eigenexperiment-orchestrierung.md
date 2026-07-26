# Ableitung: das Eigenexperiment — Meridian misst die Bauart, die es ablehnt (2026-07-26, Abend)

**Status:** Ableitung mit Fact-Lock, **Owner-Vorlage. Kein Bau, kein Paket.**
Anlass ist Franks eigener Vorschlag am 2026-07-26: „konsequent wäre vielleicht,
das Thema e2e automation of ai research selbst zum Thema zu machen."

Dieses Dokument prüft, was dafür wahr sein muss, und nennt den Preis ehrlich.
Es entscheidet nichts.

## Franks Vorschlag ist im Entwurf bereits angelegt — in zwei Stärken

Die Capability-Roadmap vom 2026-07-24 kennt den Gegenstand an zwei Stellen:

- **Schwach (N2):** Citation-/Claim-Verification-Audit „bedient den Gegenstand
  ‚KI-Forschung selbst'… Erster natürlicher Anwendungsfall: die eigenen
  Recherche-Records auditieren." Davon steht seit dem 2026-07-26 mit N2-T03a/b
  ein modellfreier Teil.
- **Stark (Stufe 3):** „**Das publikationsfähige Eigenexperiment:** kontrollierter
  Vergleich LLM-orchestriert vs. deterministisch orchestriert auf identischer
  Forschungsaufgabe — existiert im Feld nicht (Record I, offene Frage 3); MRR hat
  beide Bauarten im Zugriff und den Verifikations-Apparat für die Messung."

Die starke Fassung ist die interessante. Sie ist auch die, deren tragende
Behauptung nicht stimmt.

## Fact-Lock 1 — „beide Bauarten im Zugriff" ist falsch

| Arm | Zustand |
|---|---|
| deterministisch orchestriert | **existiert, ist gefahren, ist aktenkundig** — `verification_orchestration.py`, `synthesis_orchestration.py`; 24× `"kind": "deterministic"` in den Real-Run-Dumps; zwei vollständige Läufe |
| LLM-orchestriert | **existiert nicht** — weder als Code noch als Paket |

Der `ModelAdapter`-Port sagt es in seinem eigenen Docstring:

> „No concrete implementation exists in this module or anywhere under
> `packages/`/`adapters/` yet … **Tests use only an in-test fake** implementing
> this Protocol."

`adapters/llm/` enthält zwei Dateien; die erste Zeile der einen erklärt
ausdrücklich, sie sei selbst *kein* `ModelAdapter`.

## Fact-Lock 2 — der konkrete Provider-Adapter ist zwischen zwei Paketen verlorengegangen

Dies ist der eigentliche Befund, und er ist strukturell, nicht nachlässig.

`E4-T01.yaml` schließt aus:

> „any concrete provider adapter or provider SDK dependency (openai, anthropic,
> google-generativeai, boto3, litellm, etc.) … the port stays ABSTRACT here;
> **the concrete structured-generation adapter is E4-T02**"

`E4-T02` ist aber **„Implement the structured-generation adapter with bounded
schema-repair"** — und ausweislich seines eigenen objective ausdrücklich
**provider-neutral**: es nimmt einen `ModelAdapter` *injiziert* entgegen. Es baut
keinen.

Die sieben E4-Pakete lauten vollständig:

| Paket | Titel | gebaut |
|---|---|---|
| E4-T01 | provider-neutral ModelProfile und ModelInvocation record | ja |
| E4-T02 | structured-generation adapter mit bounded schema-repair | ja |
| E4-T03 | Hypothesis-Kontrakt und Planner/Proposer-Rolle | ja |
| E4-T04 | SkepticalChallenge-Kontrakt und Skeptiker-Rolle | ja |
| E4-T05 | deterministische Verifizierer-Orchestrierung und -Werkzeuge | ja |
| E4-T06 | Git-gestützte Prompt-/Versions-Registry | ja |
| E4-T07 | Modell-Benchmark-Runner und deterministische Promotion-Policy | ja |

**Kein einziges Paket im gesamten Plan baut einen konkreten Provider-Adapter.**
E4-T01 hat ihn mit einem Verweis auf E4-T02 ausgeschlossen, und E4-T02 hat ihn
durch die eigene Provider-Neutralität ebenfalls ausgeschlossen. Er ist nicht
vertagt worden — er ist **zwischen zwei Ausschlüssen hindurchgefallen**, und der
Docstring des Ports zeigt seither auf ein Paket, das ihn nicht enthält.

Das erklärt Befund 2 des Handoffs („keine Modell-Außenkante") strukturell: nicht
als Entscheidung, sondern als Lücke im Plan.

## Fact-Lock 3 — die gute Nachricht: der deterministische Arm ist wiederholbar

Anders als bei den Evidenz-Bytes (A1) sind die **Eingaben** des gefahrenen Laufs
vollständig da und hash-prüfbar:

```
corpora/model-collapse/works-atlas.snapshot.json
  gemessen  9d14e877efb245cc04b8451734b26285a274dda0418f5131db257d2e4312d373
  im QuestionModel gepinnt: identisch
```

Dazu `theory-atlas.snapshot.json`, `corpus-entries.json`, die
Question-Model-/Method-Protocol-/Concept-Charter-Vorlagen und das
`snapshot-manifest.json` — alle committet, alle vom 21. Juli.

**Der Vergleichsarm muss also nicht gebaut werden. Er muss nur wieder gefahren
werden, gegen bitgleiche Eingaben.** Das ist die Voraussetzung für „identische
Forschungsaufgabe", und sie ist erfüllt.

## Was der Vergleich wirklich kostet

Hier ist Sorgfalt nötig, weil zwei sehr verschiedene Dinge leicht zusammenfallen:

1. **Ein Modell *innerhalb* eines Schritts.** Braucht genau eine Sache: einen
   konkreten `ModelAdapter` — ein Protokoll mit **einer** Methode. Alles
   Drumherum steht bereits: Structured Generation mit begrenztem Repair (E4-T02),
   gehashte Prompt-Registry (E4-T06), `ModelInvocationOutcome` mit
   Redaktionspolitik (E4-T01), Benchmark-Runner (E4-T07), Skeptiker-Rolle
   (E4-T04). Kosten: **klein.**
2. **Ein Modell, das die *Schrittfolge* bestimmt.** Das ist „LLM-orchestriert" im
   Sinne der Roadmap. Dafür existiert **nichts** — kein Port, kein Paket, kein
   Entwurf. Und es steht in direktem Widerspruch zur stehenden Regel „LLMs …
   **nie als Orchestrator** (die quantifizierte Versagensschicht)". Kosten:
   **groß**, plus eine ausdrückliche Owner-Entscheidung.

Die Roadmap-Formulierung „beide Bauarten im Zugriff" verdeckt genau diesen
Unterschied. Der Vergleich, den sie meint, ist der teure.

## Der Ausweg: eine Leiter, nicht ein Sprung

Die drei Sprossen sind einzeln publizierbar und bauen aufeinander auf. Jede ist
ein eigenes Paket mit eigenem Nutzungsanlass; keine setzt die nächste voraus.

**Sprosse 1 — Schritt-Ebene, ein Arm.** Der konkrete Provider-Adapter, der nie
ein Paket hatte. Ein Protokoll, eine Methode, gehashter Prompt, redigierte
Aufzeichnung. Nutzungsanlass: ohne ihn ist die gesamte E4-Maschinerie
(fünf gebaute Pakete) nie an einem realen Modell gelaufen. Das ist der einzige
Bau, den alle drei Sprossen teilen.

**Sprosse 2 — Schritt-Ebene, Vergleich mit Goldstandard.** Derselbe
Klassifikations-/Verifikationsschritt, einmal deterministisch, einmal per Modell,
beide gegen einen **vorab finalisierten** Goldstandard. Das ist exakt N1-T02/T03
(Strang (b) aus dem Handoff) — die Validitäts-Hälfte, die heute fehlt. **Ohne
Goldstandard ist jeder Vergleich beider Arme wertlos**, weil kein Maßstab
existiert, gegen den „besser" etwas bedeutet. Diese Sprosse ist damit keine
Option, sondern die Bedingung von Sprosse 3.

**Sprosse 3 — Orchestrierungs-Ebene.** Erst hier der Vergleich, den die Roadmap
meint. Er verlangt einen LLM-orchestrierten Arm, den es nicht gibt, und die
ausdrückliche Owner-Entscheidung, die Versagensschicht **als Messobjekt** einmal
kontrolliert zu betreten.

Die Reihenfolge ist nicht Vorsicht, sondern Methodik: Sprosse 3 ohne Sprosse 2
produziert eine Zahl ohne Maßstab.

## Die Rollen-Grenze, die scharf bleiben muss

Wenn das System die eigene Bauart misst, ist die Versuchung groß, es auch
bewerten zu lassen. Das ist ausgeschlossen:

- **AGENTS Regel 8:** kein Executor verifiziert sein eigenes Ergebnis.
- **Roadmap, Routine 2:** „Kein Optimierer bewertet seine eigene Optimierung"
  — mit dem DGM-Vorfall als benanntem Präzedenzfall (Marker der
  Bewertungsfunktion entfernt trotz expliziter Anweisung).
- **Eigene Evidenz:** LLM-Judges liegen unter 85 %, κ 0,19–0,51. Ein Modell darf
  in diesem Experiment **Gegenstand** sein, niemals **Richter**.

Der Maßstab muss deshalb vor dem Lauf feststehen und darf von keinem Arm
stammen: der eingefrorene Goldstandard aus Sprosse 2. Das ist dieselbe Regel, die
das Paket-Ritual „Akzeptanz-Orakel VOR dem Bau, andere Implementierung als der
Erbauer" auf der Software-Ebene schon durchsetzt — hier auf der Forschungsebene.

## Warum der Gegenstand trotz allem gut ist

Drei Gründe, unabhängig voneinander:

1. **Er füllt eine benannte Lücke im Feld.** Record I, offene Frage 3: der
   kontrollierte Vergleich existiert nicht. Die Evidenzlage ist „korrelativ
   stark, experimentell offen".
2. **Er liefert den Nutzungsanlass, der seit Wochen fehlt.** Die use-first-Doktrin
   verlangt einen benannten Anlass; deshalb wurde der Provider-Adapter nie
   gebaut. Als **Messobjekt** hat er einen — und zwar einen, der die stehende
   Regel „nie als Orchestrator" nicht bricht, sondern voraussetzt.
3. **Der Vergleichsarm ist schon gelaufen**, an bitgleich vorliegenden Eingaben
   (Fact-Lock 3). Die halbe Messung ist Archiv.

## Was diese Ableitung NICHT behauptet

- **Nicht**, dass Sprosse 3 heute machbar ist. Sie hat zwei fehlende Bauteile und
  eine offene Owner-Entscheidung.
- **Nicht**, dass die Roadmap-Aussage in böser Absicht zu weit ging. Sie ist ein
  Entwurf und trägt „ENTWURF, Owner-Vorlage — keine Entscheidung" im Kopf.
- **Nicht**, dass N2 (Selbst-Audit) durch Sprosse 1–3 ersetzt wird. Es ist der
  billigere, bereits begonnene Zugang zum selben Gegenstand.
- **Nicht**, dass ein Modell irgendwo autoritativ werden dürfte. Es wird
  Gegenstand, nie Instanz.

## Zwei Owner-Entscheidungen, die diese Ableitung nicht rät

1. **Welcher Provider für Sprosse 1?** Das ist keine reine Technikfrage: die
   Wahl bestimmt, worüber das Experiment am Ende eine Aussage macht, und der
   Provider-Key lebt laut Entscheidung vom 2026-07-25 als GitHub-Secret — der
   Modell-Schritt läuft damit in Actions, nicht am Schreibtisch.
2. **Alte Frage oder frische?** Die alte (Model Collapse in 214 Kunstwerken) hat
   den unschlagbaren Vorteil, dass der deterministische Arm bereits vorliegt und
   die Eingaben hash-geprüft sind. Eine frische Frage wäre unbelastet von der
   Kenntnis des Ergebnisses — was bei einem Vergleich zweier Verfahren ein
   echtes methodisches Argument ist und nicht bloß Kosmetik.

Beide Entscheidungen drängen nicht. Sprosse 1 ist von beiden unabhängig baubar.
