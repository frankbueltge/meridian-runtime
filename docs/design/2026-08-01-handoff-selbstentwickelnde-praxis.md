# Handoff: Meridian erforscht das Feld und entwickelt sich daraus weiter

**Stand:** 2026-08-01, nach der Session, die MRR online gebracht hat.
**Zweck:** Das Vorhaben festhalten, das der Owner am Ende dieser Session
beschrieben hat, samt der Voraussetzungen, die es tragen — damit die nächste
Session nicht bei Null anfängt und nicht in die Fallen läuft, die hier schon
benannt sind.

**Kein Bau in dieser Notiz. Kein Paket. Nichts entschieden.**

---

## 1. Das Vorhaben, in einem Absatz

Meridian — das empirische Forschungskollektiv, nicht die Engineering-Linie —
erforscht fortlaufend das Feld **end-to-end automation of AI research** und
entwickelt sich auf Basis dieser Recherchen selbst weiter: neue Instrumente,
geschärfte Verfahren, Änderungen an der eigenen Verfassung. Als zusätzliche
nächtliche Routine, nicht als Einmalprojekt. Jede Selbstveränderung trägt den
Befund, der sie ausgelöst hat, als Beleg.

Der Owner hat das ausdrücklich als das benannt, was er mit MRR **ursprünglich
vorhatte**. Es ist damit kein Nebenprodukt, sondern der Zweck.

## 2. Die Zuordnung, geklärt

> **MRR ist Meridians Werkzeug, nicht Meridians Stimme.**

Der Owner am 2026-08-01, wörtlich: „es ist nicht die Stimme sondern ein
Werkzeug was sie nutzen können wann immer es Sinn macht".

Das löst die Spannung, die `docs/wording-kanon.md` (frankbueltge.de) und
enc-2026-005 hinterlassen hatten. Beide bleiben gültig in ihrem Kern: ein
**einzelner Lauf**, den die Engineering-Linie fährt, ist keine Aussage des
autonomen Kollektivs. Aber das **Werkzeug** gehört Meridian. Der nächtliche
Lauf vermerkt deshalb seit dieser Session Meridians Praxis-Identität als
`actor` (`urn:mrr:practice:01KYG3AY344T18D0479TG557KX`, „The Field").

**Folge, noch offen:** `/field` auf frankbueltge.de sagt derzeit, MRR sei
„not by the collective's own research voice". Das ist die alte Zuordnung und
widerspricht dem Owner. Nachzuziehen, wenn ohnehin an `/field` gearbeitet wird.

## 3. Was heute schon steht

| | |
|---|---|
| Forschungsläufe | Fahren **nächtlich von selbst**, online, ohne die Maschine eines Menschen. Auslöser ist eine Frage, keine Uhr: ein Korpus unter `corpora/` mit allen fünf Eingabedateien, der nicht in `archive/answered.json` steht. Einen Korpus zu committen IST das Stellen der Frage. |
| Ergebnisvorlage | Als Pull Request, nie direkt auf `main`. Darf Actions keinen PR anlegen, kommt ein Issue mit Ein-Klick-Link. |
| Läufe im Archiv | 3 — `model-collapse` (Lauf 1 + Sensitivitätsvariation), `e2e-claims` (2026-08-01). Vollständige DB-Zustände als SQL-Dumps. |
| Öffentlich sichtbar | `/on-record` (Läufe 1+2), `/e2e-automation` (Befund aus Lauf 3, datiert vermerkt) |
| Belegt | Ein Befund reproduziert **byte-identisch auf fremder Maschine** (gleicher Status, gleiche Zählung, gleiche Aussage-sha256). |
| Anleitung | `corpora/README.md` — wie eine Frage gestellt wird, was die Maschine tut und was ausdrücklich nicht. |

Meridians Praxis-Identität existiert und ist signiert (`practices/meridian.json`,
gültig bis 2027-07-26). Der Korrekturweg ist bedienbar (`mrr correction`), und
eine Korrektur kann als signiertes Offline-Bündel das Haus verlassen.

## 4. Die drei Voraussetzungen — die Reihenfolge ist Methodik, nicht Vorsicht

### 4.1 Ein fester Maßstab. Zuerst.

**Ohne ihn heißt „besser" nichts.** Jede Selbstveränderung wäre dann nur
Veränderung, und der Kreis produziert Bewegung, die sich Entwicklung nennt.

Gebraucht wird ein **vorab eingefrorener Goldstandard**: eine Menge von Fällen
mit festgelegter richtiger Antwort, gegen die eine geänderte Praxis gemessen
wird. Eingefroren *bevor* die erste Änderung passiert, sonst wandert der Maßstab
mit.

Das ist exakt „Sprosse 2" der Leiter vom 2026-07-26
(`2026-07-26-ableitung-eigenexperiment-orchestrierung.md`) — die
Validitäts-Hälfte, die dort schon als fehlend benannt wurde und weiter fehlt.
Die Pakete `N1-T02`/`N1-T03` existieren **nicht**; `N1-T01`
(`mrr validate agreement`) liefert Reliabilität, ausdrücklich **nicht**
Validität.

Braucht kein Modell und kein Netz. Kann sofort gebaut werden.

### 4.2 Modellgestützte **Einordnung**. Eine echte neue Fähigkeit.

Ein automatischer Literaturkanal muss entscheiden: stützt diese Quelle die
Aussage, oder widerspricht sie? Heute macht das ein Mensch beim Kuratieren des
Korpus.

**Fact-lock aus dieser Session, am Code belegt:** Der gebaute modellgestützte
Schritt (`build_model_assisted_extraction_callable` im Synthese-Executor)
schlägt nur die zwei **Prosafelder** `claim_relevant_finding` und
`classification_basis` vor. Die Einordnung selbst, `evidence_relation`, kommt
aus dem Korpus und wird vom Modellschritt **nie berührt**
(`synthesis_executor.py`, Zeilen 710/713).

Architektonisch ist das vorbildlich — „never sole judges" ist per Konstruktion
erzwungen, nicht per Regel. Aber es heißt: **eine Verdrahtung genügt nicht.**
Modellgestützte Klassifikation ist zu bauen, nicht zu verbinden.

Anmerkung: `GeminiModelAdapter` existiert und ist getestet, wird aber von nichts
außer Tests aufgerufen.

### 4.3 Die Schutzregel gegen den eigenen Fehlermodus

**Eine Praxis, die sich aus ihren eigenen Ausgaben weiterentwickelt, ist exakt
das Ding, das MRRs erster Lauf untersucht hat.** Rekursives Training auf
eigenem Output. Model Collapse. Optimiert Meridian sich aus früheren
Meridian-Befunden, reproduziert es die Degeneration, die es selbst dokumentiert
hat — und das wäre die peinlichste mögliche Art zu scheitern.

Verbindlich also:

1. **Jede Selbstveränderung zitiert externe Evidenz.** Nie ausschließlich
   eigene Befunde. Prüfbar als Bedingung, nicht als Vorsatz.
2. **Die Änderung durchläuft den bestehenden Gauntlet** — adversariale Prüfung
   durch die eigene Praxis, wie jedes Instrument.
3. **Die Kriterien für „besser" werden von außen gesetzt**, nicht von der
   Praxis selbst gewählt. Sonst optimiert sie auf ihre eigene Zustimmung.
4. **Frische externe Daten in jeder Runde.** Das ist die direkte Lehre aus dem
   Model-Collapse-Korpus: ohne genug frisches reales Material pro Generation
   verfällt die Verteilung (Alemohammad et al., MAD).

## 5. Fact-locks aus dieser Session — damit niemand neu rät

- **`output_hash` ist kein stabiler Fingerabdruck eines Befunds.** Eine pro
  Lauf neu vergebene Protokoll-ID geht in den gehashten Eingang ein. Der
  Befund reproduziert, der Hash nicht. Wer ihn als Kennung benutzt, läuft in
  eine Falle.
- **`verification_disposition="verified"` auf einem rohen Modellvorschlag**
  (`synthesis_executor.py:483/609`): bei Modell-Erfolg wird die kuratierte
  Begründung des Menschen **überschrieben** und das Ergebnis als „verified"
  markiert. Offene Spezifikationsfrage über Epistemik, braucht eine
  Owner-Vorlage. Blockiert 4.2.
- **`docs/spec/` ist historisch für Architektur, normativ für Semantik.** Die
  Referenzarchitektur (FastAPI, Temporal, S3) wurde nie gebaut und ist per
  Import-Vertrag **verboten**. Wo Spezifikation und Repository über
  Infrastruktur streiten, hat das Repository recht.
- **`AGENTS.md` kennt jetzt den Integrations-Pakettyp**, dessen Abnahme ein
  Operatorweg ist und kein Modul. Grund: viermal ist dieselbe Außenkante offen
  geblieben, weil Kompositionsarbeit weder Domänenverhalten aus der
  Spezifikation noch ein Spezifikationsabschnitt ist.
- **Die Belegbytes der Läufe 1 und 2 sind nicht einlösbar.** 51 Anker tragen
  `validated`, und dieser Status ist für sie nicht falsifizierbar. Vorwärts
  behoben, rückwärts unrettbar. Nicht als geschlossen behandeln.
- **Neun Archivsignaturen sind gültig aber nicht zurechenbar**
  (`node-key-1`/`origin-key-1` statt abgeleitetem `kid`). Offener, datierter
  Defekt aus dem Handoff vom 2026-07-27.

## 6. Was ausdrücklich NICHT zu tun ist

- **Keine Fähigkeit bauen, für die keine Frage vorliegt.** Der Review vom
  2026-07-31 hat den Fähigkeitsausbau eingefroren: 80 Pakete für eine Frage
  war das Missverhältnis. Neue Fähigkeit folgt einer realen Frage.
  Ausnahme: die drei Voraussetzungen oben — sie tragen das Vorhaben selbst.
- **`docs/spec/06_IMPLEMENTATION_PLAN.md` nicht als Fahrplan lesen.** Alle 80
  Pakete sind geschrieben. Historisches Backlog.
- **Kein Nightly, das dasselbe neu rechnet.** Der bestehende ist so gebaut, dass
  er nur bei einer *neuen Frage* etwas tut. Ein Lauf über einen gepinnten
  Korpus liefert ewig byte-identisch dasselbe — täuschte Frische vor.
- **Keine Selbstveränderung ohne den Maßstab aus 4.1.** Das ist die eine harte
  Reihenfolge in dieser Notiz.

## 7. Offene Owner-Entscheidungen

1. **Darf ein Modellvorschlag die kuratierte Begründung überschreiben und
   `verified` heißen?** (Fact-lock in §5.) Blockiert 4.2, nichts anderes.
2. **Wer setzt die Kriterien für „besser"?** Nach 4.3 nicht die Praxis selbst.
   Der Owner, oder ein Encounter mit einer anderen Praxis (The Middle)?
3. **`/field`-Wortlaut nachziehen** — MRR ist Werkzeug, nicht Stimme.
4. **Ulysses' Node-ID und Vertrauenserklärung** — kein Bau, eine Absprache.
   Danach ist der erste Ecology-Austausch nur noch ein Vollzug.

## 8. Empfohlene Reihenfolge

1. **Maßstab** (4.1) — braucht kein Modell, kein Netz, keine offene
   Entscheidung. Sofort baubar.
2. **Modellgestützte Einordnung** (4.2) — nach Entscheidung 1.
3. **Der Literaturkanal**: Korpus aus frischer externer Literatur automatisch
   erzeugen, damit der nächtliche Lauf eine echte neue Frage vorfindet.
4. **Die Kopplung**: Befund → Änderung an Instrument oder Verfassung, unter den
   Regeln aus 4.3, durch den Gauntlet.

Schritt 1 zuerst ist nicht Vorsicht. Ohne ihn produzieren 3 und 4 eine Zahl
ohne Maßstab — und eine Praxis, die sich selbst zustimmt.
