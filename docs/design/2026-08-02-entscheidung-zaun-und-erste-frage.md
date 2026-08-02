# Zwei Entscheidungen: der Zaun bleibt, und was der erste Korpus prüft

**Status:** Entscheidung, getroffen 2026-08-02. Der Owner hat beide Punkte
ausdrücklich delegiert („setz du", „entscheide du"). Sie stehen hier mit
Begründung und mit ihren Umkehrkosten, damit sie prüfbar und widerrufbar sind.

---

## 1. Der Allgemeinheitszaun bleibt. Keine Kriterien v4 für diesen Maßstab.

**Die Frage:** Ulysses' Befund 4.1 — `supports` trägt eine
Allgemeinheitsforderung („A general assertion, not one fenced to a named
subset"), `contradicts` trägt keine. Soll die Definition symmetrisch werden?

**Die Entscheidung: nein.** Vier Gründe, in der Reihenfolge ihres Gewichts.

### 1.1 Darunter liegt eine fertige Blindarbeit

Sechzig Fälle wurden blind unter v3 gelabelt (Definitionen byte-identisch zu
v2, unter denen die Arbeit entstand). Eine Definitionsänderung entwertet sie
rückwirkend: die Labels bleiben, aber die Frage, die sie beantworten, wäre eine
andere. Ulysses hat den Umstempel bereits abgelehnt und hatte recht — „the
order gate that refuses the set, and the labels themselves" gehören der
ausgebenden Seite.

### 1.2 Der Zaun rettet `supports` nicht — gemessen, nicht vermutet

Die Gegenprobe (`2026-08-02-zaun-gegenprobe-re-ableitung.md`) hat den Effekt
beziffert, statt ihn zu schätzen:

| | heute | unter symmetrischem Zaun |
|---|---|---|
| supports | 1 | **1** |
| contradicts | 12 | 5 – 7 |
| qualifies | 24 | 29 – 31 |

**`supports` bewegt sich in keiner Lesart.** Der Zaun kann Fälle nur aus
`contradicts` heraustragen, nie welche in `supports` hineintragen — Ziel jeder
Wanderung ist `qualifies`. Das Verhältnis ginge von 1:12 auf 1:5 bis 1:7 und
bliebe schief.

Damit ist die naheliegende Hoffnung widerlegt: Die Asymmetrie ist **nicht** der
Grund, warum dieser Korpus die Behauptung fast nicht stützt. Sie erklärt
zwischen 42 % und 58 % der `contradicts` und **null Prozent** der fehlenden
`supports`. Wer den Zaun symmetrisch macht, bekommt einen weniger schiefen
Zähler und dieselbe Aussage über das Feld.

### 1.3 Der zweite Leser stolpert nicht über den Zaun

`R-undecidable-is-a-finding` und die Zaun-Frage sind verschiedene Dinge, und
die Messung trennt sie. Von den zehn Kandidatenfällen ordnete das Modell
**sieben** als `contextualizes` ein und nur **zwei** als `qualifies`. Ein Zaun
erzeugt `contradicts → qualifies`. Er erzeugt nicht `contradicts →
contextualizes`.

Der Fehler des zweiten Lesers liegt woanders: Er liest die Architektur­beschreibung
eines Systems als *Hintergrund* statt als **den eigenen Beleg der Quelle über
sich selbst**. Eine symmetrische Definition würde daran nichts verbessern.

### 1.4 Damit ist der Handel schlecht

Kosten: ein fertiger, blind gelabelter Goldstandard verliert seine Grundlage.
Nutzen: ein Effekt, der bereits gemessen, beziffert und dokumentiert ist und
die Kernaussage nicht bewegt.

**Also bleibt v3 der Maßstab, mit seinem bekannten Fehler, und der Fehler wird
mitgeliefert statt versteckt.**

### 1.5 Was stattdessen gilt

- Die Asymmetrie steht in der `scope_note` jedes Korpus, den der
  Literaturkanal erzeugt, mit Verweis auf die Gegenprobe. Ein Leser trifft sie,
  bevor er eine Zahl liest.
- **Die Bedingung für eine künftige v4**, falls jemand sie doch will: eine
  **beauftragte Neu-Ableitung** durch die labelnde Praxis — kein Neu-Labeln,
  keine einseitige Änderung, und ein neuer eingefrorener Satz unter neuem
  Namen. Der alte bleibt committet und lesbar; ein Kriteriensatz wird nie
  editiert.
- **Umkehrkosten dieser Entscheidung:** null. Sie ändert nichts. Wer sie
  revidieren will, revidiert eine Unterlassung, nicht einen Eingriff.

---

## 2. Der erste Korpus prüft dieselbe Behauptung wie der Goldstandard

**Die Frage:** Welche Behauptung soll `corpora/lit-2026-08-a` prüfen?

**Die Entscheidung:**

> *„Systems that automate the research cycle end to end verify their own
> outputs independently of the component that produced them."*

Dieselbe, gegen die die sechzig blinden Fälle gelabelt wurden.

**Warum.** Der Kanal zieht seine Quellen aus demselben Kandidatenpool wie der
Goldsatz — dieselben vierzehn eingefrorenen Suchen, dasselbe Subjekt. Eine
andere Behauptung hieße, Material für Frage A gegen Frage B zu halten, und die
Einschluss­kriterien des Pools würden nicht mehr passen.

Der eigentliche Gewinn ist aber ein anderer: Mit derselben Behauptung ist der
Lauf **eine dritte, unabhängige Lesart desselben Feldes.** Es gibt dann

1. Ulysses' sechzig blinde Labels (Mensch-analog, fremde Praxis),
2. die Messung des Modells gegen genau diese sechzig (0,5439 / κ 0,3084),
3. und **293 ungezogene Quellen**, aus denen sich beliebig viele weitere,
   disjunkte Chargen ziehen lassen — nach derselben urteilsfreien Regel.

Die Verteilung, die Charge 1 produziert, ist damit direkt gegen die
Goldverteilung lesbar (24 qualifies / 20 contextualizes / 12 contradicts /
1 supports). Weicht sie stark ab, ist das ein Befund über die Ziehung oder über
das Modell — nicht über das Feld. Das wäre bei einer frei gewählten anderen
Behauptung nicht zu trennen.

**Was das ausdrücklich nicht ist:** kein zweiter Goldstandard. Die Einordnungen
dieses Korpus sind Modellvorschläge mit einer gemessenen Fehlerrate von rund
jedem zweiten Fall. Sie werden nirgends als Labels behandelt und dürfen nie in
einen Maßstab zurückfließen — das wäre der Optimierer, der seinen eigenen
Evaluator bewertet (`2026-07-24-primaerquellen-selbstoptimierung.md`).

**Umkehrkosten:** eine Charge. Die Behauptung steht in
`question-model.proposal.json` und in `literature-channel.yml`; eine andere
Frage ist eine neue Charge mit neuem Namen, nicht eine Änderung an dieser.
