# Ableitung N1-T04: modellgestützte Einordnung — das erste System, das gemessen werden kann

**Status:** Ableitung mit Fact-Lock. **Kein Bau in dieser Notiz.** Das Paket
`task-packets/N1-T04.yaml` folgt aus ihr; gebaut wird erst danach.

**Anlass:** Schritt 2 der verbindlichen Reihenfolge des Owners vom 2026-08-01
(**1. Maßstab → 2. modellgestützte Einordnung → 3. Literaturkanal →
4. Kopplung**). Schritt 1 steht seit N1-T02: der Maßstab existiert, die erste
Messung ist gelaufen, und sie hat gemessen, dass es nichts zu messen gab. Der
Boden war die Messung, weil kein System existierte, das eine Einordnung liefert.
Dieses Paket baut dieses System.

**Owner-Vorgabe zum Betrieb, 2026-08-02:** *„ich will NIX lokal, alles läuft
online und automatisch."* Das ist keine Bequemlichkeit, sondern eine
Betriebsentscheidung mit Folgen für den Zuschnitt, und §6 zieht sie.

---

## 1. Fact-Lock — der Stand vom 2026-08-02, am Code nachgerechnet

Der Handoff (`2026-08-02-stand-und-was-fehlt.md`) sagt von sich selbst, er sei
Ausgangslage und nicht Auftrag, und nennt zu jeder Behauptung eine Fundstelle.
Das wurde eingelöst: jede prüfbare Behauptung wurde nachgerechnet, nicht
nachgelesen.

### Was hält — nachgerechnet, nicht übernommen

| Behauptung | Wie geprüft | Ergebnis |
|---|---|---|
| 2565 Unit-, 600 Contract-, 48 Benchmark-Tests | `make test`, `make test-contract`, `make benchmark` ausgeführt | **hält** — Contract meldet zusätzlich 2 `skipped`, was der Handoff weglässt |
| `check_gold_freeze.py` prüft 6 Versionen, CI führt es | Skript ausgeführt, `ci.yml:71` gelesen | **hält** |
| Erste Messung: Accuracy 0,4211 = Boden 0,4211, κ 0,0000 | `mrr validate gold` gegen die committeten Dateien erneut gefahren | **hält, exakt** — dazu α −0,2662, False-Support 0/56, n=57, 3 unentscheidbar |
| `GeminiModelAdapter` hat null Produktiv-Aufrufstellen | Baumweite Suche | **hält** — nur Tests und der eigene Modul-Docstring |
| `evidence_relation` ist typseitig unerreichbar | `synthesis_executor.py:247/251/413-421` gelesen | **hält** — Top-Level-Feld, `extraction` ist ein getrennter Dict, `ExtractionOutcome` kann keinen Entry tragen |
| Föderation ist payload-agnostisch | `federation_main.py:466/483` gelesen | **hält** |
| Die nächtliche Recherche läuft leer | Auswahllogik aus `research-run.yml:57-109` gegen den Live-Baum neu ausgeführt | **hält** — `pending = []`; alles andere übersprungen wegen fehlender Eingaben oder `answered` |
| Feldbeobachtung läuft 01:10 UTC | `field-watch.yml:49` (`cron: '10 1 * * *'`) | **hält** |
| `_verification_disposition` ist write-only | Baumweite Suche | **hält** — Konstante `:202`, Schreibzugriff `:611`, kein einziger Lesezugriff |

### K1 — Der Goldstandard selbst ist nicht eingefroren

**Behauptet** (Handoff §2): *„Kriterien v1→v2→v3, alle eingefroren und lesbar |
`check_gold_freeze.py`, 6 Versionen."*

**Wörtlich richtig und in der Sache irreführend.** Die sechs Einträge in
`benchmarks/meridianbench/fixtures/FROZEN.json` sind: `mb-cls-criteria-v1/v2/v3`
und `mb-cls-v1/v2/v3-synthetic`. Das sind die drei Kriterienfassungen und die
drei **synthetischen** Fixtures.

`mb-cls-ulysses-v1-restamped` — der Satz, gegen den die erste echte Messung lief
und gegen den jedes System dieses Pakets gemessen wird — **steht nicht darin.**
Die CI-Prüfung, die genau dafür gebaut wurde, deckt den echten Maßstab nicht ab.

Und `--expect-sha256` ist optional (`validation_main.py:164-172`): der
Reproduktionslauf für diese Ableitung hat keinen Hash mitgegeben und wurde
anstandslos angenommen. Es gibt heute also **keinen erzwungenen Pin** auf den
Maßstab, weder im CLI noch in CI.

Die Labels sind mittelbar geschützt — `test_gold_classification_commission.py`
vergleicht `restamped["cases"]` gegen die zurückgegebene Datei und pinnt deren
sha256 im Kopf des umgestempelten Satzes. Das ist eine gute Kette, aber es ist
nicht der Mechanismus, den N1-T02 §4.3 für genau diesen Zweck gebaut hat
(*„Ein Skript in CI schlägt fehl, wenn sich der Hash einer bereits registrierten
Version ändert"*).

**Folge:** Dieses Paket registriert den echten Goldstandard in `FROZEN.json` und
lässt seinen Workflow **mit** `--expect-sha256` messen. Begründung für den
Eingriff in fremdes Paketgebiet: dieses Paket ist das erste, das einen echten
Messwert gegen diesen Satz erzeugt. Einen Maßstab zu benutzen, den CI nicht
festhält, hieße die Messung auf eine Datei zu gründen, die sich unbemerkt
bewegen darf.

### K2 — Schritt 2 ist spezifikationsgedeckt; Handoff und N1-T02-Ableitung wissen das nicht

**Behauptet** (Handoff §3.1, N1-T02-Ableitung §K2/§7): die modellgestützte
Einordnung sei ein *neuer Pfad*, ohne benannte Spezifikationsgrundlage. Unter
`AGENTS.md` Regel 3 (*„Do not invent domain behavior that is absent from the
specification"*) liest sich das wie eine Fähigkeit, die eine Ausnahme braucht —
etwa den Integrations-Paket-Typ.

**Sie braucht keine.** `docs/spec/08_RESEARCH_METHOD_KERNEL.md` §5 listet die
Schritte des Profils `systematic_evidence_synthesis` v1 selbst auf:

> *„Model-assisted steps (each per MTH-016): extraction **and classification
> proposals** against protocol-declared fields, verified against the anchored
> source or downgraded to marked proposals."*

Der Executor hat die **Extraktions**hälfte dieses Satzes gebaut
(`build_model_assisted_extraction_callable`, `:431-488`). Die
**Klassifikations**hälfte wurde nie gebaut. N1-T04 ist damit ein gewöhnliches
Fähigkeits-Paket, das einen deklarierten Schritt vollendet — keine Erfindung und
kein Sonderfall.

Das ist keine Wortklauberei: der Unterschied entscheidet, ob das Paket unter
Regel 2/3 normal läuft oder eine Begründung schuldet, die es nicht hat.

### K3 — „Die Disposition heißt schema-valid" verletzt MTH-016; der wirkliche Defekt ist der umgekehrte

**Behauptet** (Handoff §3.1, N1-T02-Ableitung §K3): *„die Disposition heißt, was
sie misst (schema-valid)."*

**So nicht umsetzbar.** MRR-MTH-016 verlangt als MUSS eine verification
disposition aus einem **geschlossenen** Vokabular:

> *„every model-assisted step records a `ModelInvocation` and a verification
> disposition (verified / downgraded-to-proposal / rejected)."*

Ein vierter Wert `schema-valid` erweitert dieses Vokabular. `AGENTS.md` Regel 4
verbietet, ein MUSS aufzuweichen, damit etwas durchgeht — und die Typen sagen
dasselbe: `ExtractionOutcome.verification_disposition` ist ein
`Literal["verified", "downgraded-to-proposal", "rejected"]` (`:421`).

**Die Owner-Entscheidung braucht den vierten Wert aber gar nicht.** Dieselbe
Spezifikationszeile definiert, was `verified` heißt: *„verified against the
anchored source or downgraded to marked proposals."* `verified` ist der Zustand
nach einer Prüfung gegen die verankerte Quelle — nicht nach geglücktem
JSON-Parsen.

Genau daran scheitert der bestehende Code: `synthesis_executor.py:483` setzt
`verification_disposition="verified"`, sobald `generate_structured` ein
schemagültiges Objekt zurückgibt. Verifiziert wurde dabei nichts. **Das ist die
MTH-016-Verletzung** — und es ist dieselbe Falle, die die N1-T02-Ableitung als
K3 beschrieben hat, nur schärfer benannt.

**Folge:** Der neue Pfad vergibt **immer** `downgraded-to-proposal` und kann
`verified` gar nicht erzeugen — nicht als Hausregel, sondern weil er keine
Prüfung gegen die verankerte Quelle durchführt. „Nie verified" ist damit keine
Zusatzauflage des Owners, sondern das, was die Spezifikation ohnehin sagt.
`rejected` bleibt ebenfalls unerreichbar: es ist laut Docstring des bestehenden
Arms für einen späteren aufrufer­seitigen Prüfschritt reserviert, den dieses
Paket nicht baut.

Der bestehende Extraktions-Arm wird dabei **nicht** angefasst. Er zu reparieren
ist ein eigener Vorgang mit eigenem Paket; ihn hier nebenbei umzuschreiben wäre
genau die Vermischung, die Regel 2 verbietet. Er wird in §8 als offener Befund
weitergereicht.

---

## 2. Was gemessen wird, und warum es dieselbe Aufgabe ist

Gemessen wird die eine Entscheidung, die der Goldstandard bereits abbildet:

> Stützt diese Quelle die Aussage, oder widerspricht sie ihr?

Das System unter Test bekommt **exakt die Kommission, die Ulysses bekommen hat**
(`corpora/gold-classification/commission.v2.json`, sha256
`a6b4619a…3cc0b762`), und **exakt die Kriterien, unter denen Ulysses gelabelt
hat** (`mb-cls-criteria.v3.json` — Definitionen byte-identisch zu v2, unter
denen die Arbeit entstand).

Die Blindheit ist strukturell, nicht versprochen: die Kommission trägt keine
Labels. Die Vereinigung aller Fallschlüssel ist `case_id, claim_text, excerpt,
excerpt_sha256, source_identifiers, source_url, title` — nachgerechnet für diese
Ableitung, wie Ulysses es seinerseits nachgerechnet hat. Es gibt in der Eingabe
nichts, womit ein Modell kontaminiert werden könnte.

**Warum die Kriterien mit ihrem bekannten Fehler übergeben werden.** Ulysses'
Befund 4.1 — `supports` trägt einen Allgemeinheitszaun, `contradicts` keinen —
steht unbehoben, und das ist richtig so: eine Definitionsänderung entwertet eine
fertige Blindarbeit rückwirkend. Ein System, das unter *anderen* Kriterien
einordnet als der Maßstab, misst nichts. Die Asymmetrie ist Teil der Aufgabe,
solange dieser Maßstab der Maßstab ist.

## 3. Was gebaut wird — und was ausdrücklich nicht

Gebaut wird ein **eigener Pfad**, der neben dem Synthese-Executor steht und ihn
nicht berührt:

1. **Eine Domänen-Projektion** (`relation_proposal.py`): der Vorschlag des
   Modells für einen Fall — vorgeschlagene Relation, Begründung, `decided_by`,
   `tie_with`, Unentscheidbarkeit, Disposition. Pydantic-validiert, nie
   persistiert, nie autoritativ.
2. **Ein Dienst** (`relation_service.py`), der einen injizierten `ModelAdapter`
   über die Fälle führt — über `generate_structured` (E4-T02, unverändert), mit
   beschränkter Reparatur.
3. **Ein Kommando** (`mrr classify relations`), read-only bis auf seine eine
   Ausgabedatei, im Muster von `mrr validate gold`.
4. **Ein Workflow**, der klassifiziert *und* misst und beides committet.

**Nicht angefasst:**

- `synthesis_executor.py` — der Extraktions-Arm bleibt, wie er ist, samt seinem
  falschen `verified`. Eigener Vorgang (§8).
- `evidence_relation` auf `CorpusEntry` — der Modellvorschlag landet in einer
  eigenen Datei, nicht in einem Korpus-Eintrag. Die Typgrenze aus K2 der
  N1-T02-Ableitung ist der Schutz und wird nicht aufgeweicht, sondern schlicht
  nicht berührt.
- `agreement.py`, `agreement_report.py`, `gold_validity_report.py`,
  `gold_service.py` — die Messseite ist fertig. Dieses Paket liefert ihr eine
  Eingabe, keine Änderung.
- Kriterien und Goldsatz-Labels — unantastbar.

## 4. Die Ausgabe ist zugleich Vorhersagedatei und Prüfspur

`mrr validate gold --predictions` erwartet `{"system_id": …, "predictions":
{case_id: relation}}` und toleriert weitere Schlüssel auf oberster Ebene
(`gold_service.py:488-511`; die Basisliniendatei nutzt das bereits für ihr
`note`). Die Ausgabe dieses Pakets nutzt denselben Umschlag und hängt die
Prüfspur daneben: je Fall die Begründung, den entscheidenden Regel-Namen, den
Zweitplatzierten, die Disposition und den Antwort-Hash; global das Modellprofil,
den Prompt-Hash, den sha256 der Kommission und den der Kriterien.

Eine Datei, zwei Leser. Der Messapparat liest `predictions`, ein Mensch liest
den Rest, und niemand muss zwei Dateien in Übereinstimmung halten.

**Fail-closed bei Lücken.** Ein Fall, für den `generate_structured` keinen
schemagültigen Vorschlag liefert, bekommt **keine** Vorhersage; sein Scheitern
wird mit dem unterscheidbaren Status vermerkt, den E4-T02 ohnehin führt
(`schema_invalid`, `refused`, `content_filtered`, `error`, `timed_out` — nie zu
einem generischen Fehler zusammengefasst). Das Kommando endet dann mit einer
Weigerung und nennt die betroffenen Fälle. Ein Teilsatz, der als vollständige
Messung durchginge, ist genau das, was die Apparatur verhindern soll —
`build_report` reicht `MismatchedRatersError` aus demselben Grund unverändert
durch (`gold_service.py:525-529`).

**Unentscheidbar ist kein Fehler.** R-undecidable-is-a-finding gilt für den
Maßstab; ein System, das dieselben Kriterien anwendet, muss dieselbe Möglichkeit
haben. Ein als unentscheidbar markierter Fall liefert keine Vorhersage und ist
kein Scheitern des Laufs — er wird gezählt und berichtet. Der Goldsatz hält
seine drei Unentscheidbaren ohnehin aus der Matrix heraus (n=57 von 60), und der
Vergleich der beiden Unentscheidbarkeits-Mengen ist ein Befund über die
Kriterien, kein Nebenprodukt.

**Kein Wanduhr-Zeitstempel in der Datei.** N1-T02s Invariante gilt weiter, und
der Anlass steht im Handoff §5: ein hand-getippter Zeitstempel in etwas, das auf
Zeit prüft, ist ein Designfehler. Wann gelaufen wurde, sagt der Commit und der
Workflow-Lauf, nicht ein Feld, das driften kann.

## 5. Determinismus, wo er möglich ist — und Ehrlichkeit, wo nicht

Ein Modelllauf ist nicht reproduzierbar. Deshalb trennt das Paket sauber:

- **Der Apparat** ist vollständig offline gegen einen gescripteten
  Fake-`ModelAdapter` getestet — kein Netz, kein Schlüssel, kein Provider, in
  jedem Testrang. Das ist das Muster, das `test_gemini_adapter.py` und der
  bestehende Modellarm bereits verwenden.
- **Der Lauf** ist ein einmaliger Akt, dessen Ergebnis als committete Datei mit
  eigener Provenienz ins Repository kommt — dasselbe Muster wie der Goldsatz
  selbst und wie `agreement-crosswalk.v1.json`.

Was das Paket **nicht** behauptet: dass die Zahl ein Modellvermögen misst. Die
Auszüge stammen aus arXiv-Papern, die im Training des Modells gelegen haben
können. Das ist eine benannte Grenze des Aufbaus, kein Fußnotenmaterial, und sie
gehört in den Bericht — dieselbe Ehrlichkeit, mit der Ulysses „these labels are
not a human gold standard" in den eigenen Rücklauf geschrieben hat.

## 6. Betrieb: online und automatisch, aber kein Nightly

Die Owner-Vorgabe (*„alles läuft online und automatisch"*) trifft auf eine
stehende Regel des Handoffs §7: *„Kein Nightly, das dasselbe neu rechnet"* und
*„Keine dritte nächtliche Routine."* Beides ist erfüllbar, und zwar ohne
Kompromiss:

- **Online.** Der Lauf findet in GitHub Actions statt. `GEMINI_API_KEY` liegt
  dort bereits als Repo-Secret (seit 2026-07-26). Kein Schlüssel berührt eine
  lokale Shell, und der Lauf hat eine Lauf-ID, auf die ein Bericht zeigen kann.
- **Automatisch, aber anlassgetrieben.** Der Workflow feuert bei
  `workflow_dispatch` **und** bei Push, wenn sich eine seiner Eingaben ändert —
  Klassifikator, Kommission, Kriterien oder Goldsatz. Eine *neue* Eingabe löst
  eine *neue* Messung aus; eine unveränderte Nacht löst nichts aus.

Das ist keine Erfindung für diesen Anlass, sondern exakt die Philosophie, die
`research-run.yml` schon fährt (*„nur eine neue Frage löst etwas aus"*, K4 der
N1-T02-Ableitung). Ein Zeitplan, der jede Nacht dieselben 60 eingefrorenen Fälle
erneut an ein Modell schickt, verbrennt Kontingent und erzeugt Rauschen, das
nicht von Befund zu unterscheiden ist.

Der Workflow misst im selben Lauf mit `mrr validate gold --expect-sha256` und
committet Vorhersagen und Bericht. Damit ist die Außenkante besetzt, um die es
`AGENTS.md` in seiner eigenen Tabelle viermal geht: es gibt einen benannten
Operatorpfad, der von Ende zu Ende läuft, und nicht bloß ein Modul, das seine
eigenen Tests besteht.

## 7. Abgrenzung — was N1-T04 NICHT tut

- **Keine Änderung an der Messseite.** N1-T02s Apparat bleibt byte-identisch,
  bis auf den Registrierungseintrag aus K1.
- **Kein Literaturkanal.** Schritt 3 bleibt Schritt 3; dieses Paket erzeugt
  keinen Korpus und beantwortet keine Frage.
- **Keine Kopplung an die Verfassung.** Schritt 4, Repo-Grenze, unberührt.
- **Keine Selbstoptimierung.** Nichts hier verändert Prompt oder Kriterien
  aufgrund eines Messergebnisses. Der Optimierer, der seinen eigenen Evaluator
  bewertet, ist der dokumentierte Fehlermodus
  (`2026-07-24-primaerquellen-selbstoptimierung.md`, DGM-Vorfall).
- **Keine Schwellenwerte.** `GOLD_CLASSIFICATION_*_TARGET` bleiben `None` und
  lassen ihre Prüfungen durchfallen. Sie kommen aus einem Encounter, nicht aus
  einem Bau (N1-T02 R5).
- **Kein Prompt-Stability-Lauf.** Krippendorffs Alpha über Paraphrasen bleibt
  N1-T03 — dieses Paket liefert ihm erstmals den Modellarm, den es braucht, und
  baut ihn nicht selbst.

## 8. Offene Befunde, weitergereicht statt nebenbei behoben

1. **Der Extraktions-Arm setzt `verified`, ohne zu verifizieren**
   (`synthesis_executor.py:483`, K3). Ein MUSS-Verstoß gegen MTH-016 in
   bestehendem Code, heute ohne Datenfolge, weil die Disposition write-only ist
   und der Claim-Text aus dem kuratierten Feld gebaut wird. Eigenes Paket:
   entweder die Disposition auf `downgraded-to-proposal` korrigieren oder den
   Prüfschritt gegen die verankerte Quelle bauen, den `verified` behauptet.
2. **`--expect-sha256` bleibt optional** (K1). Dieses Paket macht den Pin im
   Workflow verbindlich, aber nicht im CLI. Ihn zur Pflicht zu machen ist eine
   Änderung an N1-T02s Kommando und gehört dorthin.
3. **Der Allgemeinheitszaun** (Ulysses 4.1) steht weiter unbehoben, und das
   bleibt eine Owner-Entscheidung (Handoff §6.2). Dieses Paket erzeugt aber
   erstmals Material dafür: wo ein zweiter, unabhängiger Leser dieselben Zäune
   trifft, ist der Zaun das Problem, und wo nicht, war es die Lesart.
4. **Kein stabiler Fingerabdruck eines Befunds** (Handoff §5) — unverändert
   offen und von diesem Paket nicht berührt.
5. **`hashes_only` macht `generate_structured` ergebnislos.** Gefunden beim
   ersten Online-Lauf: `generate_structured` prüft `raw_response_text`, und
   `ModelInvocationOutcome` verbietet dieses Feld unter `hashes_only`
   (MRR-FR-045). Unter dieser Policy ist `status == "proposal"` also
   **strukturell unerreichbar**. Der bestehende Extraktions-Arm fordert genau
   diese Policy und verzweigt dann auf `"proposal"`, um `verified` zu setzen —
   der Zweig ist gegen jeden konformen Adapter toter Code. Sein Test besteht
   nur, weil das Fake-Objekt `raw_permitted`-Outcomes zurückgibt, unabhängig
   davon, was die Anfrage verlangt. Das schärft Befund 1: `verified` ist dort
   nicht bloß semantisch falsch, es ist unerreichbar. Reparatur gehört ins
   selbe eigene Paket.
6. **Ein Guard, der nur getrackte Dateien sieht.** `git diff --quiet -- <pfad>`
   übersieht neue Dateien. Im Workflow warf das eine fertige Messung weg und
   meldete Erfolg. Behoben (erst stagen, dann fragen); `field-watch.yml` trägt
   dasselbe Muster und ist heute unauffällig, weil seine Registerdatei bereits
   existiert — ein eigener, kleiner Vorgang.

## 9. Offene Punkte dieses Pakets

1. **Welches Gemini-Modell.** Ein konkreter Name muss in die Provenienz, weil
   „Gemini" kein Messobjekt ist. Der Name wird als Workflow-Eingabe gesetzt und
   in die Ausgabedatei geschrieben, nicht im Code versteckt.
2. **Kontingent.** 60 Fälle mal bis zu (1 + Reparaturen) Aufrufe. Der
   AI-Studio-Free-Tier ist ratenbegrenzt; der Dienst braucht daher eine
   Drosselung zwischen den Aufrufen und muss bei `error`/`timed_out` ehrlich
   scheitern statt zu wiederholen, bis es passt.

   **Geändert 2026-08-02, nach dem Bau, aus einem Lauf gelernt.** Der Satz
   oben zog die Linie an der falschen Stelle. Ein Lauf klassifizierte 59 von
   60 Fällen und verwarf alles wegen eines einzigen `timed_out` — das ist
   nicht Ehrlichkeit, das ist Sprödigkeit. Die tragfähige Unterscheidung ist
   nicht „wiederholen ja/nein", sondern **wer geantwortet hat**:

   - `refused` und `content_filtered` **sind** die Antwort des Modells.
     Sie zu wiederholen, bis etwas anderes herauskommt, wäscht eine Weigerung
     in ein Ergebnis um. Bleibt verboten.
   - `schema_invalid` ist eine schlechte Antwort und hat mit E4-T02s
     beschränkter Reparatur bereits ihren eigenen Mechanismus.
   - `error` und `timed_out` heißen, dass **überhaupt keine Antwort ankam.**
     Das ist eine Tatsache über den Transport, keine Aussage des Modells.

   Für diese zwei — und nur diese zwei — gilt jetzt ein **beschränkter,
   gezählter** Retry (`NO_ANSWER_STATUSES`, Standard 2). „Beschränkt und
   gezählt" ist der Unterschied zu „bis es passt": eine Kontingentwand lässt
   auch jeden Retry scheitern, der Lauf weigert sich weiterhin, und
   `transport_retries_used` steht je Fall im Artefakt. Ein Lauf, der Retries
   brauchte, sieht nie aus wie einer, der keine brauchte.
3. **Ob der Lauf veröffentlicht wird.** Das Ergebnis landet als Commit im
   Branch, nicht auf `main`. Der Merge ist ein Owner-Akt, wie jeder andere auch.
