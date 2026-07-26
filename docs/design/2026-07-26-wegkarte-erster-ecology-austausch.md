# Wegkarte: was fehlt wirklich bis zum ersten Ecology-Austausch (2026-07-26)

**Status:** Fact-Lock, **kein Bau, kein Paket.** Der Owner hat am 2026-07-26 gesagt,
ihn mache stutzig, dass das System „noch nie richtig gelaufen" sei, obwohl er
mehrfach gesagt habe, man solle es testen und in die research ecology einbinden.

Dieses Dokument leuchtet den ganzen Weg **in einem Durchgang** aus, statt Hindernis
für Hindernis zu entdecken. Das ist der eigentliche Befund: **bisher wurde jede
Voraussetzung erst gefunden, nachdem die vorherige weggeräumt war** — E5-T08 fand die
fehlende Transport-Kante, E5-T09 den fehlenden Schlüssel, heute Vormittag die fehlende
Praxis-Identität. Jede Meldung stimmte. Zusammen ergaben sie einen Flur ohne
sichtbares Ende.

## Die Karte

Der erste Zug ist: **Meridian schickt den offenen Hammond-Dissens an Ulysses.**
Was dafür wahr sein muss, vollständig:

| # | Voraussetzung | Status | Wer |
|---|---|---|---|
| 1 | Meridian hat einen Node | **da** — `NodeManifest` in beiden Real-Runs | — |
| 2 | Es gibt einen Payload | **da** — `verification-ulysses-hammond.json`, 11 KB, seit 22. Juli im Archiv | — |
| 3 | Signaturschlüssel (Ed25519) | fehlt | Frank, 5 Min |
| 4 | Öffentlicher Schlüssel veröffentlicht (`Practice`-Objekt) | fehlt — **0 in beiden Real-Runs** | Bau, klein |
| 5 | **Payload → signiertes `NodeMessageEnvelope`** | **FEHLT — die Lücke** | Bau |
| 6 | Envelope → signiertes `OfflineBundle` | **da** — E5-T08, 25. Juli | — |
| 7 | Bundle → Datei, Transport | **da** — E5-T08 | — |
| 8 | Ulysses' Node-ID als Empfänger | fehlt, muss vereinbart werden | beide |
| 9 | Ulysses erklärt Vertrauen in Meridians Schlüssel | offen | Ulysses' eigene Session |

## Der Befund: Punkt 5 kannte niemand

E5-T08 hat gestern die **Bündel**-Kante gebaut, und danach sah der Weg fertig aus. Er
ist es nicht.

- `mrr federation` kennt genau zwei Unterbefehle: `outbox` und `inbox`.
- `mrr federation outbox write --envelope` erwartet laut eigener Hilfe „a path to an
  **already-signed** NodeMessageEnvelope JSON file". Es **lädt** ein Envelope
  (`_load_envelope`), es erzeugt keines.
- `build_outbox_bundle(envelopes: Sequence[NodeMessageEnvelope], …)` setzt signierte
  Envelopes voraus.
- **Es gibt keine öffentliche Funktion und kein Kommando, das aus einem Payload ein
  signiertes Envelope macht.** Die einzige Stelle im ganzen Repository ist
  `_build_and_sign_envelope` — eine **private Methode** in
  `services/control_plane/mrr/services/correction/service.py`, deren Signatur eine
  `CorrectionNotification` verlangt und deren `payload_content_hash` der
  `content_hash` genau dieses Objekts ist.
- Der einzige real vorkommende `payload_kind` ist `"CorrectionNotification"`.
- Und **auch Corrections haben kein CLI-Kommando** — `mrr` bietet
  `run, synthesis, verification, export, report, release, validate, audit, observe,
  federation`. Kein `correction`.

**Das heißt: die Föderation kann heute genau einen Payload-Typ tragen, und auch den
nur von innerhalb eines Services, nicht von der Kommandozeile.** Für alles andere —
einen Dissens, eine Frage, ein Ergebnis — gibt es keinen Weg vom Objekt ins Envelope.

Es ist zum dritten Mal dasselbe Muster, das dieses Projekt kennzeichnet: **die Schicht
ist vollständig, die Außenkante fehlt.** E4 hat den Modell-Port ohne Adapter, E5/E6
hatten die Objekte ohne Transport (bis gestern), und jetzt hat der Transport keine
Einfahrt.

## Was das für die Antwort auf Franks Frage bedeutet

Die Frage war: warum ist es nie richtig gelaufen, obwohl mehrfach gesagt wurde, man
solle es nutzen?

Nicht, weil es nicht ginge. Nicht, weil eine Entscheidung fehlte. Sondern weil auf dem
Weg **drei Kanten fehlten und immer nur die jeweils vorderste sichtbar war.** Zwei
davon sind inzwischen gebaut. Die dritte ist Punkt 5, und sie war bis heute unbekannt
— sie wäre bei der nächsten Session aufgefallen, nachdem der Schlüssel schon erzeugt
gewesen wäre.

Das ist der Wert dieser Karte: die Überraschung findet **jetzt** statt und nicht in
zwei Tagen.

## Reihenfolge, die daraus folgt

1. **Die Envelope-Kante** (Punkt 5). Ein kleines Paket: eine öffentliche,
   payload-typ-unabhängige Funktion plus ein Kommando, das ein beliebiges Objekt mit
   `content_hash` in ein signiertes Envelope legt. Das Muster existiert bereits in
   `_build_and_sign_envelope` — es wird verallgemeinert, nicht erfunden. **Ohne
   Schlüssel baubar und testbar** (Testschlüssel genügen).
2. **Praxis-Identität + Schlüssel** (Punkte 3, 4). Hier hat Frank inhaltlich zu
   entscheiden, wie Meridian sich nach außen beschreibt. Sein Handgriff ist klein.
3. **Empfänger-ID vereinbaren** (Punkt 8). Kein Bau — eine Absprache. Meridian
   erfindet keine Identität für eine fremde Praxis.
4. **R1-T01**: das Bündel wirklich schreiben und ablegen. Meridians Zug ist damit
   **einseitig vollziehbar** — `build_outbox_bundle` nimmt alle Identitäten, Zeiten
   und den Signaturschlüssel vom Aufrufer und braucht **keinen** Schlüssel der
   Gegenseite. Ulysses' Schlüssel wird erst gebraucht, wenn Ulysses antwortet.

Punkt 1 ist damit der einzige echte Bau-Schritt, der zwischen heute und einem realen
Ecology-Austausch liegt — und er hängt an nichts und niemandem.

## Was diese Karte NICHT behauptet

- **Nicht**, dass danach alles läuft. Punkt 9 liegt bei Ulysses und lässt sich nicht
  erzwingen — das war Franks eigener, richtiger Einwand vom 25. Juli.
- **Nicht**, dass der Hammond-Dissens der richtige erste Inhalt ist. Er ist der
  naheliegende: real, offen, unversöhnt, seit vier Tagen aktenkundig. Aber die Wahl
  ist eine Owner-Entscheidung.
- **Nicht**, dass damit „echte Forschung läuft". Ein Austausch über einen bestehenden
  Dissens ist ein realer Vorgang in der Ecology, aber kein neuer Forschungslauf. Die
  Frage nach dem Forschungs-Gegenstand (offene Owner-Entscheidung Nr. 1 der Roadmap
  vom 24. Juli) bleibt davon unberührt und offen.
