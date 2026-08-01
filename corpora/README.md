# Eine Frage stellen

Dieses Verzeichnis ist der Ort, an dem Meridian seine Forschungsfragen stellt.
**Einen Korpus zu committen IST das Stellen der Frage.** Niemand startet danach
etwas: der Workflow `.github/workflows/research-run.yml` findet die Frage
nächtlich, fährt sie online auf einem frischen Läufer mit eigener PostgreSQL,
und legt das Ergebnis als Pull Request vor.

Kein Knopfdruck, keine lokale Maschine.

## Was eine Frage ist

Ein Unterverzeichnis mit **genau diesen fünf Dateien**:

| Datei | Was darin steht |
|---|---|
| `question-model.proposal.json` | Die Frage im Wortlaut, ihre Population, ihr Zeitfenster, ihre Bedingungen |
| `concept-charter.proposal.json` | Die tragenden Begriffe, streng definiert — hier entscheidet sich, was die Frage überhaupt bedeutet |
| `method-protocol.proposal.json` | Ein- und Ausschlusskriterien, die geplanten Analysen, die Abbruchbedingungen |
| `corpus-entries.json` | Die Belege, jeder mit seiner Einordnung (`supports` / `contradicts` / `qualifies` / `contextualizes`) und der Begründung dafür |
| `protocol-parameters.sidecar.json` | Schwellen: wie viele unabhängige Quellfamilien eine Aussage tragen muss, und wann der Lauf abbricht |

Fehlt eine davon, wird das Verzeichnis übergangen — nicht als Mangel gemeldet.
So können hier auch reine Audit-Eingaben liegen (`archive-integrity`,
`e2e-survey`, `research-records`), die keine Synthese-Frage sind.

Vorlagen: `model-collapse/` (die erste Frage) und `e2e-claims/` (die zweite,
mit acht Quellen und einem `contested`-Ergebnis).

## Was die Maschine tut — und was sie nicht tut

**Sie ordnet nicht ein.** Die Einordnung jedes Belegs steht im Korpus, von der
Praxis gesetzt, bevor die Maschine etwas sieht. Sie prüft die Hashes, filtert
nach den erklärten Kriterien, baut die Belegmatrix, wendet die Deckelungsregeln
an und versiegelt das Ergebnis. Wo Belege sich widersprechen, bleibt der
Widerspruch stehen.

**Sie weigert sich zu übertreiben.** Eine Aussage, die der Korpus nicht trägt,
entsteht nicht. Eine Aussage mit einem Beleg dafür und dreien dagegen landet auf
`contested` und nicht auf einem Mittelwert.

**Sie erfindet nichts.** Fehlt die Code-Revision, bricht der Lauf ab statt eine
zu erfinden. Ist eine Sensitivitätsvariation deklariert aber nicht geliefert,
bricht er ab. Beides ist ein Ergebnis, kein Werkzeugfehler — die versiegelte
Kiste nennt den Grund.

## Redlichkeit im Korpus

- **Jeder `claim_relevant_finding` muss aus der Quelle selbst belegt sein**, nicht
  aus ihrer Beschreibung durch Dritte. In `e2e-claims` sind die Abstracts über
  `scripts/fetch_source_content.py` geholt, gehasht und committet — die
  Einordnung ist gegen sie geschrieben, nicht gegen die Seite, die sie zitiert.
- **Was die Quelle nicht hergibt, wird `unverifiable`**, mit Grund. Nie geraten.
- **`source_family_id` trennt unabhängige Belege von Kopien.** Zwei Texte
  desselben Autorenteams über dieselbe Sache sind eine Familie, nicht zwei
  Belege.

## Nach dem Lauf

Der Workflow trägt die Frage in `archive/answered.json` ein und sichert den
vollständigen Datenbankzustand als Dump unter `archive/dumps/`. Damit wird sie
in der nächsten Nacht nicht erneut gefahren.

Eine **Variation** derselben Frage (andere Schwelle, andere Definition) ist ein
neuer Korpus mit neuem Namen, kein Nachbearbeiten des alten. Committete
Archivstände bleiben unangetastet.
