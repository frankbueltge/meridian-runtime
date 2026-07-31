# MRR — Recovery, Integration und Produktrichtung (2026-07-31)

**Status:** Strategischer Review. **Kein Bau, kein Rückbau.** Gemessen am Repository,
nicht an seiner Dokumentation. Auftrag des Owners vom 2026-07-31.

**Maßstab der Prüfung:** Welche Form macht die bereits investierte Arbeit zu einem real
benutzbaren, weiterentwickelbaren System für empirische, agentisch unterstützte
Forschung?

---

## 1. Executive Diagnosis

Die Ausgangsdiagnose des Owners („kaum noch überschaubar, viele technisch hochwertige
Einzelleistungen, unklar wie daraus ein regelmäßig nutzbarer Forschungsprozess wird")
beschreibt ein berechtigtes Gefühl, benennt aber die falsche Ursache. Vier Korrekturen,
jede am Repository geprüft:

### Korrektur 1 — es ist keine Plattform

Es gibt **keinen Server, keine Queue, keinen Orchestrator**. `fastapi`, `temporalio` und
`boto3` stehen in `pyproject.toml` nicht als Abhängigkeiten, sondern als **verbotene
Module** in einem durchgesetzten Import-Linter-Vertrag. Die schwere Referenzarchitektur
der Spezifikation (§06 §1: Temporal, S3, FastAPI-Services, `workers/`, `infra/`) wurde
nie gebaut — bewusst.

Was existiert: 546 Python-Dateien, eine Kommandozeile mit elf Kommandos, sieben
Domänenpakete, zwei Service-Bäume, eine Postgres. Eine große, außergewöhnlich gut
getestete **Bibliothek mit CLI** — kein verteiltes System. Das senkt die Rückbaukosten
drastisch: es gibt keine Betriebslast, die stillgelegt werden müsste.

### Korrektur 2 — der Forschungsweg existiert und ist gefahren

`mrr synthesis run` mit vier JSON-Eingaben (QuestionModel, ConceptCharter,
MethodProtocol, Korpus) ist der Prozess. Er ist **zweimal an echten Korpora gelaufen**.
Beide Läufe liegen vollständig als SQL-Dumps im Repository
(`archive/dumps/`, 67 und 125 Objekte) und werden **bei jedem Push in CI gegen eine echte
PostgreSQL rekonstruiert** (`test_k1_t04_first_real_run.py`,
`test_k1_t04b_corroboration_floor_second_real_run.py`). Alle sechs Testebenen grün,
letzter Lauf 2026-07-26.

### Korrektur 3 — es gibt einen funktionierenden Operatorweg

Der README-Einstieg wurde nachgeprüft: `mrr audit anchoring` läuft in **1,6 Sekunden**,
offline, ohne Datenbank, ohne Schlüssel, über die Belegdaten beider Realläufe — und
trennt Integritätsverletzungen sauber von Abdeckungsbeobachtungen. Die Behauptung „in
zwei Minuten" stimmt.

### Verschärfung — fertige Dienste ohne Einfahrt

Das eigentliche Problem ist enger und gravierender als „unübersichtlich". Vollständige,
getestete Produktivkomponenten sind von **nichts außer Tests** erreichbar:

| Komponente | Zustand |
|---|---|
| `CorrectionImpactService` | 7 öffentliche Operationen, vollständig — **kein `mrr correction`** |
| `build_model_assisted_extraction_callable` | Produktivcode, getestet — nur von Tests aufgerufen |
| `GeminiModelAdapter` | Produktivcode, getestet — nur von Tests aufgerufen |
| `EnvelopeTransport` | Port mit **null Implementierungen** (eigener Docstring) |

### Der Mechanismus

`AGENTS.md` Regel 2 erlaubt ein approved Task-Packet zur Zeit, Regel 3 verbietet
Domänenverhalten, das nicht in der Spezifikation steht. **Kompositionsarbeit ist keins
von beidem** — sie konnte deshalb nie zu einem Paket werden.

Dieselbe Regel, die die hohe Einzelqualität erzwungen hat, hat die letzte Meile
strukturell ausgeschlossen. Das ist kein Disziplinproblem und wird durch mehr Disziplin
nicht besser.

### Die Hypothese des Owners — geprüft

> „Meridian bleibt die empirische Forschungspraxis. MRR wird ihre ausführende Runtime,
> zunächst über einen kleinen, opinionated und vollständig nutzbaren Forschungsweg
> operationalisiert."

**Im Kern richtig, und sie wertet nichts fälschlich ab** — mit einer Einschränkung: Der
„kleine vollständig nutzbare Forschungsweg" muss nicht erst geschaffen werden. Er
existiert. Was fehlt, ist nicht Produktisierung, sondern eine **zweite Frage**, die ihn
benutzt.

---

## 2. Current Capability Map

Nach der Taxonomie des Auftrags. „working" heißt: in dieser Session ausgeführt oder in
grünem CI gegen echte Postgres belegt.

### implemented and working

- `mrr audit anchoring | citations | support | artifacts` — offline, read-only,
  fail-closed Hash-Gate vor jeder Auswertung
- `mrr synthesis run` — der deterministische Forschungslauf, 2 Realläufe
- `mrr verification record` (K1-T05) — 6 VerificationResult-Objekte im Dump von Lauf 2
- `mrr export ro-crate` — der Export auf frankbueltge.de/on-record stammt daraus
  (`datePublished` 2026-07-22)
- `mrr release create | verify | supersede | status`, `mrr report render`
- `mrr federation envelope | outbox | inbox` — realer Hammond-Dissens vollständig
  durchgelaufen, von unveränderten Empfängerfunktionen angenommen
- `mrr practice init` — `practices/meridian.json`, „The Field", gültig bis 2027-07-26
- `mrr validate agreement` (N1-T01), `mrr observe field` (R2-T01)
- Testbestand: 2791 Testfunktionen über 5 Ebenen; Unit + Property lokal grün (exit 0)

### implemented but not integrated

- **Korrektur-Lebenszyklus** — siehe Tabelle oben. `CorrectionNotification` ist laut
  Wegkarte der **einzige real vorkommende `payload_kind`**; die Föderation kann heute
  genau einen Payload-Typ tragen, und der ist nur von innerhalb eines Services erzeugbar.
- **Modellarm** — Adapter und Extraktionsschritt existieren, sind nie verbunden
- **Föderation als Austausch** — Weg belegt, Identität vorhanden, es fehlt die Gegenseite
  (Ulysses' Node-ID + Vertrauenserklärung)

### implemented only for a specific run

- `corpora/model-collapse/` — Frage, Charter, Protokoll, 18 Korpuseinträge, Atlas-Snapshots
- `archive/dumps/*.sql` — der vollständige DB-Zustand beider Läufe
- `corpora/e2e-survey/`, `corpora/archive-integrity/`, `corpora/research-records/`

### partially implemented

- **Belegbytes beider Realläufe nicht einlösbar.** 51 Anker tragen
  `anchor_validation_status: "validated"`, und dieser Status ist **nicht falsifizierbar**
  — die gehashten Snapshot-Bytes sind nicht findbar. Vorwärts behoben (A1/A2), für diese
  zwei Läufe unrettbar. Anker-*Integrität* ist intakt, Anker-*Einlösbarkeit* nicht.
- `mrr audit support` erreicht nur Abstract-Ebene, ~28 % Abdeckung
- Adversarial-Testebene als leer deklariert (`tests/EMPTY_TIERS.txt`) — ehrlich markiert

### specification only

- Temporal, S3/Objektspeicher, FastAPI-Control-Plane — nie gebaut und per Import-Vertrag
  **verboten**. Bewusste Abweichung, keine Lücke.
- LLM-orchestrierter Arm (Sprosse 3 der Leiter) — kein Code, kein Paket
- Goldstandard-Pakete N1-T02/T03 — existieren nicht
- E7 (qualitativ/Feldforschung), E9 (Härtung/Betrieb) — weitgehend Spezifikation

### obsolete or superseded

- README „Current limitations", Punkte 2 und 3 („kein konkreter Modell-Adapter", „keine
  deklarierte Praxis-Identität") — **beide seit 2026-07-26 falsch**
- Site-Methodenblatt „two claims from a single run" — es gibt zwei Läufe
- Monorepo-Layout aus §06 §1 (`workers/`, `infra/`) — nie angelegt
- Offene Fragen 1 und 3 des Site-Kopplungs-Memos vom 2026-07-21 — beantwortet

### unclear

- 42 Branches, deren Enthaltensein Git wegen Squash-Merges nicht belegen kann
- 9 Archivsignaturen: gültig, aber **nicht zurechenbar** (`node-key-1`/`origin-key-1`
  statt abgeleitetem `kid`) — offener, datierter Defekt aus dem Handoff vom 2026-07-27
- Ob Lauf 2 öffentlich werden soll
- `../meridian-runtime-e8t05-capture/` — leeres Verzeichnis, Rest, löschbar

---

## 3. Actual Operator Journey

Ohne Architekturvokabular. Bruchstellen markiert.

| Schritt | Was heute passiert | Bruch |
|---|---|---|
| Projekt anlegen | Es gibt kein Projekt-Objekt. Vier JSON-Dateien schreiben. | **Kein Scaffold.** Kein `mrr project init`, keine Vorprüfung. Vorlagen nur als `examples/`. |
| Quellen einbringen | Korpus von Hand kuratieren. Jeder Eintrag trägt seine Einordnung (`supports`/`contradicts`) bereits. | **Hier liegt die Forschung.** Die deutende Arbeit macht ein Mensch, bevor MRR etwas sieht. |
| Lauf starten | Ein Kommando: `mrr synthesis run`. | Postgres und Artefaktverzeichnis von Hand. Kein `make dev-up`. |
| Outputs untersuchen | `report render`, `export ro-crate`, vier `audit`-Kommandos | kein Bruch |
| Claims formulieren | Man formuliert keine — der Lauf leitet sie aus der Belegmatrix ab, gedeckelt. | gewollt: man *kann* keine Aussage treffen, die der Korpus nicht trägt |
| Unabhängig prüfen | `mrr verification record` nimmt ein fertiges Urteil auf. | Die Prüfung selbst führt MRR nicht aus. Bei K1-T06 war der blinde Prüfer eine Agenten-Session. |
| Widersprüche behandeln | Dissens bleibt per Invariante stehen (MRR-FR-077). | **Kein `mrr correction`.** |
| Release erzeugen | `mrr release create | verify | supersede | status` | kein Bruch |

**Ehrliche Zusammenfassung:** Der Weg ist durchgängig begehbar. Sechs der acht Schritte
haben keinen oder einen kleinen Bruch. Der eine Bruch, der zählt, ist Schritt 2 — und er
ist kein Defekt, sondern die derzeitige Wesensbestimmung: *MRR ist heute ein
Beweisführungs- und Verwaltungsapparat für Forschung, die woanders gedacht wird.*

---

## 4. Gap Analysis

Leitfrage: Wie weit ist MRR davon entfernt, Meridian bei einem realen empirischen
Projekt von der Frage bis zu einem geprüften, begrenzten Ergebnis zu unterstützen?

**Antwort: Es hat das zweimal getan.** Frage, gepinnter Korpus, deterministischer Lauf,
gedeckelte Claims, zwei unabhängige Prüfungen, bewahrter Dissens, portabler Export,
öffentliche Projektion. Die Entfernung ist **nicht technisch**. Sie besteht darin, dass
die Frage von der Engineering-Linie kam und nichts eine dritte angetrieben hat.

| Kategorie | Konkret |
|---|---|
| Fehlende technische Fähigkeit | LLM-orchestrierter Arm. Belegbyte-Einlösbarkeit der Altläufe (unrettbar). Messbarer Klassifikationsvergleich. Sonst: wenig. |
| **Vorhanden, nicht komponiert** | Korrektur-Service ohne CLI. Modellarm ohne Naht. `EnvelopeTransport` ohne Implementierung. Praxis-Identität ohne Gegenüber. |
| Fehlendes Interface / Operatorwissen | Kein Projekt-Scaffold, kein `dev-up`, README an zwei Stellen veraltet. |
| **Fehlendes Forschungsprojekt** | Der eigentliche Engpass. Es gibt keine zweite Frage. |
| Fehlende organisatorische Entscheidung | Welche Frage als nächste und von wem. Ob Lauf 2 publiziert wird. Ulysses' Node-ID. |
| Vorzeitig generalisierte Architektur | 31 Service-Module für eine Frage. E5 (14 Pakete) gebaut, bevor ein Austauschpartner existierte. E6 (6) ohne Verkehr. *Nicht falsch — vor dem Bedarf gebaut.* |

---

## 5. Strategic Options

|  | A — eigenständige Runtime | B — Kernel unter Meridian | C — Hybrid, ein Weg produktisiert | D — zweite Frage statt zweite Schicht |
|---|---|---|---|---|
| Nutzen | Vollständigkeit gegenüber der Spec | Klare Rolle, kleine Oberfläche | Bestand bleibt, ein Weg wird benutzbar | Jede weitere Zeile hat einen belegten Anlass |
| Nötige Arbeit | E7, E9, LLM-Arm, Betrieb, Härtung | Einfrieren von E5/E6/E8 + Doku umschreiben | Verdrahtung + Scaffold + Doku | Verdrahtung + eine Frage |
| Risiken | Der Flur wird länger | **Verlust einer belegten Fähigkeit** — die Föderation läuft schon | „Produktisierung" wird selbst ein Bauprogramm | Kommt keine Frage, steht alles still — sichtbar |
| Komplexität | hoch | mittel | mittel | niedrig |
| Meridian Classic (`field-research`) | unberührt, Distanz wächst | wird Werkzeug, weiß aber nichts davon | Kopplung erst mit der ersten Praxis-Frage | **die zweite Frage kommt aus Classic** |
| Site / On Record | bleibt Standbild | bleibt Standbild | zweiter Export möglich | Autorschaftsproblem löst sich mit |
| Einfrieren | nichts | E5, E6, E8 | neue Epics | **alle Capability-Epics**, nichts archivieren |

### Empfehlung: C, verschärft durch D

Die Architektur bleibt **vollständig erhalten** — auch die Föderation, denn sie ist keine
Spekulation, sie ist gelaufen. Aber die Produktisierung wird **kein Bauprogramm**: Das
nächste Paket ist eine Verdrahtung, das danach eine Forschungsfrage. Neue Fähigkeiten
entstehen nur noch, wenn eine reale Frage über sie stolpert.

Gegen B spricht ein handfestes Argument: Die Föderation ist die einzige Fähigkeit des
Systems, für die es in der Ökologie einen **konkreten Adressaten** gibt. Sie einzufrieren
hieße, das eine Stück wegzulegen, das den Anschluss an die Praxen herstellt.

---

## 6. Dokument- und Steuerungsrollen

| Dokument | Rolle heute | Vorgeschlagene Rolle |
|---|---|---|
| `AGENTS.md` | Normativ — Regel 2 + 3 schließen Kompositionsarbeit strukturell aus | **Normative Source of Truth, erweitert um den Integrations-Pakettyp** (Abnahme = Operatorweg, nicht Modul) |
| `docs/spec/` | Wird normativ gelesen, obwohl ihre Referenzarchitektur verboten ist | **Historische Spezifikation** — ausdrücklich kennzeichnen |
| `06_IMPLEMENTATION_PLAN.md` | Backlog, 80 Pakete, alle „approved" | **Historisches Backlog.** §8 „what not to build early" als Mahnung erhalten, nicht als Fahrplan |
| `2026-07-24-capability-roadmap-entwurf.md` | Entwurf zur Owner-Review | **Aktive Product Direction** — sie sagt schon „Stufe 2 erst nach realer Stufe-1-Nutzung" |
| `README.md` | Mischt Einstieg, Grenzen, Positionierung; zwei Grenzen veraltet | **Operator-Einstieg**, Grenzen korrigiert |
| Task-Packets + Ableitungen | Implementierungsbacklog, sehr hohe Einzelqualität | **Implementierungsbacklog** — unverändert, plus Integrationstyp |
| Handoffs, Fact-Locks, Wegkarten | Faktisch das beste Steuerungsmaterial im Repo | **Forschungs- und Arbeitsprotokoll**, formell als solches führen |

---

## 7. Integrationsprogramm — drei vertikale Scheiben

Zum Vorschlag des Owners („ein frischer Checkout rekonstruiert einen Reallauf") ein
**Widerspruch**: Das tut CI bereits bei jedem Push, gegen echte Postgres, einschließlich
deterministischem Replay und Release-Verifikation. Als erste Scheibe wäre es die
Bestätigung des schon Bestätigten. Der unabgedeckte Rest — der Weg auf einer
*menschlichen* Maschine ohne CI-Postgres — ist ein Nebenprodukt von Scheibe 1.

### Scheibe 1 — die Korrektur wird bedienbar, und damit die Föderation

Der vollständige `CorrectionImpactService` bekommt seine Kommandozeile, und der eine
fehlende konkrete `EnvelopeTransport` (offline, bündel-schreibend) wird geschlossen.

`CorrectionNotification` ist der einzige Payload-Typ, den die Föderation tragen kann —
ein Korrektur-Kommando ist damit gleichzeitig die fehlende Einfahrt zu dem einen
Transportweg, der schon funktioniert. Verbindet Korrektur, Föderation und
Praxis-Identität in *einem* Operatorweg.

**Abnahme:** Eine Korrektur an einem publizierten Claim wird per Kommando aufgenommen,
Abhängige markiert, eine signierte Benachrichtigung liegt versandfertig als
Offline-Bündel — ohne einen einzigen direkten Service-Aufruf.

→ Paket: `task-packets/I1-T01.yaml`, Ableitung:
`2026-07-31-ableitung-korrektur-cli.md`

### Scheibe 2 — der erste Austausch verlässt das Haus

Das Bündel erreicht Ulysses und wird angenommen. Meridians Zug ist einseitig
vollziehbar; Ulysses' Schlüssel wird erst für die Antwort gebraucht.

**Abnahme:** Ulysses' eigene Session bestätigt Signatur und Hash und verzeichnet den
Empfang.

### Scheibe 3 — der Modellarm, mit seiner Spezifikationsfrage

Adapter und Extraktionsschritt werden komponiert. Vorher muss die Frage aus Befund 2 der
Ableitung fallen: Darf ein Modellvorschlag die kuratierte Begründung überschreiben und
`verified` heißen?

**Abnahme:** Ein Lauf mit modellvorgeschlagenen Prosafeldern, Modell-, Prompt- und
Antwort-Hash im Archiv — und einer ausdrücklichen Entscheidung im Paket.

### Reihenfolge

Scheibe 1 macht das System bedienbar, Scheibe 2 föderiert, Scheibe 3 agentisch. Nach der
Fact-Lock-Korrektur ist die naheliegende Reihenfolge genau umgekehrt richtig — der
Modellarm war die attraktivste, aber nicht die tragfähigste erste Scheibe.

**Eine zweite Forschungsfrage von Meridian** bleibt der eigentliche Engpass (§4). Sie ist
kein Bau-Slice, sondern eine Frage an die Praxis, und wird von keiner Scheibe blockiert.

---

## 8. Entscheidungen, die nur der Owner treffen kann

1. **Freigabe von I1-T01** — es fügt einen Adapter für einen deklarierten Port hinzu
   (`EnvelopeTransport`), strukturell wie E4-T08. Das ist mehr als reine Verdrahtung und
   wird deshalb nicht stillschweigend vollzogen.
2. **Darf ein Modellvorschlag die kuratierte Begründung überschreiben und `verified`
   heißen?** Die Grundsatzfrage „Modellschritt ja/nein" ist bereits entschieden und
   signiert in `practices/meridian.json`. Offen ist nur diese engere Frage. Sie blockiert
   Scheibe 3, nicht Scheibe 1.
3. **Woher kommt die zweite Frage** — aus Meridians Session oder von der
   Engineering-Linie? Kommt sie aus der Praxis, löst das das Autorschaftsproblem der Site
   mit.
4. **Wird Lauf 2 veröffentlicht?** Die Site zeigt Lauf 1 und sagt „a single run". Stehen
   lassen geht nicht.
5. **Bekommt `AGENTS.md` den Integrations-Pakettyp?** Ohne diese Änderung entsteht die
   nächste Kompositionslücke genauso wie die vier bisherigen. Die einzige strukturelle
   Korrektur, die dieser Review vorschlägt.

---

*Grundlage: `meridian-runtime` @ `c447b40`, lokaler Checkout. CI grün über sechs
Testebenen. Unit- und Property-Ebene in der Review-Session lokal ausgeführt (exit 0).
`mrr audit anchoring` selbst ausgeführt. Beide Lauf-Dumps und der Site-Export gelesen.
Kein Code geändert, kein Schema angelegt.*
