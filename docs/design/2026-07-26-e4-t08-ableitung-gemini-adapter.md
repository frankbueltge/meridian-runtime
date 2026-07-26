# E4-T08-Ableitung: der erste konkrete Modell-Adapter (2026-07-26, Nacht)

**Status:** Ableitung, fact-locked gegen
`docs/design/2026-07-26-fact-lock-provider-adapter.md`. Governance-Commit **vor**
dem Bau. Owner-Entscheidungen vom 2026-07-26 abends: **Provider = Google
Gemini**, **Redaktionspolitik = `raw_permitted`** für das Experiment.

## Anlass — benannt, real, überfällig

Fünf gebaute E4-Pakete (Port, Structured Generation, Planner, Skeptiker,
Verifizierer-Orchestrierung), dazu Prompt-Registry und Benchmark-Runner, sind
**nie gegen ein reales Modell gelaufen.** Die einzige Implementierung des Ports
ist ein Fake im Test.

Der Grund war kein Beschluss, sondern eine Lücke: E4-T01 schloss den konkreten
Provider-Adapter mit Verweis auf E4-T02 aus, und E4-T02 ist selbst
provider-neutral. Er fiel zwischen zwei Ausschlüsse
(`2026-07-26-ableitung-eigenexperiment-orchestrierung.md`, Fact-Lock 2).

Der Nutzungsanlass im Sinne der use-first-Doktrin ist Sprosse 1 der dort
vorgeschlagenen Leiter: ohne diesen Adapter ist der Forschungsgegenstand
„e2e-Automation von KI-Forschung" nicht erreichbar, weil ein Vergleichsarm fehlt.

## Der Zuschnitt — und warum er kleiner ist, als der Fact-Lock nahelegt

Der Fact-Lock nannte drei Teile: den Adapter, die Wahrmachung der Netzpolitik,
und die Redirect-Lücke. Dieses Paket baut **nur den ersten**, und das ist keine
Bequemlichkeit, sondern folgt aus der Sache:

**Die Netzpolitik lügt erst, wenn ein Lauf wirklich ein Modell aufruft.** Solange
der Adapter existiert, aber in keine Orchestrierung verdrahtet ist, verzeichnet
kein `RunManifest` etwas Falsches. Die Übereinstimmung „nichts erlaubt / nichts
geschehen" bleibt intakt.

Damit ist die Reihenfolge zwingend und ausdrücklich festgehalten: **die
Verdrahtung in einen Lauf ist ein eigenes Paket und setzt die Wahrmachung der
Netzpolitik voraus.** Dieses Paket schreibt das in `explicitly_not`, damit
niemand es später beiläufig tut.

Dasselbe Muster wie E5-T10 und E5-T11: die Kante bauen, den Vollzug nicht.

**Die Redirect-Lücke ist entkoppelt.** Sie sitzt in der Allowlist von `scripts/`
und betrifft freie Ziel-URLs. Dieser Adapter spricht gegen **eine feste
Provider-Adresse** und folgt Umleitungen ausdrücklich **gar nicht**. Er erbt die
Lücke also nicht. Sie bleibt ein eigener, offener Mangel und wird von diesem
Paket weder behoben noch berührt.

## Tragende Entwurfsentscheidungen

### 1. Kein SDK, keine neue Abhängigkeit — stdlib `urllib`

E4-T01 hat Provider-SDKs namentlich ausgeschlossen (`openai, anthropic,
google-generativeai, boto3, litellm`). Ein Wiedereinzug wäre zu begründen; hier
gibt es nichts zu begründen, weil Geminis HTTP-API mit der Standardbibliothek
vollständig erreichbar ist. Die sieben Produktionsabhängigkeiten bleiben
unverändert, und `pip-audit` bekommt keine neue Fläche.

### 2. Der Schlüssel geht in den Header, niemals in die URL

Googles Generative-Language-API akzeptiert den Schlüssel **auch** als
Query-Parameter `?key=…`. Das ist die bequeme und die falsche Form: eine URL
erscheint in Ausnahmetexten, in Zeitüberschreitungsmeldungen und in jedem
Protokoll, das die angeforderte Adresse notiert. Der Schlüssel wäre damit genau
dort, wo AGENTS Regel 11 ihn verbietet.

**Der Adapter benutzt ausschließlich den Header `x-goog-api-key`.** Ein Test
prüft, dass die zusammengebaute URL den Schlüssel unter keinen Umständen trägt.

### 3. Der Schlüssel kommt aus der Umgebung, nie aus einem Argument

Folgt der Verwahrungs-Entscheidung vom 2026-07-25: GitHub-Secret, aus einem
Workflow in die Umgebung, von dort in den Adapter. Kein Parameter, kein
Dateipfad, keine Konfigurationsdatei — was nicht übergeben werden kann, kann auch
nicht versehentlich in einem Aufrufprotokoll landen.

Fehlt die Variable, ist das eine **typisierte Verweigerung vor jedem Netzverkehr**,
kein Aufruf, der beim Provider mit 401 endet.

### 4. Der Transport wird injiziert, damit Tests hermetisch bleiben

AGENTS Regel 11 verbietet unbeschränkten Netzverkehr; Tests müssen ohne Netz
laufen. Der Adapter nimmt seinen HTTP-Transport deshalb als Abhängigkeit
entgegen. Die Vorgabe ist ein `urllib`-Transport mit **abgeschalteten
Umleitungen** und gesetztem Zeitlimit; in Tests tritt ein Doppel an seine Stelle,
das aufgezeichnete Provider-Antworten zurückgibt.

**Kein automatischer Test macht je einen echten Netzaufruf.** Der reale Aufruf
ist ein gesonderter, dokumentierter Handgriff des Owners mit seinem Schlüssel.

### 5. Die fünf Endzustände ehrlich abbilden — der Kern des Pakets

Hier entscheidet sich, ob der Adapter etwas taugt. `TerminalStatus` hält fünf
Werte ausdrücklich getrennt, und AGENTS verbietet wörtlich, sie zu einem
generischen Fehler zusammenzuziehen. Die Abbildung:

| Provider-Antwort | Zustand | warum nicht anders |
|---|---|---|
| Antwort mit Text | `completed` | — |
| `finishReason: SAFETY` / `promptFeedback.blockReason` | **`content_filtered`** | ein Filter hat eingegriffen; das ist kein Fehler und keine Weigerung des Modells |
| Modell verweigert inhaltlich, ohne Filter | **`refused`** | die Weigerung des Modells ist ein Befund, kein Defekt |
| Zeitlimit überschritten | **`timed_out`** | nicht zu `error` verschmelzen — die Unterscheidung trägt die spätere Messung |
| HTTP-Fehler, Parsefehler, Transportfehler | `error` | — |

`response_hash` existiert **genau dann**, wenn `completed` — der Konstruktor von
`ModelInvocationOutcome` erzwingt beide Richtungen ohnehin; der Adapter darf ihn
nie umgehen.

### 6. `raw_permitted` wird durchgereicht, nie gesetzt

Owner-Entscheidung ist, dass das Experiment Rohtexte aufbewahrt. Der Adapter
**entscheidet das nicht** — er übernimmt die `redaction_policy` aus dem `Request`
und reicht sie an `apply_redaction` weiter. Unter `hashes_only` gibt er niemals
Rohtext zurück, auch nicht versehentlich; das ist strukturell durch den Helfer
gesichert und wird zusätzlich geprüft.

So bleibt die Datenschutz-Entscheidung dort, wo sie hingehört: beim Aufrufer, pro
Aufruf, sichtbar.

## Das Akzeptanz-Orakel — VOR dem Bau festgelegt

Der scharfe Fall ist **nicht** „ein Aufruf funktioniert". Er ist:

> **Jeder der fünf Endzustände wird aus einer je eigenen, aufgezeichneten
> Provider-Antwort erzeugt — insbesondere wird eine sicherheitsgefilterte Antwort
> zu `content_filtered` und nicht zu `error`, und eine Zeitüberschreitung zu
> `timed_out` und nicht zu `error`.**

Denn genau hier scheitern Adapter in der Praxis: sie werfen alles Unangenehme in
einen Sammeltopf, und die spätere Messung kann Weigerung, Filterung und Defekt
nicht mehr auseinanderhalten. Das wäre für ein Experiment über Modellversagen
fatal.

Die zweite scharfe Prüfung ist das Schlüssel-Leck: der Schlüssel darf in keiner
zusammengebauten URL, keiner Ausnahmemeldung, keinem Rückgabefeld und keiner
Konsolenausgabe erscheinen — geprüft an allen fünf Pfaden, nicht nur am
glücklichen.

Der Prüfer implementiert das Orakel unabhängig; der Erbauer verifiziert sein
eigenes Ergebnis nicht (AGENTS Regel 8).

## Was dieses Paket ausdrücklich nicht tut

- **Keine Verdrahtung in eine Orchestrierung.** Kein Lauf ruft ein Modell auf.
  Das ist ein eigenes Paket und setzt die Wahrmachung der Netzpolitik voraus.
- **Keine Änderung am Port**, an `structured_generation.py`, an `ModelProfile`
  oder an irgendeinem Vertrag.
- **Kein echter Netzaufruf in einem automatischen Test.**
- **Kein SDK, keine neue Abhängigkeit, keine Migration.**
- **Keine Behebung der Redirect-Lücke** — dieser Adapter folgt keinen Umleitungen
  und erbt sie nicht; sie bleibt als eigener offener Mangel stehen.
- **Keine Entscheidung über Rohtext-Aufbewahrung im Adapter** — die Politik kommt
  vom Aufrufer.

---

## Korrektur 2026-07-26 (aus dem Bau, gemeldet vom Erbauer)

Die Tabelle in Abschnitt „Die fünf Endzustände ehrlich abbilden" führte als
dritte Zeile: „Modell verweigert inhaltlich, ohne Filter → `refused`".

**Das war falsch.** Geminis Antwortform kann eine inhaltliche Weigerung
(gewöhnlicher Text, `finishReason: STOP`, keine Sicherheitsmarkierung)
**strukturell nicht** von einer gewöhnlichen Antwort unterscheiden — es gibt kein
Signal dafür. Sie zu erkennen verlangte eine semantische Beurteilung des
Ausgabetextes, also genau die Erfindung von Domänenverhalten, die AGENTS Regel 3
verbietet, und eine Modellaussage als autoritativ zu behandeln, was Regel 7
verbietet.

Die zutreffende Zeile lautet:

| Provider-Antwort | Zustand | warum |
|---|---|---|
| Kandidat vorhanden, nicht gefiltert, **ohne jeden Text** (oder `candidates` fehlt ohne `blockReason`) | `refused` | das ist der einzige Fall, den die Wire-Form als Weigerung erkennbar macht |

Die Ableitung hatte mehr versprochen, als die Schnittstelle hergibt; der Bau hat
es bemerkt und gemeldet. Nachgeprüft im Review
(`2026-07-26-e4-t08-review.md`) mit einer eigens gebauten Antwort dieser Form.

**Was daraus folgt, und was nicht:** Eine Weigerung, die als normaler Text
daherkommt, wird von diesem Adapter als `completed` verzeichnet. Das ist keine
Lücke, die sich hier schließen ließe — sie gehört auf die Ebene, die den Inhalt
beurteilt, und dort unter ausdrückliche Verifikation. Für ein Experiment über
Modellversagen ist die Unterscheidung **selbst ein Messgegenstand**, kein
Vorverarbeitungsschritt.
