# E5-T10-Ableitung: die Envelope-Kante — vom Objekt ins signierte Envelope (2026-07-26)

**Status:** Entschieden — der Owner hat am 2026-07-26 nach der Wegkarte
(`2026-07-26-wegkarte-erster-ecology-austausch.md`) Punkt ① als nächstes Paket
freigegeben. Governance-Commit vor dem Bau; Merge nach `main` bleibt an eine
ausdrückliche Owner-Freigabe gebunden.

**Der Anlass ist ein belegter Defekt, kein Spezifikationspunkt:** zwischen „ich habe
einen Payload" und „ich habe ein signiertes Envelope" existiert kein Weg. Die
Föderation kann heute **genau einen Payload-Typ** tragen, und den nur von innerhalb
eines Services.

## Fact-Lock (erstverifiziert am Code)

- `mrr federation` kennt **zwei** Unterbefehle: `outbox`, `inbox`. Kein `envelope`.
- `outbox write --envelope` erwartet laut eigener Hilfe „a path to an **already-signed**
  NodeMessageEnvelope JSON file"; `federation_main.py:199` heißt `_load_envelope`.
- `build_outbox_bundle(envelopes: Sequence[NodeMessageEnvelope], …)` setzt signierte
  Envelopes voraus.
- Die **einzige** Stelle, die eines baut, ist `_build_and_sign_envelope` — eine
  **private Methode** in `services/control_plane/mrr/services/correction/service.py`,
  deren Signatur eine `CorrectionNotification` verlangt.
- Der einzige real vorkommende `payload_kind` ist `"CorrectionNotification"`.
- **Auch Corrections haben kein CLI-Kommando.**
- Der gedachte erste Inhalt ist tragfähig: `verification-ulysses-hammond.json` ist ein
  `VerificationResult` mit eigenem `content_hash`
  (`sha256:ba90ee1821e241e3a…`), 23 Felder, seit dem 22. Juli im Archiv.

## Die tragende Einsicht: der Absender muss verweigern, was der Empfänger verwirft

`validate_inbound_envelope` prüft in Bedingung 3 — wörtlich aus dem Modul-Docstring:

> the carried payload's own `content_hash` (`envelope.payload.get("content_hash")`)
> equals `envelope.payload_content_hash` … This is a **CONSISTENCY check** …, **not an
> independent recomputation** from payload bytes — this module is payload-agnostic and
> has no opinion on any specific payload kind's own hashing policy.

Daraus folgt zwingend, wie die neue Funktion `payload_content_hash` bestimmt:

- **Nicht berechnen.** Eine Neuberechnung könnte von dem abweichen, was das Objekt
  selbst trägt, und der Empfänger vergleicht gegen das Getragene.
- **Nicht vom Aufrufer nehmen.** Ein freier Parameter erlaubte ein Envelope, das der
  Empfänger garantiert verwirft — oder schlimmer, eines, das in sich unstimmig ist.
- **Aus dem Payload lesen:** `payload["content_hash"]`.

Und daraus die Ehrlichkeits-Regel dieses Pakets: **ein Payload ohne eigenen
`content_hash` ist eine typisierte Verweigerung beim Bauen.** Ein solches Envelope
könnte Bedingung 3 nie bestehen. Es zu bauen hieße, etwas auszuliefern, von dem man
weiß, dass es zurückgewiesen wird. Der Absender verweigert also genau das, was der
Empfänger verwerfen würde — dieselbe Regel auf beiden Seiten, einmal als Bau-, einmal
als Annahmebedingung.

## Architektur: der exakte Zwilling des prüfenden Moduls

`packages/domain/mrr/domain/envelope_signing.py` — Geschwistermodul zu
`envelope_validation.py`, dessen Bauart es genau spiegelt:

- **Rein und payload-agnostisch.** Kein Import von `CorrectionNotification`, kein
  geschlossener Satz erlaubter `payload_kind`-Werte. Der prüfende Zwilling hat
  „no opinion on any specific payload kind" — der signierende ebenso.
- **Der Import von `mrr.contracts.node_message_envelope` ist erlaubt** und hier kein
  neuer Zyklus: `envelope_validation.py:104` tut genau dasselbe. Präzedenz im
  Geschwistermodul, nicht neu erfunden.
- **Alle Identitäten, Zeiten und der Schlüssel kommen vom Aufrufer** — wie bei
  `build_outbox_bundle`. Auch `message_id`: gleiche Eingaben, gleiches Envelope.
  Keine Uhr, kein Zufall im Modul.

Das Signaturverfahren wird **wörtlich** von `_build_and_sign_envelope` übernommen,
nicht neu erfunden: Entwurf mit Platzhalter-Signatur → `model_dump_json(exclude_none=
True)` → `sign_object` über diesen Körper → Signatur ersetzen → erneut validieren.
Verallgemeinert werden ausschließlich die drei correction-spezifischen Stellen
(`payload_kind`, `payload_content_hash`, `payload`), die zu Parametern bzw. zum
Payload-Lesen werden.

`services/control_plane/mrr/services/cli/federation_main.py` + 2 Zeilen in `main.py`
→ **`mrr federation envelope sign`**.

## Ausdrücklich NICHT in E5-T10

**Der Correction-Service wird nicht umgebaut.** Er hat einen funktionierenden,
getesteten Pfad; ihn auf die neue Funktion umzuziehen wäre eine Änderung an fremden,
grünen Interna ohne Anlass. Die entstehende Doppelung ist real und wird hier benannt
statt versteckt — eine DRY-Konsolidierung ist ein eigenes Paket, wenn sie je gebraucht
wird (Präzedenz: E9-T00b).

Kein Netz, keine DB, kein Modell, kein neuer Dependency, keine Migration, kein
`schemas/**`. **Keine erfundene Identität** — weder eine Praxis noch ein Schlüssel für
Meridian, und erst recht keine für Ulysses. Das Paket baut die Kante; es fährt keinen
Austausch. **Keine Änderung** an `envelope_validation.py`, `offline_bundle.py` oder
irgendeinem committeten Korpus-Artefakt.

## Akzeptanz-Orakel (VOR dem Bau festgelegt)

Der scharfe Fall ist der **vollständige Durchlauf mit dem realen Hammond-Dissens**,
gegen die **unveränderten** Empfängerfunktionen:

1. `verification-ulysses-hammond.json` (`content_hash: sha256:ba90ee18…`) →
   signiertes Envelope → `build_outbox_bundle` → Datei → zurückgelesen →
   `validate_inbound_bundle` → `validate_inbound_envelope`: **akzeptiert**, wenn der
   Empfänger den Signaturschlüssel als vertrauenswürdig führt. Testschlüssel genügen.
   **Das wäre der erste Beleg überhaupt, dass ein reales Archiv-Objekt den ganzen
   Föderationsweg zurücklegen kann.**
2. Payload **ohne** `content_hash` → typisierte Verweigerung beim Bauen, kein Envelope.
3. `payload_content_hash` nachträglich verfälscht → der Empfänger scheitert an
   Bedingung 3 (`EnvelopePayloadContentHashMismatchError`). Beweist, dass die
   Konsistenzprüfung nicht durch das neue Modul aufgeweicht wurde.
4. Ein Byte im Envelope-Inhalt gekippt → `SignatureVerificationError`.
5. Reproduzierbarkeit: gleiche Eingaben inklusive `message_id` und `sent_at` →
   bytegleiches Envelope.
6. `payload_kind` bleibt frei — ein Test führt einen zweiten, anderen Payload-Typ
   durch dieselbe Funktion, damit die Payload-Agnostik nicht nur behauptet ist.

## Was dieses Paket ausdrücklich nicht erreicht

Es macht **keinen Austausch**. Nach ihm fehlen weiter: Meridians Schlüssel (Franks
Handgriff), die Praxis-Identität, die die öffentliche Hälfte veröffentlicht, die
vereinbarte Empfänger-ID, und Ulysses' Vertrauenserklärung in eigener Session.

Es macht auch **keine Forschung**. Ein Austausch über einen bestehenden Dissens ist
ein realer Vorgang in der Ecology, aber kein neuer Forschungslauf. Die Frage nach dem
Forschungsgegenstand bleibt offen.

**Aber:** nach diesem Paket hängt Meridians eigener Zug an nichts Gebautem mehr —
`build_outbox_bundle` nimmt alle Identitäten und den Schlüssel vom Aufrufer und
braucht **keinen** Schlüssel der Gegenseite.
