# Zaun-Gegenprobe: was der Allgemeinheitszaun am Goldsatz wirklich kostet

**Status:** Re-Ableitung. **Kein Label, keine Definition, keine Fremdsession wird
angefasst.** Das Ergebnis ist ein Intervall, keine neue Wahrheit.

**Anlass:** Ulysses' Befund 4.1 aus `RETURN-2026-08-01.md` — `supports` trägt in
den Kriterien eine Allgemeinheitsforderung, `contradicts` trägt keine. Der Owner
hat die Frage seit 2026-08-01 offen gehalten und am 2026-08-02 entschieden, sie
zu **messen statt zu entscheiden**. Das hier ist die Messung.

---

## 1. Die Asymmetrie, wörtlich

Aus `mb-cls-criteria.v3.json`, den Definitionen, unter denen die sechzig Fälle
blind gelabelt wurden (Definitionen byte-identisch zu v2):

> **supports** — „…that the claim holds: some verification step exists and is
> separate from the component whose output it checks. **A general assertion, not
> one fenced to a named subset.**"
>
> **contradicts** — „…that the claim does not hold: either no verification step
> exists, or the checking is performed by the same component (or the same model,
> or the same process) that produced the output. Self-review counts as
> contradicting, because the claim is about independence, not about the
> existence of review."
>
> **qualifies** — „…neither asserts nor denies the claim in general, but narrows
> it: it holds for **a named subset** (some outputs, some stages, some
> configuration), or under a stated condition…"

Der Satz mit der Fettung steht nur bei `supports`. Bei `contradicts` fehlt er.

Die Behauptung, gegen die alles gelesen wird, ist eine einzige und für alle
sechzig Fälle dieselbe (nachgerechnet: `len(set(claim_text)) == 1`):

> *„Systems that automate the research cycle end to end verify their own outputs
> independently of the component that produced them."*

Die Grundgesamtheit ist damit benannt: **Systeme, die den Forschungszyklus
Ende-zu-Ende automatisieren.** Ein Beleg über Roboterplanung, Textaufgaben oder
Computerbedienung ist gegenüber dieser Grundgesamtheit eingezäunt. Unter
`supports` fällt ein solcher Beleg deshalb heraus (er ist „fenced to a named
subset") und landet in `qualifies`. Unter `contradicts` fällt er **nicht**
heraus, weil dort kein solcher Satz steht. Das ist die Asymmetrie, und sie
wirkt genau in eine Richtung.

## 2. Die Kandidatenmenge ist exakt, nicht geschätzt

`decided_by` macht sie exakt (Ulysses' eigener Vorschlag). Nachgerechnet am
2026-08-02 gegen `corpora/gold-classification/mb-cls-ulysses-v1-restamped.json`:

| `expected_relation` | n |
|---|---|
| qualifies | 24 |
| contextualizes | 20 |
| contradicts | 12 |
| supports | 1 |
| (undecidable, kein Label) | 3 |

Von den zwölf `contradicts`:

| `decided_by` | n |
|---|---|
| `contradicts definition (self-review clause)` | **10** |
| `contradicts definition` | 2 |

Die zwei übrigen wurden gelesen und sind **keine** Kandidaten: `mbcls-2511.13825`
(Audit eines autonomen AI-Scientist gegen Null-Modelle) und `mbcls-2607.26064`
(die Lücke zwischen agentischer Produktion und Prüfkapazität) sprechen beide
über den Forschungszyklus selbst. Sie blieben unter jedem Zaun `contradicts`.
Die Zehn sind die vollständige Kandidatenliste.

**Ein Regex über die Begründungen taugt nicht.** „its own" trifft
Selbstprüfung, nicht Einzäunung; eine frühere Zählung kam so auf „nur 3 von 10
nennen Zaun-Sprache" und misst damit die Wortwahl der Begründung statt den
Gegenstand des Belegs. Die Zehn wurden gelesen.

## 3. Die zehn, einzeln einsortiert

Sortiert wird in (a) **Forschungszyklus-System** — der Beleg spricht über die
Grundgesamtheit der Behauptung, bleibt also unter jedem Zaun `contradicts` — oder
(b) **fachfremd eingezäunt** — der Beleg spricht über ein anderes Feld und würde
unter einem symmetrischen Zaun nach `qualifies` wandern.

| # | case_id | Gegenstand des Belegs | Einordnung |
|---|---|---|---|
| 1 | `mbcls-2408.06292` | The AI Scientist: Ideen → Code → Experiment → Paper → eigener automatisierter Reviewer | **(a)** |
| 2 | `mbcls-2409.04109` | LLM-Ideations­agent gegen 100 NLP-Forschende; „failures of LLM self-evaluation" bei den eigenen Agent-Baselines | **(a/b) Grenzfall** |
| 3 | `mbcls-2502.19613` | Self-rewarding reasoning LLMs, Selbstkorrektur auf Reasoning-Aufgaben (Llama-3, Qwen-2.5) | **(b)** |
| 4 | `mbcls-2506.02918` | DyMo: Werkzeuggebrauch in zustandsbehafteten Umgebungen, Berkeley Function Calling Leaderboard | **(b)** |
| 5 | `mbcls-2506.11442` | ReVeal: Codegenerierung mit Selbstverifikation, LiveCodeBench | **(b)** |
| 6 | `mbcls-2510.19949` | Surfer 2: Web-/Desktop-/Mobile-Bedienung, self-verification with adaptive recovery | **(b)** |
| 7 | `mbcls-2601.00828` | Zerlegung der Selbstkorrektur auf GSM8K-Complex — Textaufgaben | **(b)** |
| 8 | `mbcls-2601.03315` | Vier Ende-zu-Ende-Versuche, ML-Paper autonom zu erzeugen; sechs Fehlermodi | **(a)** |
| 9 | `mbcls-2602.04288` | Contextual drag: 11 Modelle, 8 Reasoning-Aufgaben; Selbstverfeinerung kollabiert | **(a/b) Grenzfall** |
| 10 | `mbcls-2604.17406` | EvoMaster/SciMaster: „Agentic Science", Selbstkritik über Experimentierzyklen | **(a)** |

**Eindeutig (a): 1, 8, 10** — drei Systeme, die den Forschungszyklus selbst
automatisieren und ihre eigene Ausgabe beurteilen.

**Eindeutig (b): 3, 4, 5, 6, 7** — fünf Belege aus Reasoning, Werkzeuggebrauch,
Codegenerierung, Computerbedienung und Textaufgaben. Ulysses' Befund 4.3 nennt
drei dieser fünf Felder wörtlich.

**Grenzfälle: 2 und 9.** Beide sind der Grund, warum das Ergebnis ein Intervall
ist und keine Zahl:

- **2** ist forschungsnah, aber **stufen**-eingezäunt: der untersuchte Agent
  ideiert, er durchläuft keinen Zyklus. Das Paper sagt es selbst — „no
  evaluations have shown that LLM systems can take the very first step …, let
  alone perform the entire research process." Unter „nicht auf eine benannte
  Teilmenge eingezäunt" wäre *some stages* ein Zaun; unter „spricht es über
  Forschungsautomatisierung?" ist es keiner.
- **9** ist umgekehrt gelagert: fachfremd (Reasoning-Aufgaben), aber der Befund
  ist ausdrücklich breit — 11 Modelle, 8 Aufgaben, „a persistent failure mode in
  current reasoning architectures". Ein Zaun-Begriff, der auf Allgemeinheit
  abstellt, fasst ihn nicht; einer, der auf die Grundgesamtheit der Behauptung
  abstellt, fasst ihn.

## 4. Das Ergebnis als Intervall

Nach dem Präzedenzfall der Tie-Break-Auflösung in `mb-cls-criteria.v3.json`:
zweimal gerechnet, die Spanne dazwischen ist die ehrliche Breite.

**Lesart A (strenger Zaun** — eingezäunt ist jede Verengung weg von „dem
Forschungszyklus Ende-zu-Ende"; Grenzfälle wandern):
wandern 7 (2, 3, 4, 5, 6, 7, 9) → `contradicts` **12 → 5**

**Lesart B (großzügiger Zaun** — (a), sobald der Gegenstand
Forschungsautomatisierung ist, und ein breiter Querschnittsbefund gilt nicht als
„auf eine benannte Teilmenge eingezäunt"; Grenzfälle bleiben):
wandern 5 (3, 4, 5, 6, 7) → `contradicts` **12 → 7**

| | heute | unter symmetrischem Zaun |
|---|---|---|
| supports | 1 | 1 |
| contradicts | 12 | **5 – 7** |
| qualifies | 24 | **29 – 31** |
| contextualizes | 20 | 20 |

`supports` bewegt sich in **keiner** Lesart. Der Zaun kann nur Fälle aus
`contradicts` heraustragen, nie welche in `supports` hineintragen — Ziel jeder
Wanderung ist `qualifies`, weil ein eingezäunter Beleg genau dessen Definition
erfüllt.

**Damit ist Ulysses' Befund 4.1 bestätigt und zugleich beziffert.** Die
Asymmetrie erklärt **5 bis 7 der 12** `contradicts` — zwischen 42 % und 58 %.
Das Verhältnis supports:contradicts ginge von 1:12 auf 1:5 bis 1:7. Es bliebe
schief. Der Zaun ist der größte Einzelgrund, aber nicht der einzige: auch
symmetrisch gelesen findet dieser Korpus fast nichts, das die Behauptung stützt.

## 5. Gegenprobe am zweiten Leser — und was sie widerlegt

Ulysses' 4.1 legt nahe: *„wo ein zweiter, unabhängiger Leser dieselben Zäune
trifft, ist der Zaun das Problem."* Der zweite Leser existiert seit N1-T04.
Nachgerechnet gegen `predictions-gemini-3.5-flash-lite.json`:

| Einordnung oben | case_id | Modell sagte |
|---|---|---|
| (a) | `mbcls-2408.06292` | contextualizes |
| (a/b) | `mbcls-2409.04109` | contextualizes |
| (b) | `mbcls-2502.19613` | **contradicts** ✓ |
| (b) | `mbcls-2506.02918` | contextualizes |
| (b) | `mbcls-2506.11442` | qualifies |
| (b) | `mbcls-2510.19949` | qualifies |
| (b) | `mbcls-2601.00828` | contextualizes |
| (a) | `mbcls-2601.03315` | contextualizes |
| (a/b) | `mbcls-2602.04288` | contextualizes |
| (a) | `mbcls-2604.17406` | **contradicts** ✓ |

**Die Zaun-Hypothese erklärt die Fehler des zweiten Lesers nicht.** Ein Zaun
würde `contradicts → qualifies` erzeugen. Das Modell erzeugt siebenmal
`contradicts → contextualizes` und nur zweimal `qualifies`. Seine zwei Treffer
liegen zudem auf **beiden** Seiten der Einsortierung (ein (a), ein (b)) — die
Trennlinie oben sagt nichts über seinen Erfolg vorher.

Sein dominanter Fehler ist ein anderer: es liest die Architekturbeschreibung
eines Systems als *Hintergrund* („takes no position") statt als **den eigenen
Beleg der Quelle über sich selbst**. Das ist derselbe Fehlermodus wie die null
Unentscheidbaren von sechzig — das Modell weicht der Festlegung aus, wo die
Kriterien eine verlangen.

Das ist ein Befund über den zweiten Leser, kein Argument über den Zaun. Die
Einsortierung in §3 steht deshalb allein auf dem Lesen der zehn Belege.

## 6. Was daraus NICHT folgt

- **Keine Kriterien v4.** Darunter liegen sechzig blinde Labels; eine
  Definitionsänderung entwertet sie rückwirkend. Diese Notiz ändert nichts.
- **Keine Bitte an Ulysses.** Kein Umstempeln, kein Neu-Labeln, keine Anfrage.
- **Keine neue Wahrheit.** Das Intervall 5–7 ist eine *Ableitung unter einer
  hypothetischen Definition*, kein gemessener Wert. Wo es zitiert wird, wird es
  als Spanne zitiert, mit beiden Lesarten daneben.
- **Kein Ersatz für eine Entscheidung.** Ob der Zaun symmetrisch wird, bleibt
  offen und beim Owner. Diese Notiz liefert ihm nur, was die Entscheidung
  kostet: zwischen fünf und sieben von zwölf.

## 7. Reproduktion

```bash
# Kandidatenmenge
python3 -c "
import json, collections
d = json.load(open('corpora/gold-classification/mb-cls-ulysses-v1-restamped.json'))
sel = [c for c in d['cases']
       if c['expected_relation'] == 'contradicts'
       and c['decided_by'] == 'contradicts definition (self-review clause)']
print(len(sel), [c['case_id'] for c in sel])
"

# Gegenprobe am zweiten Leser
python3 -c "
import json
g = json.load(open('corpora/gold-classification/mb-cls-ulysses-v1-restamped.json'))
p = json.load(open('corpora/gold-classification/predictions-gemini-3.5-flash-lite.json'))['predictions']
for c in g['cases']:
    if c['decided_by'] == 'contradicts definition (self-review clause)':
        print(c['case_id'], '->', p.get(c['case_id']))
"
```

Die Einsortierung selbst ist nicht skriptbar: sie beruht auf dem Lesen von zehn
`expected_rationale`-Texten und ihrer Auszüge. Wer sie prüfen will, liest sie.
