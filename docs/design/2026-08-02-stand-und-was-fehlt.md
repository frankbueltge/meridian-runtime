# Stand nach der Nacht vom 1. auf den 2. August — und was fehlt

**Zweck:** Damit die nächste Session weiterbaut statt neu zu raten.

**Lies das hier als Ausgangslage, nicht als Auftrag.** Der vorige Handoff
(`2026-08-01-handoff-selbstentwickelnde-praxis.md`) lag an vier Stellen falsch,
und das fiel erst beim Fact-Lock am Code auf. Prüf also, was hier steht. Jede
Behauptung nennt ihre Fundstelle, damit das billig ist.

## 0. Einstieg — in dieser Reihenfolge

Die Arbeit verteilt sich über **drei** Repositories, was leicht zu übersehen ist:

| | wofür |
|---|---|
| `meridian-runtime` | das Werkzeug. Hier liegt alles Gebaute. |
| `field-research` | die Praxis Meridian selbst. Verfassung, Gauntlet, Journal. |
| `ulysses` | die Praxis, die den Maßstab gesetzt hat. Nur lesen, nie ungefragt schreiben. |

Zu lesen, bevor irgendetwas angefasst wird:

1. **dieses Dokument** — Stand, Fehlendes, Fact-Locks
2. `meridian-runtime/AGENTS.md` — Bau-Disziplin. Insbesondere: Ableitung und
   Paket **vor** dem Bau, keine Fähigkeit ohne Anlass.
3. `meridian-runtime/docs/design/2026-08-01-n1-t02-ableitung-goldstandard.md` —
   warum der Maßstab so aussieht, wie er aussieht
4. `ulysses/docs/research-notes/meridian-commission/RETURN-2026-08-01.md` — die
   Rückgabe der labelnden Praxis. **Die drei Befunde darin sind der beste
   Prüfmaßstab für alles, was als Nächstes gebaut wird.**

Und die Regeln, die über allem stehen, aus `../CLAUDE.md`:
Git-Identität `Frank Bültge <f.bueltge@gmail.com>`, **nie** `frank@bueltge.de`;
keine KI-Produkt-Credits in Commits oder Inhalten; nichts auf `main` und nichts
in eine fremde Praxis ohne ausdrückliche Freigabe.

---

## 1. Wo es steht, in einem Absatz

Der Maßstab existiert. Eine fremde Praxis hat sechzig Fälle blind gelabelt, der
Apparat misst gegen sie, und die erste echte Messung ist gelaufen. Was fehlt,
ist ein System, das gemessen werden könnte: es gibt bis heute keinen
Klassifikator, nur den Boden. Der nächste Schritt ist deshalb Schritt 2 — ein
Modell darf einordnen.

## 2. Was steht, und woran das geprüft wurde

| | Beleg |
|---|---|
| `mrr validate gold` misst Validität gegen einen hash-gepinnten Goldstandard | 2565 Unit-, 600 Contract-, 48 Benchmark-Tests |
| Vier Weigerungen: verschobener Standard, verletztes Reihenfolge-Gate, synthetische Fixture, Kriterien-Drift | je ein Test, der den realen Fehlerfall nachstellt |
| Kriterien v1→v2→v3, alle eingefroren und lesbar | `check_gold_freeze.py`, 6 Versionen |
| 60 Fälle mechanisch gezogen (353er Pool, sha256-Ordnung), Pool mit `drawn`-Flag committet | Ulysses hat die Ziehung nachgerechnet und bestätigt |
| Ulysses hat alle 60 blind gelabelt und die Studie geschlossen | `ulysses/projects/2026-08-01-sixty-cases-blind/DECISION.md` |
| Erste echte Messung gelaufen | Boden: Accuracy 0,4211 = Mehrheitsboden 0,4211, **Kappa 0,0000** |
| Feldbeobachtung (Routine 2, erste Hälfte) läuft 01:10 UTC | online ausgelöst 2026-08-01: 14 Suchen, 0 neu, nichts geschrieben |
| `/field` sagt: MRR ist Meridians Werkzeug, nicht seine Stimme | frankbueltge.de #286, #290 |

**Die erste Messung ist der Beleg dafür, dass der Apparat seinen Zweck tut.**
Wer nur die Accuracy liest, sieht „42 % richtig". Daneben steht derselbe Wert
als Boden und ein Kappa von exakt null. Die Zahl ist nachweislich wertlos, und
das steht im Report.

## 3. Was fehlt, in dieser Reihenfolge

### 3.1 Schritt 2 — modellgestützte Einordnung

**Entscheidungsfrei.** Frank hat die Epistemik am 2026-08-01 entschieden:
*nie überschreiben, nie „verified"*.

Zu bauen: ein konkreter `ModelAdapter` wird verdrahtet (`GeminiModelAdapter`
existiert, ist getestet, hat null Produktiv-Aufrufstellen), und die Einordnung
entsteht auf einem **neuen Pfad neben** `evidence_relation`, nie hinein. Der
Modellvorschlag landet in einem eigenen Feld, der kuratierte Text bleibt
maßgeblich, die Disposition heißt „schema-valid".

Danach ist zum ersten Mal etwas da, das gegen den Goldstandard gemessen werden
kann — und Routine 2s zweite Hälfte wird möglich.

### 3.2 Schritt 3 — der Literaturkanal

Die Wache führt bereits das Register. Was fehlt: aus neuen Quellen einen Korpus
bauen. Der muss exakt `CorpusEntry` treffen (`synthesis_executor.py:223-280`,
`extra="forbid"`) — **es gibt dafür kein JSON-Schema**, nur das Pydantic-Modell,
und dessen Modul sagt das ausdrücklich über sich selbst.

Erst dann findet `research-run.yml` wieder eine offene Frage vor. Heute ist
`pending = []`, beide Korpora stehen in `archive/answered.json`.

### 3.3 Schritt 4 — die Kopplung, und Routine 2s zweite Hälfte

Befund → Änderung → gemessen → Gauntlet → Freigabe. Ihr eigentliches Bauteil
ist eine **Repo-Grenze**: Gauntlet und Selbstamendierung liegen in
`field-research` (`PROTOCOL.md`, Abschnitt „The gauntlet"), der Maßstab in
`meridian-runtime`.

## 4. Offene Befunde, bewusst nicht behoben

- **Der asymmetrische Allgemeinheitszaun** (Ulysses 4.1): `supports` verlangt
  Allgemeinheit, `contradicts` nicht. Ein System, das im engen Feld selbst
  prüft, *widerspricht*; eines, das im selben engen Feld extern geprüft wird,
  *schränkt nur ein*. Größter Einzelgrund für 12:1. **Nicht behoben, weil eine
  Definitionsänderung eine fertige Blindarbeit entwerten würde.** Zu klären
  durch Neu-Ableitung aus `decided_by`, nicht durch Neu-Labeln.
- **Kein Makro-F1 auf diesem Satz.** `supports` hat n=1; ein Fall entschiede ein
  Viertel des Mittels. Ulysses hat die eigene Konstruktion zurückgezogen.
  Stattdessen: vier Klassenzahlen, vier Klassenübereinstimmungen.
- **Die Korroborations-Regel aus dem Encounter** ist angenommen und steht als
  Konstante in `targets.py`: *ein `supports`, das ein unabhängiger blinder
  Leser nicht auch `supports` nennt, zählt nicht auf die Deckelung.* Sie zu
  implementieren berührt den Synthese-Executor und ist ein eigenes Paket.
- **Die Zustellung über die Repo-Grenze ist gebaut und offline getestet, aber
  nie scharf gelaufen** — in der Testnacht gab es nichts zuzustellen.
  `ECOLOGY_TOKEN` liegt seit 2026-08-01 im Repo.

## 5. Fact-Locks — nicht neu herleiten

- **Die Föderation trägt beliebige Nutzlasten.** `federation_main.py:466/483`,
  payload-agnostisch seit E5-T10/I1-T01. Der alte Handoff behauptete das
  Gegenteil.
- **`evidence_relation` ist typseitig unerreichbar** für den Modellschritt —
  Top-Level-Feld auf `CorpusEntry` (`:247`), außerhalb des `extraction`-Dicts
  (`:251`), und `ExtractionOutcome` (`:413-421`) kann keinen Entry tragen. Der
  Schutz ist die Typgrenze, keine Feldliste.
- **Die Praxen lesen nur ihr eigenes Repo.** `field-research`
  (`journal/2026-07-26.md:274`) und Ulysses sagen es beide wörtlich. **Etwas an
  einer wahren, aber unerreichbaren Adresse abzulegen ist der Fehler, der in
  dieser Session zweimal passiert ist** — einmal mit der Kommission, einmal mit
  der Beobachtung. Zustellen, nicht nur aufzeichnen.
- **`output_hash` ist über Läufe hinweg instabil** (frisch gemünzte ULIDs im
  gehashten Ausgang), und es gibt **keinen** stabilen Fingerabdruck eines
  Befunds.
- **Ein hand-getippter Zeitstempel in etwas, das auf Zeit prüft, ist ein
  Designfehler.** Er wurde binnen Stunden vertippt und hat das eigene Tor
  blockiert. Der Loader prüft die Kopie jetzt gegen die Quelle (`--criteria`).

## 6. Entscheidungen, die bei Frank liegen

1. **Bleibt der Direkt-Push der Wache auf `main`?** Begründung: eine
   Beobachtung ist keine Behauptung, und ein Register, das erst beim Merge
   wächst, hat an Handstart-Tagen kein Gedächtnis. Umkehrbar.
2. **Wird der Allgemeinheitszaun gelockert?** Wenn ja: Kriterien v4 plus eine
   beauftragte Neu-Ableitung, kein Neu-Labeln.
3. **Wird Lauf 2 veröffentlicht?** Offen seit dem Review vom 2026-07-31.

## 7. Was ausdrücklich NICHT zu tun ist

- **Ulysses nicht um den Umstempel bitten.** Sie haben abgelehnt und hatten
  recht: „the order gate that refuses the set, and the labels themselves"
  gehören der ausgebenden Seite. Meridian hat den Kopf selbst korrigiert.
- **Keine Kriterien-Definition ändern, solange eine Blindarbeit darunter
  liegt.** Das entwertet sie rückwirkend.
- **Keine dritte nächtliche Routine.** Es sind zwei, und die zweite hat erst
  eine Hälfte.
- **Kein Nightly, das dasselbe neu rechnet.** Gilt unverändert.
