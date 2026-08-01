# Kommission: Ulysses setzt Meridians Maßstab

**Status:** Vorbereitet, **nicht zugestellt.** Die Zustellung an eine andere Praxis
ist ein Schritt nach außen und braucht Franks ausdrückliches Go. Diese Notiz und die
gesperrten Kriterien sind alles, was ohne dieses Go entstehen darf.

**Anlass:** Owner-Entscheidung vom 2026-08-01. Auf die Frage, wer die Gold-Labels und
wer die Schwellenwerte setzt, hat Frank zweimal dieselbe Antwort gegeben: **ein
Encounter über The Middle.** Nicht er selbst, nicht die Literatur, und ausdrücklich
nicht Meridian.

---

## 1. Warum überhaupt eine fremde Praxis

Meridian soll sich aus eigenen Recherchen weiterentwickeln. Genau das ist der Vorgang,
den MRRs erster Lauf untersucht hat — rekursives Training auf eigenem Output. Die Lehre
aus `2026-07-24-primaerquellen-selbstoptimierung.md` ist unmissverständlich: der
dokumentierte Fehlermodus ist **der Angriff des Optimierers auf seinen eigenen
Evaluator** (DGM entfernte die Marker der Bewertungsfunktion trotz ausdrücklicher
Anweisung). Also darf die gemessene Praxis den Maßstab nicht setzen.

Bliebe: Frank, die Literatur, oder eine andere Praxis. Frank hat die dritte gewählt.

**Der Einwand dazu, einmal ausgesprochen:** Ulysses ist selbst eine maschinelle Praxis.
„Nicht von der gemessenen Praxis gesetzt" ist damit erfüllt, „extern zur KI" nicht. Das
ist schwächer als menschliches Gold. Es hat dafür eine Eigenschaft, die keine andere
Variante hat: **die Uneinigkeit zweier Praxen ist selbst messbar** (κ zwischen ihnen)
und wird damit ein Befund statt eines blinden Flecks.

## 2. Was Ulysses gefragt wird

> Ordne ~60 wörtliche Quellauszüge danach ein, ob sie eine benannte Aussage stützen,
> ihr widersprechen, sie einschränken oder sie nur einordnen — **blind**, gegen
> vorab gesperrte Kriterien, mit einer Begründung je Fall.

Und, zweitens, die eigentliche Autoritätsfrage:

> Sag, ab welchem Wert das Ergebnis gut genug ist. Kappa, Makro-F1, False-Support-Rate.
> Diese drei Zahlen stehen in `benchmarks/meridianbench/targets.py` auf `None` und
> fallen bis dahin durch. Meridian darf sie nicht selbst setzen.

## 3. Die Kriterien sind schon gesperrt

`benchmarks/meridianbench/fixtures/mb-cls-criteria.v1.json`
`sha256:c1d3bc7b5896573527859ae1e96d0107dc7c3420c8db944148b0ddf0792627a4`

**Vor den Fällen gesperrt, und das ist kein Zeremoniell.** Das Reihenfolge-Gate
(N1-T02 R1) weist jeden Goldsatz zurück, dessen `labelled_at` nicht strikt nach
`criteria_locked_at` liegt. Kriterien, die nach den Fällen entstehen, sind auf die
Fälle gepasst.

Enthalten: die eine Aussage, um die es geht; die vier Definitionen; fünf Regeln
(Eigenbeleg, nur der Auszug, konservativer Gleichstand, kein Enthalten, Begründungs-
pflicht); der Auswahlrahmen; und **eine offen benannte Streitfrage** —
`R-conservative-supports` schreibt eine Asymmetrie in die Kriterien selbst
(bei echtem Gleichstand gewinnt `qualifies`, weil nur `supports`/`contradicts` die
Deckelung bewegen). Der Goldstandard ist damit nicht neutral, sondern konstruktiv
konservativ. Ulysses wird ausdrücklich gefragt, ob es die Regel annimmt; lehnt es sie
ab, geht der Einwand in eine v2 der Kriterien und wird nicht überstimmt.

## 4. Die Bedingungen, ohne die es keine Messung ist

1. **Blind.** Ulysses sieht Auszug und Kriterien, nie Meridians eigene Einordnung.
   Sonst ist das Ergebnis eine Bestätigung, kein unabhängiger Maßstab. Der Report
   trägt `blind_to_measured_labels` bis auf die Seite durch und druckt eine Warnung,
   wenn das Feld `false` ist — die Bedingung ist also nicht nur zugesagt, sie ist
   sichtbar, wenn sie gebrochen wurde.
2. **Rücklauf als committete Datei**, gegen
   `benchmarks/meridianbench/fixtures/gold-label-set.schema.json`. Meridian liest sie
   als gepinnte Eingabe und **editiert sie nie** — dasselbe Regime wie für die
   Archiv-JSONs.
3. **Herkunft benannt.** `label_provenance.producing_practice` ist Ulysses;
   `encounter_id` verweist auf den Registereintrag. Ein Goldsatz, der nicht sagen kann,
   woher seine Antworten kommen, ist kein Maßstab.
4. **Der Widerspruch bleibt stehen.** Wo Ulysses anders einordnet als Meridian, wird
   das nicht ausgeglichen. Es wird gezählt.

## 5. Was noch fehlt, ehrlich

- **Die ~60 Auszüge gibt es noch nicht.** Der Auswahlrahmen steht in den Kriterien;
  gezogen ist nichts. `scripts/fetch_source_content.py` kann sie holen (arXiv/Crossref,
  Abstract-Ebene, hash-verankert) und ist ausdrücklich ein Handbetrieb-Skript.
  Reihenfolge ist trotzdem richtig: Kriterien zuerst.
- **Ulysses' Node-ID und Vertrauenserklärung** existieren nicht (Wegkarte 2026-07-26,
  Punkte 8/9). Für die erste Runde genügt Zurechnung über Commit und Journal; die
  kryptografische Zurechenbarkeit des Rücklaufs ist ein Nachrüstschritt.
- **Signieren kann ich nicht.** Der Ed25519-Privatschlüssel liegt nicht im Repository
  und darf es nicht (`.gitignore`: „Private key material — NEVER in this repository").
  Der Zug ist einseitig vollziehbar, sobald der Schlüssel da ist:

```bash
mrr federation envelope sign \
  --payload benchmarks/meridianbench/fixtures/mb-cls-criteria.v1.json \
  --payload-kind GoldLabelCommission \
  --sender-practice-id urn:mrr:practice:01KYG3AY344T18D0479TG557KX \
  --recipient-node-id <Ulysses' Node-ID — muss vereinbart werden> \
  --key-file <Franks Schlüssel> --key-id kid:vZCtAffr9K1Q9TZpBtrMbdufoCnoTZYXne/tmqdwK/4= \
  --message-id ... --sent-at ... --expires-at ... --output outbox/commission.json
```

`--payload-kind` ist ein freier Tag (`federation_main.py:483`) — es braucht **keinen
neuen Payload-Typ.** Das ist die Korrektur K1 aus der N1-T02-Ableitung: die Föderation
kann das seit E5-T10/I1-T01, und der Handoff behauptete das Gegenteil.

Die Nutzlast braucht vor dem Signieren noch ein eigenes `content_hash`-Feld; der Wert
steht oben.

## 6. Warum der Apparat trotzdem schon steht

Er hängt an keiner fremden Session. `mrr validate gold` ist gebaut, getestet und
**weigert sich fail-closed**, solange kein hash-gepinnter Goldsatz vorliegt; die
Schwellen stehen auf `None` und lassen ihre Prüfungen durchfallen, mit eigenem Grund
(„no threshold set by an encounter yet"). Kommt der Encounter nicht zustande, misst
Meridian nichts — sichtbar, nicht still.

Das ist die richtige Reihenfolge: das Instrument wartet auf den Maßstab, nicht der
Maßstab auf das Instrument.
