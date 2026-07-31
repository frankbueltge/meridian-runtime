# Ableitung I1-T01 — die Korrektur bekommt ihre Kommandozeile

**Status:** Fact-Lock + Ableitung. **Kein Bau.** Governance-Commit vor der Implementierung,
nach dem Ritual dieses Repositoriums.

**Anlass:** Der Owner hat am 2026-07-31 eine strategische Neueinordnung angefordert
(„MRR Recovery, Integration and Product Direction Review"). Das Ergebnis liegt in
`2026-07-31-mrr-review-und-integrationsrichtung.md`. Diese Ableitung setzt dessen
erste vertikale Scheibe um.

---

## 1. Was der Fact-Lock ergeben hat

Vier Befunde an echten Dateien, in dieser Reihenfolge gefunden. Zwei davon haben die
Aufgabe **korrigiert**, einer hat den Zuschnitt entschieden.

### Befund 1 — `CorrectionImpactService` ist vollständig und von nichts erreichbar

`services/control_plane/mrr/services/correction/service.py` führt sieben öffentliche
Operationen: `record`, `propagate_impact`, `notify_affected_practices`,
`receive_correction_notification`, `record_response`, `open_pending_delivery`,
`retry_pending_delivery_online`. Vollständig implementiert, in Unit- und
Integrationsebene getestet.

`mrr --help` listet elf Kommandos: `run, synthesis, verification, export, report,
release, validate, audit, observe, federation, practice`. **Kein `correction`.**

Der Lebenszyklus ist damit nur von innerhalb eines Services erreichbar — genau wie die
Wegkarte vom 2026-07-26 („was fehlt wirklich bis zum ersten Ecology-Austausch")
festgehalten hat.

### Befund 2 — der Modellarm kann nicht klassifizieren (Korrektur am Review selbst)

Eine erste Fassung des Reviews behauptete, der Goldstandard für einen Modellvergleich
liege im Korpus bereits vor, „Sprosse 1 und 2" der Leiter fielen zu einem Paket
zusammen. **Am Code widerlegt:**

- `corpora/model-collapse/method-protocol.proposal.json` deklariert
  `extraction_fields: ['claim_relevant_finding', 'classification_basis']` — zwei
  **Prosafelder**.
- `synthesis_executor.py:710/713` teilt Belege nach `row.entry.evidence_relation` auf.
  Dieser Wert stammt aus dem Korpus; der optionale Modellschritt berührt ihn nie.

Der gebaute Modellschritt schlägt Begründungstext vor, keine Einordnung. Architektonisch
ist das vorbildlich — „never sole judges" ist hier per Konstruktion erzwungen, nicht per
Regel. Aber ein *messbarer* Klassifikationsvergleich ist durch Verdrahtung nicht
erreichbar; er bleibt eine echte neue Fähigkeit. Die Leiter hatte recht.

**Nebenbefund, als Spezifikationsfrage vermerkt, nicht hier entschieden:** Bei
`status == "proposal"` überschreibt `build_model_assisted_extraction_callable` die
kuratierte Begründung des Menschen (`synthesis_executor.py:609`) und markiert das rohe
Modellergebnis als `verification_disposition="verified"`. Das ist eine Entscheidung über
Epistemik und gehört in ein eigenes Paket mit Owner-Vorlage.

### Befund 3 — die Grundsatzentscheidung zum Modellgebrauch ist schon getroffen

`practices/meridian.json` (owner-gesetzter Inhalt, E5-T11, signiert):

> „Where models are used, they are declared, hashed and logged tools of single steps —
> never orchestrators, never sole judges."

Die Frage „Modellschritt ja/nein" braucht keine neue Owner-Entscheidung. Offen ist nur
die engere Frage aus Befund 2.

### Befund 4 — `EnvelopeTransport` ist ein Port mit null Implementierungen

Dies hat den Zuschnitt dieses Pakets entschieden.

`packages/domain/mrr/domain/envelope_transport.py:95`, Docstring wörtlich:

> „No concrete implementation exists in this module or anywhere under
> `packages/`/`adapters/` yet — the real mTLS client/server is infra-dependent and
> explicitly deferred. **Tests use only an in-test fake** implementing this Protocol."

Gegengeprüft: jeder Treffer auf `EnvelopeTransport` außerhalb der Domäne und des
Correction-Services liegt in `tests/`. `LocalFilesystemBundleTransport`
(`adapters/federation/.../local.py:194`) erfüllt das Protokoll **nicht** — es trägt
`write_bundle(bundle, path)`, kein `send(request)`. Es ist ein Bündel-, kein
Envelope-Transport.

`notify_affected_practices` verlangt aber `transport: EnvelopeTransport`. **Der
Korrekturversand ist deshalb durch reine Verdrahtung nicht erreichbar.**

---

## 2. Das Muster, zum vierten Mal

| # | Schicht | Fehlende Außenkante | Geschlossen |
|---|---|---|---|
| 1 | `ModelAdapter` | kein konkreter Provider-Adapter | E4-T08, 2026-07-26 |
| 2 | Föderationsobjekte | kein Transport | E5-T08, 2026-07-25 |
| 3 | Bündel-Transport | keine Einfahrt (Envelope-Bau) | E5-T10, 2026-07-26 |
| 4 | **`EnvelopeTransport`** | **keine Implementierung** | offen — dieses Paket |

Der Mechanismus ist benannt: `AGENTS.md` Regel 2 erlaubt ein approved Paket zur Zeit,
Regel 3 verbietet Domänenverhalten, das nicht in der Spezifikation steht.
Kompositionsarbeit ist keins von beidem und konnte deshalb nie ein Paket werden.

## 3. Die Zuschnitt-Entscheidung

Zwei Fassungen waren möglich:

**Minimal.** Nur `record` + `impact` durchreichen, null neuer Code außer CLI. Reine
Integration, sauber — aber die Scheibe endet **wieder einen Schritt vor der Außenwelt**.
Kein Bündel, kein Adressat, kein Vollzug.

**Vollständig.** Zusätzlich der eine fehlende konkrete Transport, damit `notify` läuft
und ein versandfertiges Bündel entsteht.

**Gewählt: vollständig** — mit ausdrücklicher Deklaration, dass dieses Paket **einen
Adapter für einen bereits deklarierten Port hinzufügt**, genau wie E4-T08 es tat. Das ist
keine neue allgemeine Fähigkeit und keine neue Domänenlogik. Die Begründung ist die
Diagnose selbst: Ein Paket, das erneut vor der Außenkante endet, reproduziert den Fehler,
den es beheben soll.

Der Transport ist offline und klein: Er schreibt das Envelope in ein Bündel und meldet
`delivered`, wenn die Bytes liegen. Kein Netz, kein mTLS — der bleibt weiter vertagt.

## 4. Was ausdrücklich draußen bleibt

- `open_pending_delivery`, `retry_pending_delivery_online` — sie berühren Netz.
- `receive_correction_notification`, `record_response` — Empfängerseite; das ist Ulysses'
  Zug, nicht Meridians.
- Jede Änderung an `CorrectionImpactService` selbst.
- Der Modellarm (Befund 2/3) — eigenes Paket, eigene Owner-Vorlage.
- Netzpolitik-Durchsetzung. Sie bleibt verzeichnet, nicht erzwungen; die Frage wird erst
  mit einem verdrahteten Modellschritt real, nicht mit einem Offline-Transport.

## 5. Warum diese Scheibe zuerst

`CorrectionNotification` ist laut Wegkarte **der einzige `payload_kind`, der real
vorkommt** — die Föderation kann heute genau einen Payload-Typ tragen. Ein
Korrektur-Kommando ist damit gleichzeitig die fehlende Einfahrt zu dem einen
Transportweg, der schon belegt funktioniert (E5-T08/T10, realer Hammond-Dissens
durchgelaufen, von unveränderten Empfängerfunktionen angenommen).

Nach diesem Paket ist der erste Ecology-Austausch **kein Bau mehr, sondern ein
Vollzug** — und einseitig vollziehbar: `build_outbox_bundle` nimmt alle Identitäten und
den Signaturschlüssel vom Aufrufer und braucht keinen Schlüssel der Gegenseite.

Meridians Identität existiert (`practices/meridian.json`,
`urn:mrr:practice:01KYG3AY344T18D0479TG557KX`, gültig bis 2027-07-26).

## 6. Berührung mit der Ökologie

Die Site hat am 2026-07-31 öffentlich zugesagt: *„Factual errors are corrected on the
record with a date rather than removed quietly."* Dieses Paket macht diese Zusage zum
ersten Mal maschinell vollziehbar statt nur redaktionell.

Die Unantastbarkeit committeter Archivstände bleibt gewahrt: Eine Korrektur erzeugt eine
**neue Revision**, sie überschreibt nichts.
