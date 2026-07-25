# N2-T03-Ableitung: Support-Prüfung — trägt die Quelle die Behauptung? (2026-07-25)

**Status:** Ableitung, Owner-Vorlage. N2-T03 steht seit
`docs/design/2026-07-25-n2-t02-derivation.md` als benannter, nicht abgeleiteter
Slot („Support-Prüfung — das schwere, LLM/menschliche Stück"). Dieses Dokument
fixiert den Fact-Lock und korrigiert die Aufgabenstellung an der Realität. Es ist
der Governance-Commit vor dem Bau; der Merge nach `main` bleibt an eine
ausdrückliche Owner-Freigabe gebunden.

**Die Aufgabe war in drei Punkten ungenau. Die dritte Korrektur ist die
wertvollste: N2-T03 wird modellfrei gebaut — und die Begründung dafür steht in
den Records, die geprüft werden sollen.**

---

## Korrektur 1: „Das erste Paket mit einem Modell-Schritt" — es gibt keine Modell-Kante

Die Aufgabe war so gestellt: N2-T03 sei „das erste Paket mit einem Modell-Schritt,
und damit das erste, hinter dem R2-T01s fail-closed-Gate wirklich etwas schützt".
Der Fact-Lock am Code zeigt: **ein Modell-Schritt ist heute überhaupt nicht
aufrufbar.**

- `packages/domain/mrr/domain/model_adapter.py` (E4-T01) ist ein `Protocol` mit —
  wörtlich — „**NO concrete implementation** exists in this module or anywhere
  under `packages/`/`adapters/` yet".
- `adapters/llm/mrr/adapters/llm/structured_generation.py` (E4-T02) ist der
  einzige Bewohner von `adapters/llm/` und ist ausdrücklich **provider-frei**:
  „It opens no network connection and calls no model-provider SDK anywhere — the
  ONLY way it ever reaches a model is through the caller-injected
  `ModelAdapter.invoke` method." Maschinell erzwungen über import-linter-Vertrag 1.
- Kein Provider-SDK im Dependency-Baum, kein HTTP-Client (`httpx`/`requests`
  fehlen, bereits an N2-T02a erstverifiziert).

**Das ist exakt dasselbe Muster wie bei E5/E6** — und damit der zweite Befund
derselben Form innerhalb eines Tages: vollständige Port-Schicht, null konkrete
Adapter, keine Außenkante. E5-T08 hat diese Kante für die Föderation gebaut. Für
das Modell ist sie **nicht gebaut und nicht harmlos zu bauen**: ein konkreter
`ModelAdapter` heißt Provider-SDK, Netz-Egress aus der Laufzeit und **ein Secret**
— AGENTS Regel 11 („no … unrestricted network egress, or secrets in prompts") und
eine Owner-Entscheidung zur Schlüsselverwahrung. Das ist **dieselbe
Entscheidungsklasse wie E5-T09** (siehe „Zusammengeführte Owner-Entscheidung").

Zum Gate-Teil der Annahme, ebenfalls korrigiert: R2-T01s Gate schützt heute
bereits etwas — `corpora/e2e-survey/observation-batch.v1.json` verankert die
N2-T01-Fixtures. Es schützt aber **nicht** die N2-T02a-Fixtures: für
`corpora/research-records/` existiert **kein** Observation-Batch-Descriptor. Und
ein dritter Eingang (Quellinhalte) lässt sich dort nicht anhängen, weil
`BatchRole` ein **geschlossener Zwei-Satz** `{"manifest","snapshot"}` ist
(„this batch shape has no third input, and no caller of this module may invent
one"). Ihn aufzuweiten wäre genau der verbotene Griff, den N2-T02b schon einmal
verweigert hat. N2-T03 spiegelt das Muster darum mit eigenem Gate, wie T02b es
mit den Dumps getan hat.

## Korrektur 2: Das Support-Substrat existiert nicht — und Abstracts allein tragen es nicht

Eine Support-Prüfung braucht Quellinhalt. **Im Repository liegt keiner.** Der
T02a-Snapshot trägt je Zitat genau vier Felder — `resolved`, `resolved_title`,
`resolved_detail` (Erstautor), `resolver`. Kein Abstract, kein Volltext. Das
T02a-Skript parst das arXiv-Atom, greift aber `summary` nie ab.

An der Ableitung erstverifiziert (keyless, https, `export.arxiv.org` +
`api.crossref.org` — dieselbe Allowlist wie T02a):

- **Substrat ist beschaffbar, 21/21.** Alle 20 arXiv-Abstracts kommen in **einem**
  Batch-Query; die Nature-DOI trägt bei Crossref einen JATS-Abstract
  (`<jats:title>Abstract</jats:title>…`, Feld `abstract` vorhanden). Für die 21.
  Quelle ist also ein deterministisches JATS-Tag-Stripping nötig — benannt, nicht
  stillschweigend.
- **Aber Abstracts decken die Behauptungen nur zu einem Bruchteil.** Heuristisch
  auf Satz-Ebene zugeordnet (Zahlen-Token je Zitat-Satz gegen den Abstract der
  zitierten Quelle): **18 von 64 Zahlen** treffen, **28,1 %**. Bei den wörtlichen
  Zitaten noch dünner: von 37 zitierten Spannen über die drei Records sind 11
  englische Quellenzitat-Kandidaten (≥ 4 Wörter) — **1 von 11** steht in
  irgendeinem Abstract.
- Die Fehlstellen sind nicht Randfälle, sondern die **Trag-Zahlen der Roadmap**:
  Kosmos' Gradient 85,5 / 82,1 / **57,9 %** fehlt im Kosmos-Abstract (79,4 %
  steht drin); DeepTRACEs Detailzahlen 0/4; Pangakis 0/6; Silicon Sampling 0/6;
  Barrie 0/1. Die 57,9 % sind die Zahl, auf der die gesamte Stufung der
  Capability-Roadmap ruht.

**Konsequenz:** eine Support-Prüfung, die den Abstract als „die Quelle" behandelt,
würde für die klare Mehrheit `unverifiable` melden und dabei so aussehen, als
hätte sie geprüft. Das ist wörtlich die vorgetäuschte Prüfung, die N2-T02b
ausdrücklich verweigert hat („würde eine Prüfung vortäuschen, die nicht
stattfindet"). Der geprüfte Ausschnitt muss darum **im Report benannt** werden,
und Abwesenheit darin darf **nie** als Widerlegung gelten (siehe die
Ehrlichkeits-Grenze unten).

> Die Zahl 28,1 % ist eine **an der Ableitung heuristisch** ermittelte
> Größenordnung, **kein Akzeptanz-Orakel** — die Zuordnung Zahl → Behauptung
> geschah per Satz-Heuristik, nicht per Hand. Das Orakel entsteht erst aus dem
> handtranskribierten Claim-Manifest (unten), wie schon bei T02a das
> Zitat-Manifest.

## Korrektur 3: Auf der prüfbaren Ebene braucht die Support-Prüfung kein Modell — und auf der Modell-Ebene trägt das Modell nicht

Das ist der eigentliche Befund dieser Ableitung, und er ist reflexiv.

Zerlegt man „trägt die Quelle die Behauptung?" nach Prüfbarkeit, entstehen zwei
Klassen:

1. **Mechanisch entscheidbar** — eine Zahl steht im Quelltext oder nicht; ein
   wörtliches Zitat steht dort buchstabengetreu oder nicht; die Quelle nennt für
   dieselbe Größe einen **anderen** Wert. Das ist Substring- und Token-Vergleich.
   **Kein Modell nötig.**
2. **Paraphrase-Ebene** — trägt der Sinn des Absatzes die Behauptung? Dafür
   bräuchte es ein Urteil, also ein Modell oder einen Menschen.

Für Klasse 2 sagt die Evidenz **der zu prüfenden Records selbst**:

- LLM-Judges bleiben „selbst mit szenariospezifischen Checklisten plus
  vollständigem execution trace **unter 85 % Genauigkeit**" (Record I §3,
  arXiv 2605.10246 Sec. 6 — das Negativresultat der Benchmark-Autoren);
- „fabrizierte Reports sind oberflächlich plausibel und intern konsistent"
  (ebenda) — also genau in der Zone, in der ein Judge nichts merkt;
- Roadmap-Empfehlung, bereits abgenommen: LLM **„nie als alleiniger Judge"**
  (κ 0,19–0,51).

**Also: N2-T03 wird modellfrei gebaut, und der Modell-Schritt wird benannt und
abgelehnt — mit der Begründung aus dem Material, das geprüft wird.** Das ist
keine Sparmaßnahme, sondern die Anwendung des eigenen Befunds auf die eigene
Bauentscheidung. Die Aufgabe hatte N2-T03 als „das schwere, LLM/menschliche
Stück" geführt; der Fact-Lock sagt, dass der maschinell verantwortbare Teil
davon **leichter und härter zugleich** ist als angenommen.

---

## Der Befund, den die Prüfung schon an der Ableitung produziert hat

Die Support-Prüfung hat vor ihrem Bau bereits einen realen Fehler gefunden — in
den eigenen Dokumenten, an der Zahl, mit der N2 überhaupt begründet wird.

**Primärstelle, korrekt:** Record I:56 —
„Zitations**genauigkeit** von Deep-Research-Systemen **40–80 %**" (DeepTRACE,
arXiv 2509.04499).

**An der Quelle erstverifiziert:** der DeepTRACE-Abstract sagt wörtlich
„…with **citation accuracy** ranging from **40--80%** across systems".
Record I ist also richtig — die 40–80 % sind der Anteil **korrekter** Zitate.

**Fünf nachgelagerte Stellen kehren die Zahl um** und machen aus der Genauigkeit
eine Lücke bzw. Fabrikationsrate:

| Stelle | Wortlaut |
|---|---|
| `docs/design/2026-07-23-recherche-e2e-research-automation.md:134` | „die dokumentierte 40–80-%-**Lücke**" |
| `docs/design/2026-07-24-capability-roadmap-entwurf.md:50` | „die dokumentierte 40–80-%-**Zitationslücke** des Feldes" |
| `docs/design/2026-07-24-n2-derivation.md:34` | „dokumentierte 40–80-%-**Feldlücke**" |
| `docs/design/2026-07-25-n2-t02-derivation.md:57` | „die dokumentierte 40–80-%-**Fabrikationslücke**" |
| `corpora/e2e-survey/citations.manifest.json:5` | „a documented 40-80% field **gap**" |

Ist die Genauigkeit 40–80 %, ist die Lücke **20–60 %**. Beides kann nicht von
derselben Zahl gelten. Die letzte Zeile wiegt am schwersten: sie steht in einer
**committeten Korpus-Fixture**, deren sha256 im R2-T01-Descriptor als
Integritäts-Anker gepinnt ist — eine Korrektur dort ist kein Textedit, sondern
berührt den Anker und ist eine Owner-Entscheidung (unten).

Das ist die reflexive Bewegung eine Ebene weiter: N2-T01 prüfte die öffentliche
Survey, N2-T02 die Zitate der Records — **N2-T03 findet einen Fehler in der
Argumentationskette, mit der N2 selbst begründet wurde.** Genau der Fehlertyp,
den die Records dem Feld attestieren, in der eigenen Akte.

Kein Befund über böse Absicht: die Zahl wurde in der Primärstelle korrekt
erfasst und ist erst in der Weitergabe gekippt. Das ist die gewöhnlichste
Fehlerform überhaupt — und der Grund, warum eine mechanische Konsistenzprüfung
Wert hat.

---

## Die zentrale Ehrlichkeits-Unterscheidung: Abwesenheit im geprüften Ausschnitt ist keine Widerlegung

Das N2-T03-Analogon zu N1s „Reliabilität ≠ Validität", N2-T01s „Existenz ≠
Bestätigung", R2-T01s „Beobachtung ≠ Optimierung", N2-T02bs „Verankerung ≠
Belegkraft" und E5-T08s „Transport ≠ Vertrauen".

Ein Treffer beweist, dass die Behauptung **im geprüften Ausschnitt** steht. Ein
Nicht-Treffer beweist **nichts** — er sagt, dass der Ausschnitt sie nicht deckt.
Bei Abstracts ist das der Normalfall, nicht die Ausnahme (28,1 % Deckung).

Daraus folgen zwei geschlossene Status-Sätze, die der Report nie kollabieren darf
(AGENTS-Verbot gegen zusammengefasste Statuswerte):

**Zahlen**
- `figure_supported` — der Wert steht im geprüften Ausschnitt. *Treffer.*
- `figure_contradicted` — der Ausschnitt nennt für die **deklarierte Größe** einen
  anderen Wert. **VERLETZUNG.**
- `figure_absent_from_checked_excerpt` — der Ausschnitt sagt dazu nichts.
  **BEOBACHTUNG, keine Verletzung.**

**Wörtliche Zitate**
- `quotation_verbatim` — buchstabengetreu im Ausschnitt. *Treffer.*
- `quotation_altered` — der Ausschnitt enthält die Stelle in **abweichendem
  Wortlaut**. **VERLETZUNG.**
- `quotation_absent_from_checked_excerpt` — nicht im Ausschnitt.
  **BEOBACHTUNG, keine Verletzung.**

Würde man Abwesenheit in den Verletzungs-Topf werfen, meldete der erste Lauf
sofort **rund 46 falsche Zahlen-Verletzungen und 10 falsche Zitat-Verletzungen**
— dieselbe Falle, die T02b mit `source_unanchored` schon einmal umgangen hat, nur
diesmal mit einem Faktor, der die Prüfung wertlos machen würde.

Zusätzlich bindend: **der Report benennt den geprüften Ausschnitt** („abstract",
nicht „das Papier") und die Quelle des Ausschnitts (arXiv-Atom `summary` bzw.
Crossref `abstract`, gehasht). Ein Report, der „geprüft" sagt, ohne zu sagen
*wogegen*, wäre selbst eine Überbehauptung.

---

## Die Zerlegung

Geschnitten wird an derselben Linie wie bei N2-T02 — **wer das Netz berührt und
wer nicht**:

- **N2-T03a — berührt das Netz.** `scripts/fetch_source_content.py` (neu, gated,
  außerhalb der Laufzeit) erzeugt
  `corpora/research-records/verification/content-snapshot.json`: je Zitat den
  Abstract-Text, dessen sha256 und die Herkunft des Ausschnitts. Ändert **keine
  Zeile** an `scripts/fetch_citation_resolutions.py` und **kein Byte** an
  `resolution-snapshot.json` — das T02a-Artefakt bleibt bitgleich (Archiv
  unantastbar). Dieselbe Egress-Rahmung wie T02a, wörtlich übernommen: https,
  harte Zwei-Host-Allowlist, keyless, run-once-commit, nichts wird interpretiert.
- **N2-T03b — berührt das Netz nicht.** Handtranskribiertes
  `corpora/research-records/claims.manifest.json` (je Eintrag: Behauptungstyp
  `figure`|`quotation`, der behauptete Wert bzw. Wortlaut, die **deklarierte
  Größe**, `citation_id`, Datei+Zeile), der deterministische Evaluator und
  `mrr audit support` — hinter einem eigenen fail-closed Hash-Gate über
  Claim-Manifest + Content-Snapshot (Muster von T02b gespiegelt, `BatchRole`
  **nicht** aufgeweitet).
- **N2-T03c — benannt, blockiert.** Der Modell-Schritt für die Paraphrase-Ebene.
  Blockiert nicht durch einen fehlenden Anlass, sondern durch die fehlende
  Modell-Außenkante (konkreter `ModelAdapter` = Provider, Egress, Secret) **und**
  durch die eigene Evidenz gegen den alleinigen Judge. Kein Bau-Paket, solange
  beides offen ist.
- **N2-T04 — benannt.** Interne Konsistenz derselben Zahl über alle committeten
  Dokumente (die Klasse, in der die 40–80-Inversion lebt). Braucht **keinen
  Quellinhalt** und **kein Modell**. Ihr erster Befund liegt bereits vor — von
  Hand, oben.

**Zuschnitt des ersten Baus (use-first):** Das Claim-Manifest wird zunächst für
**Record I** transkribiert (13 der 21 Zitate; die Quelle der Roadmap-Stufung und
der Ort, an dem die 40–80-Inversion entsteht). Records II/III folgen als
**N2-T03d**, sobald die Form an realem Material getragen hat. Der
Content-Snapshot deckt dagegen sofort alle 21 Quellen ab — ein Batch-Query kostet
nicht mehr als ein Teil-Query, und ein halber Snapshot wäre eine Fußangel für
den Folgeschritt.

---

## Architektur-Platzierung

- `scripts/fetch_source_content.py` — gated, außerhalb der Laufzeit, kein
  Laufzeit-Pfad importiert es (Spiegel von T02a).
- `packages/domain/mrr/domain/support_audit.py` — reine, no-IO-Funktionen: die
  beiden geschlossenen Status-Sätze, der Zahlen-Vergleich, der
  Wortlaut-Vergleich. Nimmt bereits gelesene Werte (T02b-Präzedenz).
- `packages/domain/mrr/domain/support_audit_report.py` — Pydantic-v2-Projektion
  (`MRRModel`, `extra="forbid"`) mit fixem Ehrlichkeits-Header, der den geprüften
  Ausschnitt benennt. **Kein** persistiertes Objekt, **kein** `schemas/*`-Spiegel
  (Regel 7).
- `services/control_plane/mrr/services/support_audit/service.py` — read-only,
  **keine Netz- und keine DB-Verbindung**: liest den Descriptor, hasht
  Claim-Manifest und Content-Snapshot, **fail-closed vor jeder Auswertung**,
  wertet dann aus.
- `services/control_plane/mrr/services/cli/support_audit_main.py` + 2 Zeilen in
  `cli/main.py` → `mrr audit support`.

**Bewusst NICHT wiederverwendet:** R2-T01s `check_anchor` (geschlossener
`BatchRole`-Zwei-Satz, siehe Korrektur 1). Der Zahlen-Vergleich normalisiert
deutsche Dezimalkommata auf Punkte und vergleicht **exakt**, nie „ungefähr" — ein
Toleranzband wäre eine erfundene Domänenregel (AGENTS Regel 3).

---

## Akzeptanz-Orakel (VOR dem Bau festzulegen)

Das Orakel kann erst beziffert werden, **nachdem** das Claim-Manifest von Hand
transkribiert ist — genau wie bei T02a, wo erst das Zitat-Manifest die Prüfmenge
definierte. Der Bau beginnt darum nicht vor diesem Schritt. Festgelegt ist
bereits, **welcher Art** das Orakel ist:

1. Jede Manifest-Zeile bekommt genau einen Status aus dem für sie geltenden
   geschlossenen Satz — an der Ableitung von Hand bestimmt und im Paket
   festgeschrieben; der Bau muss diese Zahlen exakt reproduzieren.
2. Die 40–80-Zeile von Record I:56 muss `figure_supported` ergeben (der Abstract
   trägt sie wörtlich) — das ist der scharfe Einzelfall, der beweist, dass die
   Prüfung nicht pauschal „absent" sagt.
3. Kein Lauf darf eine `*_absent_from_checked_excerpt`-Zeile als Verletzung
   zählen; Verletzungs- und Beobachtungssummen erscheinen getrennt im Report.
4. Nicht-Null-Exit **nur** bei Eingabe-/Verweigerungsfehlern (Semantik von N2-T01
   gespiegelt: 0 Erfolg / 2 Eingabe / 3 Verweigerung). Gefundene Verletzungen
   ändern den Exit-Code **nicht** — das Audit berichtet, es urteilt nicht über
   den Lauf.

---

## Ausdrücklich NICHT in N2-T03a/b

Kein LLM, kein Modell-Schritt, kein Provider-Adapter, kein neuer Dependency, kein
Netzzugriff aus der Laufzeit, keine DB, kein persistiertes Objekt, kein
`schemas/**`, keine Migration. **Keine Volltext-Beschaffung** (PDF-Download,
Scraping, `arxiv.org/pdf/…`) — der Ausschnitt bleibt der Abstract, und dass er
schmal ist, wird berichtet statt umgangen. **Keine Aufweitung von R2-T01s
`BatchRole`.** **Keine Änderung** an `resolution-snapshot.json`,
`citations.manifest.json` oder an N1s/N2-T01s/N2-T02s/R2-T01s Interna. **Keine
Korrektur der 40–80-Stellen** — das ist eine Owner-Entscheidung, weil eine davon
in einer gehashten Korpus-Fixture steht.

---

## Zusammengeführte Owner-Entscheidung: wo lebt privates Schlüsselmaterial?

Zwei Stränge, die heute unabhängig voneinander gestoppt sind, brauchen dieselbe
Entscheidung — sie sollte einmal getroffen werden, nicht zweimal:

- **E5-T09** (Föderation): Meridian braucht ein eigenes Schlüsselpaar; die
  öffentliche Hälfte kommt ins Repo, die private **darf nicht** ins Repo.
- **N2-T03c** (Modell-Schritt): ein konkreter `ModelAdapter` braucht einen
  Provider-Key — ebenfalls nie im Repo, und nach AGENTS Regel 11 nie in einem
  Prompt oder Vermerk.

Beide Male lautet die Frage: **lokale Datei außerhalb des Repos, Passwort-Manager
oder GitHub-Secret?** Das ist Franks Infrastruktur-Entscheidung; die Session rät
nicht. (Für E5-T09 gilt zusätzlich unverändert: Ulysses erzeugt seinen Schlüssel
in **eigener** Session — Meridian erzeugt niemals einen Schlüssel für eine fremde
Praxis.)

## Weitere offene Owner-Entscheidungen

Unverändert und **nicht** durch diese Ableitung berührt: K2-Tor-Wiedervorlage,
erstes A4-Release, Befund 1 (Artefakt-Blob-Dauerhaftigkeit), N1-T02/T03, N3,
R2-T02 (Fetch + Vorschlags-Emitter), R2-T03 (GEPA-Schleife, substrat-gated),
R1-T01 (erste Joint Inquiry, setzt E5-T09 voraus), sowie der `scripts/`-Blindfleck
von `make security-check`.

**Neu zur Entscheidung vorgelegt:**

1. Die **40–80-Korrektur** in fünf Dokumenten — darunter
   `corpora/e2e-survey/citations.manifest.json`, deren sha256 in
   `corpora/e2e-survey/observation-batch.v1.json` als Anker steht. Eine Korrektur
   dort erzwingt eine Neuberechnung des Ankers; wird sie ausgelassen, bleibt eine
   falsche Zahlenzuschreibung in einer gehashten Fixture stehen. Beides ist eine
   Owner-Entscheidung, keine Session-Entscheidung.
2. Ob N2-T03 im hier abgeleiteten, **modellfreien** Zuschnitt gebaut wird.
