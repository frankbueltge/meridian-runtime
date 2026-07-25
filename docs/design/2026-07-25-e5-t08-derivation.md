# E5-T08-Ableitung: der erste konkrete Föderations-Adapter (Datei-Transport) — 2026-07-25

**Status:** Entschieden — der Owner (Frank) hat in Session vom 2026-07-25, nach der
Bestandsaufnahme des Gesamtprojekts, die Auswahl und Umsetzung des nächsten
sinnvollen Schritts ausdrücklich an die Session delegiert („alles was sinnvoll ist
und das Projekt voranbringt, kannst du gerne umsetzen"), nachdem er zur ersten
Joint Inquiry richtig eingewandt hatte, dass man sie nicht erzwingen kann. Dieses
Dokument fixiert den Fact-Lock, korrigiert die Diagnose und leitet **E5-T08** ab.
Governance-Commit vor dem Bau; der Merge nach `main` bleibt an eine ausdrückliche
Owner-Freigabe gebunden.

## Die Diagnose war ungenau — der Fact-Lock schärft sie

Die wiederkehrende Formulierung in den Records lautet „ungenutzte Föderation" und
legt nahe, es fehle ein **Anlass** oder ein **Partner**. Der Fact-Lock am Code
zeigt etwas anderes:

**E5/E6 haben in 17 Paketen die vollständige Objekt- UND Port-Schicht gebaut — und
keinen einzigen konkreten Adapter.** Das ist keine Nachlässigkeit, sondern war je
Paket ausdrücklich so entschieden und dokumentiert:

- `mrr.domain.envelope_transport` ist ein `Protocol` mit — wörtlich —
  „NO concrete implementation"; der reale mTLS-Transport ist als
  infra-abhängig deklariert und vertagt (E5-T03).
- E5-T06 klammert wörtlich aus: „the physical air-gap transfer medium
  (file/USB/media byte I/O) and real envelope encryption / KMS are marked
  infra-dependent and are NOT built or CI-tested in this packet".
- **Kein CLI-Kommando erreicht die Föderation.** `mrr` bietet heute
  `run, synthesis, verification, export, report, release, validate, audit,
  observe` — kein `federation`, kein `transfer`, kein `inbox`/`outbox`;
  `services/.../cli/` enthält kein entsprechendes Modul.

**Die Föderation ist also nicht ungenutzt, weil niemand sie benutzen wollte —
sie hat keine Außenkante.** Ein Bundle kann gebaut, signiert und geprüft werden,
aber es gibt keinen Weg, es aus dem Prozess heraus- oder hereinzubekommen. Das ist
die kleinste fehlende Sache zwischen 17 gebauten Paketen und einem realen Vorgang.

## Warum der Datei-Weg — und nicht mTLS

Der naheliegende Reflex wäre, den vertagten mTLS-Transport nachzubauen. Das wäre
hier falsch:

**Die beiden Praktiken tauschen tatsächlich über Git-Repositories aus, nicht über
Sockets.** Der bereits stattgefundene Vorgang belegt es: Am 2026-07-22 hat Ulysses
eine Meridian-Claim-Zeile geprüft und ihr widersprochen (der erhaltene
Hammond-Dissens). Getragen wurde das als — Wortlaut des Archivdokuments —
**„verbatim carriage"**: Text wurde aus Ulysses' `REQUESTS.md` und Journal
mechanisch herauskopiert und im MRR-Archiv abgelegt, „unabhängig von der künftigen
Historie des anderen Repos".

Für diese Ökologie ist der store-and-forward-Weg darum **kein minderwertiger
Ersatz für mTLS, sondern das zutreffende Medium.** Ein mTLS-Client/Server wäre ein
Bau für ein Deployment, das es nicht gibt — genau das, was die use-first-Doktrin
untersagt. Ein Datei-Adapter ist dagegen exakt die Maschinen-Fassung dessen, was
heute von Hand passiert.

Hinzu kommt ein bereits abgemachter Abnehmer: `research-ecology` hat am 2026-07-19
datiert entschieden (D-JI-03), **keine eigene Koordinations-/Transportschicht** für
Joint Inquiries zu bauen und auf meridian-runtimes E5/E6 zu setzen. Die Föderation
ist der designierte Rückgrat — bisher ohne Anschlussstück.

## Der Nutzungsanlass (real, heute erfüllbar)

**Der Hammond-Dissens ist offen und aktenkundig unversöhnt** — der Owner hat am
2026-07-25 entschieden, ihn zu erhalten statt zu adjudizieren. Zwei getrennt
konstituierte Praktiken widersprechen sich; der eine Austausch, der dazu
stattfand, lief von Hand. Der Anlass ist also nicht konstruiert, sondern liegt
seit drei Tagen auf dem Tisch.

**Was dieses Paket dafür tut und was ausdrücklich nicht:** Es baut die Kante, über
die ein solcher Austausch künftig maschinell laufen kann — und **führt den
Austausch nicht durch**. Warum nicht, siehe die Grenze unten.

## Die harte Grenze: Meridian darf keine Identität für Ulysses erfinden

Der Fact-Lock zeigt: **es existiert keinerlei Praxis- oder Node-Identität für
Ulysses** irgendwo im Laufzeit-Code (nur Prosa-Erwähnungen in den Atlas-Snapshots).
Ein realer Austausch bräuchte Ulysses' öffentlichen Schlüssel — und den kann
Meridian **nicht** ausstellen. Ein Schlüsselpaar für eine andere Praxis zu erzeugen
wäre die Fälschung genau der Unabhängigkeit, die das ganze System behauptet.

Der Schlüsselaustausch ist damit ein **Governance-Akt zwischen zwei Praktiken**, kein
Bau-Schritt — er braucht Ulysses' eigene Session. Das ist der Punkt, an dem Franks
Einwand („kann man nicht erzwingen") vollständig zutrifft: nicht beim Anlass, nicht
beim Partner, sondern **beim Vertrauensanker**.

## Die zentrale Ehrlichkeits-Unterscheidung: Transport ist nicht Vertrauen

Das E5-T08-Analogon zu N1s „Reliabilität ≠ Validität", N2-T01s „Existenz ≠
Bestätigung", R2-T01s „Beobachtung ≠ Optimierung" und N2-T02bs „Verankerung ≠
Belegkraft".

Ein akzeptiertes Bundle beweist: es wurde von einem Schlüssel signiert, den der
**Empfänger vorher als vertrauenswürdig deklariert hat**, es ist an diesen Knoten
adressiert, liegt in seinem Gültigkeitsfenster und wurde noch nie verarbeitet. Es
beweist **nicht**, dass der Absender in der Welt der ist, für den er sich ausgibt.
Die Vertrauensentscheidung bleibt beim Aufrufer — genau so, wie die bestehende
Funktion es mit ihrem Parameter `trusted_sender_practice_id` bereits modelliert.
Der Adapter darf diese Entscheidung nicht verstecken und nicht defaulten.

## Fact-Lock (erstverifiziert am realen Code)

- **`build_outbox_bundle(envelopes, *, bundle_id, bundle_nonce, sender_node_id,
  sender_practice_id, recipient_node_id, created_at, expires_at, signing_key,
  key_id, ...) -> OfflineBundle`** — rein, alle Identitäten/Zeiten/Nonces
  **vom Aufrufer**, also reproduzierbar: gleiche Eingaben, gleiches Bundle.
- **`validate_inbound_bundle(bundle, *, this_node_id, trusted_sender_practice_id,
  ring, already_processed, at=None) -> list[NodeMessageEnvelope]`** — fünf
  Akzeptanzbedingungen, je eigener typisierter Fehler
  (`BundleRecipientMismatchError`, `BundleNotWithinValidityWindowError`,
  `BundleAlreadyProcessedError`, `BundleSignerMismatchError`, `UnknownKeyIdError`,
  `BundleKeyNotValidError`, `SignatureVerificationError`) — **nie kollabiert**.
- **Entscheidend: `already_processed` ist ein vom Aufrufer geliefertes Prädikat.**
  Der Replay-Schutz braucht **keine Datenbank** — ein committetes JSON-Register
  erfüllt ihn und passt exakt zu „Git ist das Archiv".
- `validate_inbound_bundle` gibt Envelopes zurück, die **jeweils noch ihre eigene**
  `validate_inbound_envelope`-Prüfung brauchen, bevor ihr Inhalt gilt — der Adapter
  darf diese zweite Stufe nicht überspringen oder vortäuschen.
- **Vorbild-Adapter existiert:** `adapters/object_store/mrr/adapters/object_store/
  local.py` (`LocalFilesystemArtifactStore`) — dieselbe Bauform, dasselbe
  Verzeichnis-Layout, an dem sich E5-T08 anlehnt.
- Kein neuer Dependency nötig (`json`, `hashlib`, `pathlib` sind stdlib; Ed25519
  über das vorhandene `cryptography`).

## Zerlegung (use-first: nur T08 jetzt)

- **E5-T08 (jetzt):** konkreter **Dateisystem-Adapter** für den
  Offline-Bundle-Weg — Outbox schreiben, Inbox lesen und fail-closed prüfen,
  dateibasiertes Replay-Register — plus die erste Operator-Oberfläche
  `mrr federation outbox write` / `mrr federation inbox accept`. Reine
  Wiederverwendung von `build_outbox_bundle` / `validate_inbound_bundle`,
  ohne eine Zeile daran zu ändern. Kein Netz, keine DB, kein neuer Dependency.
- **E5-T09 (benannt, blockiert):** der **Schlüssel-/Identitäts-Austausch** mit
  Ulysses — Governance-Akt, braucht die andere Praxis. Kein Bau-Paket, solange
  Ulysses seinen öffentlichen Schlüssel nicht selbst veröffentlicht hat.
- **R1-T01 (benannt, danach):** die erste reale Joint Inquiry über diese Kante,
  am offenen Hammond-Dissens. Setzt E5-T08 **und** E5-T09 voraus.

## Ausdrücklich NICHT in E5-T08

Kein Netzzugriff, kein Socket, kein TLS/mTLS, kein HTTP-Client/-Server, keine
FastAPI-Route (bleibt E5-T03s ausdrücklich vertagter Bereich). Keine DB. Keine
Verschlüsselung/KMS (E5-T06 hat sie als infra-abhängig markiert; die Bundles
gehen zwischen zwei öffentlichen Repos, Vertraulichkeit ist hier nicht die
Schutzanforderung — Integrität und Urheberschaft sind es). **Keine erfundene
Identität und kein erzeugter Schlüssel für eine fremde Praxis.** Kein realer
Austausch mit Ulysses. Kein neuer Inquiry-Payload-Typ — der Adapter trägt
bestehende Objekte, er erfindet keine. Keine Änderung an der Objekt-/Port-Schicht
von E5/E6.

## Offene Owner-Entscheidungen (unverändert)

Weiterhin offen und **nicht** durch diese Ableitung berührt: K2-Tor-Wiedervorlage,
erstes A4-Release, Befund 1 (Artefakt-Blob-Dauerhaftigkeit), N1-T02/T03, **N2-T03**
(Support-Prüfung), N3, R2-T02/T03, sowie der `scripts/`-Blindfleck von
`make security-check`. Neu benannt: **E5-T09** (Schlüsselaustausch, braucht die
andere Praxis) und **R1-T01** (erste Joint Inquiry, setzt beides voraus).
