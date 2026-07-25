# A2-Ableitung: der Lauf hält fest, wohin er seine Bytes geschrieben hat (2026-07-26)

**Status:** Entschieden — der Owner (Frank) hat am 2026-07-26 beauftragt, dafür zu
sorgen, „dass das beim nächsten Lauf nicht nochmal passieren kann". Governance-Commit
vor dem Bau; der Merge nach `main` bleibt an eine ausdrückliche Owner-Freigabe
gebunden.

**Der Anlass ist ein dokumentierter Defekt, kein Spezifikationspunkt:**
`docs/design/2026-07-26-a1-fact-lock-artifact-bytes.md` — null von 51 EvidenceAnchors
beider Real-Runs haben auffindbare Bytes, weil nirgends aufgezeichnet ist, wohin sie
geschrieben wurden.

## Fact-Lock (erstverifiziert am realen Code)

- **`--artifact-root` ist Pflichtargument** von `mrr run` (`required=True`,
  `cli/main.py:129`). Ein Lauf ohne Wurzelverzeichnis ist gar nicht möglich.
- **Der Pfad wird verwendet und danach vergessen:** `cli/main.py:283` legt ihn an,
  `:292` konstruiert `LocalFilesystemArtifactStore(args.artifact_root)`. Danach
  erscheint er in **keinem** persistierten Objekt.
- **`RunManifest` hat 20 Felder** (`contracts/run_manifest.py:97–121`), darunter
  `environment: dict[str,str]`, `parameters: dict[str,Any]`, `input_hashes`,
  `image_digest`, `code_commit` — **kein einziges für die Artefakt-Ablage.** Der Lauf
  zeichnet sein Image, seinen Commit, seine Seeds und seine Netzwerkfreigaben auf,
  aber nicht, wohin seine Beweise gingen.
- **`RunManifest` hat einen `schemas/`-Spiegel** (`run-manifest.schema.json`), der
  mitgeführt werden muss (AGENTS Regel 6).
- **Der Store ist content-adressiert:** `<root>/<hex[0:2]>/<hex[2:4]>/<hex>` plus
  `<…>.meta.json` (`adapters/object_store/.../local.py:7–8`).

## Die tragende Einsicht: ein Feld genügt, nicht einundfünfzig

Der naheliegende Reflex wäre, jedem `EvidenceAnchor` einen Locator zu geben. Das wäre
falsch — und teuer:

**Weil der Store content-adressiert ist, ist der Pfad jedes Blobs aus Wurzel und Hash
vollständig bestimmt.** Der Anker trägt seinen Hash bereits. Ein Locator je Anker wäre
also redundante Information, die auseinanderdriften kann — und er würde ein Objekt
ändern, das in zwei committeten Archiv-Dumps 51-fach vorkommt und extern
schema-validiert ist.

**Die Wurzel ist eine Eigenschaft des LAUFS, nicht des einzelnen Ankers.** Alle Anker
eines Laufs teilen sie. Ein Feld auf `RunManifest` — ein Objekt pro Lauf — genügt, um
jeden einzelnen Blob wiederzufinden. Das ist die kleinste Änderung, die den Defekt
schließt, und sie fasst `EvidenceAnchor` nicht an.

## Die Ehrlichkeits-Grenze: „nicht aufgezeichnet" ist ein Wert, kein fehlendes Feld

Die beiden existierenden RunManifests haben keine Wurzel aufgezeichnet und können sie
nicht nachträglich bekommen — sie zu erfinden wäre Fabrikation im Archiv.

Das Feld ist darum **nie ein nacktes `None`**, sondern trägt einen geschlossenen
Status, gespiegelt vom Haus-Muster der Bikonditionale
(`ModelInvocationOutcome.response_hash`: „present if and only if"):

- **`recorded`** — die Wurzel ist da. Genau dann ist `root` gesetzt.
- **`not_recorded`** — es wurde keine aufgezeichnet. Genau dann ist `root` `None`.

Beides sind Aussagen, keine Lücken. Für die beiden Alt-Läufe ist `not_recorded` die
**wahre** Aussage — der Default ist hier nicht bequem, sondern zutreffend, und darum
zulässig. Ein Lauf ab diesem Paket kann `not_recorded` nicht mehr produzieren, weil
`--artifact-root` Pflicht ist.

`not_recorded` darf **nie** als „Bytes fehlen" gelesen werden und **nie** als „alles in
Ordnung". Es heißt genau: wir wissen nicht, wo sie sind. Dieselbe Trennung wie
N2-T02bs `source_unanchored` (Beobachtung) gegen `anchor_dangling` (Verletzung).

## Zweite Hälfte: das Aufzeichnen wird erst durch das Nachsehen nützlich

Ein Feld, das niemand prüft, ist ein Feld, das still falsch werden kann. Darum gehört
`mrr audit artifacts` dazu, gespiegelt von `mrr audit anchoring`: liest einen
committeten Dump, nimmt die aufgezeichnete Wurzel, leitet aus jedem Anker-Hash den
erwarteten Pfad ab und berichtet — mit geschlossenem Status je Anker:

- `artifact_present` — Byte liegt am erwarteten Pfad, Hash stimmt.
- `artifact_missing` — Wurzel aufgezeichnet, Byte fehlt. **VERLETZUNG.**
- `artifact_hash_mismatch` — Byte da, Hash weicht ab. **VERLETZUNG.**
- `store_reference_not_recorded` — der Lauf hat keine Wurzel aufgezeichnet.
  **BEOBACHTUNG**, keine Verletzung: es ist nichts kaputt, es ist nichts bekannt.

Die Trennung ist hier besonders wichtig, weil der **erste reale Lauf ausschließlich
Beobachtungen** produziert. Würde man sie als Verletzungen zählen, meldete das Werkzeug
sofort 51 Fehler, wo es null Fehler und eine Wissenslücke gibt.

## Akzeptanz-Orakel (VOR dem Bau festgelegt)

An den beiden committeten Dumps, mit dem geprüften Parser (`mrr.domain.archive_dump`)
erstverifiziert am 2026-07-26:

| | `mrr_k1t04_real_run_v2` | `mrr_run2_corroboration_floor_v1` |
|---|---|---|
| EvidenceAnchors | 17 | 34 |
| `store_reference_not_recorded` | **17** | **34** |
| `artifact_present` / `missing` / `hash_mismatch` | 0 / 0 / 0 | 0 / 0 / 0 |
| Verletzungen | **0** | **0** |
| Beobachtungen | **17** | **34** |

**Der erste Lauf des Werkzeugs reproduziert damit maschinell den Befund, der heute von
Hand gefunden wurde** — 51 Anker, keine Wurzel, keine Verletzung, eine Wissenslücke.
Das ist zugleich die schärfste Gegenprobe: fiele auch nur eine Zeile als Verletzung an,
wäre die Statustrennung gebrochen.

## Ausdrücklich NICHT in A2

Keine Änderung an `EvidenceAnchor` (siehe „ein Feld genügt"). **Keine Wiederbeschaffung
der verlorenen Bytes** — 15 der 18 SourceRecords sind Web-URLs, ein neuer Abruf wäre
eine neue Erhebung mit neuem Hash und würde die alten Anker ersetzen statt einlösen.
**Kein nachträgliches Eintragen einer Wurzel** in die Alt-Manifeste. Keine Migration
(JSONB-Body), kein Modell, kein Netz, keine DB-Verbindung im Audit, kein neuer
Dependency. **Kein Nicht-Null-Exit bei Verletzungen** — das Audit berichtet, es urteilt
nicht (Semantik von N2-T01: 0 / 2 / 3).

## Offene Owner-Entscheidung, unverändert

Ob außerhalb dieser Maschine noch ein Artifact-Root der Juli-Läufe existiert (Backup,
zweiter Rechner). Findet sich einer, wird aus dem Defekt eine Rettung — dieses Paket
ändert daran nichts und verbaut nichts.

Zusätzlich **neu benannt, nicht Teil dieses Pakets:** ob `mrr run` einen Lauf
verweigern soll, dessen `--artifact-root` in einem vom System aufgeräumten
Temp-Bereich liegt. Das war heute der wahrscheinlichste Verlustweg. Es ist ein
sinnvoller Schutz, aber eine eigene Entscheidung — ein Gate, das Läufe ablehnt, ist
mehr als eine Aufzeichnung.
