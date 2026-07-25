# A1-Fact-Lock: die Evidenz-Bytes der Real-Runs existieren nicht (2026-07-26)

**Status:** Fact-Lock vor der Ableitung, Owner-Vorlage. **Kein Bau, keine Änderung
am Code.** Dieses Dokument hält fest, was die Prüfung der Voraussetzung von A1
(„Artifact Bytes Closure") ergeben hat — und dass A1 in der vorgeschlagenen Form
**nicht ausführbar** ist, weil es nichts zu sichern gibt.

## Der Befund

**Null von 51 EvidenceAnchors der beiden Real-Runs haben auffindbare Bytes.**

| | `mrr_k1t04_real_run_v2` | `mrr_run2_corroboration_floor_v1` |
|---|---|---|
| EvidenceAnchors | 17 | 34 |
| davon mit `snapshot_hash` | 17 | 34 |
| **Snapshot-Bytes auf dieser Maschine auffindbar** | **0** | **0** |
| **Content-Bytes auf dieser Maschine auffindbar** | **0** | **0** |

Ermittelt mit dem **geprüften Dump-Parser des Repositories**
(`mrr.domain.archive_dump`, N2-T02b), nicht mit einem Ad-hoc-Parser — ein erster,
selbstgebauter Versuch scheiterte still an der COPY-Escaping-Form und sah nur je
einen Anker; die Zahlen 17/34 decken sich exakt mit N2-T02bs Ableitung und sind
damit kreuzgeprüft.

Gegen die Anker gehalten wurde jede content-addressed Datei, die ein Verzeichnis-
Durchlauf über `/var/folders`, `/tmp`, `/private/tmp`, `~/Documents` und
`~/Downloads` findet (Dateiname = 64 Hex-Zeichen, optional `.meta.json` —
das Layout, das `LocalFilesystemArtifactStore` schreibt). Gefunden: **26 Dateien,
keine davon gehört zu einem Real-Run.** Es sind Reste von Smoke-Läufen in
Session-Scratchpads.

## Warum das passieren konnte — die strukturelle Ursache

Nicht Nachlässigkeit, sondern eine Lücke im Schema und in der Aufrufform:

1. **`EvidenceAnchor` hat kein Ablage-Feld.** Der Body trägt `anchor_kind`,
   `snapshot_hash`, `content_hash`, `source_record_id`, `extraction_method`,
   `transformation_chain`, `anchor_validation_status` — und **keinen Locator,
   keine `snapshot_uri`, keine `artifact_id`**. Das Objekt hält fest, *dass*
   ein Snapshot gehasht wurde, und nirgends, *wo* er liegt.
2. **`--artifact-root` ist ein Pflicht-Kommandozeilenargument** von `mrr run`
   (`required=True`). Jeder reale Lauf hatte also ein Verzeichnis — aber der
   gewählte Pfad wird **nirgends aufgezeichnet**: nicht im RunManifest, nicht in
   den Objekten, nicht in einer Config, nicht in einer Env-Variable. Er lebte
   ausschließlich im Shell-Aufruf.
3. Es gibt **keine zweite Store-Implementierung** (nur `local.py`) und keinen
   Default-Ort. Wo die Bytes hinliefen, wusste nur der Aufrufende, im Moment des
   Aufrufs.

Ergebnis: das Archiv kann die Frage „wo lagen die Bytes?" nicht beantworten —
nicht weil die Antwort verloren ging, sondern weil das Feld nie existierte.

## Was das epistemisch bedeutet

Die beiden Real-Runs tragen die zwei realen Claims, die das gesamte System
rechtfertigen. Ihre 51 EvidenceAnchors sind mit
`anchor_validation_status: "validated"` verzeichnet.

**Dieser Status ist heute unfalsifizierbar.** Ohne die Bytes lässt sich nicht
nachprüfen, ob ein Anker auf das zeigt, was er behauptet — nur, dass irgendwann
ein Hash notiert wurde. N2-T02b hat gezeigt, dass die *internen* Bezüge
lückenlos auflösen (0 tote Anker). Das bleibt wahr und ist wertvoll. Aber es ist
Verankerungs-**Integrität**, nicht Verankerungs-**Einlösbarkeit**: der Graph ist
in sich geschlossen, seine Blätter sind leer.

Das ist die konsequente Fortsetzung derselben Unterscheidungsreihe:
Reliabilität ≠ Validität, Existenz ≠ Bestätigung, Verankerung ≠ Belegkraft,
Transport ≠ Vertrauen, Anwesenheit ≠ Support — und jetzt:
**ein aufgelöster Anker ≠ ein einlösbarer Anker.**

## Warum Wiederbeschaffung nicht allgemein möglich ist

N2-T02b hat erstverifiziert: von den 18 distinkten SourceRecords des ersten Runs
sind 3 arXiv-Papers und 1 DOI; **15 sind `curated-artwork-record` mit gewöhnlichen
Web-URLs**. Für diese ist ein erneuter Abruf kein Restore, sondern eine neue
Erhebung — der Inhalt kann sich geändert haben, und ein neuer Snapshot hätte
einen anderen Hash. Er würde die alten Anker nicht einlösen, sondern ersetzen.

## Konsequenz für A1

A1 in der vorgeschlagenen Form („sichere die Artifact Bytes und beweise, dass
Dump + Bytes einen Forschungszustand rekonstruieren") **ist nicht ausführbar.**
Es gibt keine Bytes zu sichern, und ein Restore-Test gegen sie kann nicht
bestehen. Das Paket hatte einen Defektstatus als Randfall vorgesehen; er ist der
Hauptfall.

Was stattdessen zu tun ist, in dieser Reihenfolge:

1. **Owner-Frage, zuerst und einzige Rettungschance:** existiert außerhalb dieser
   Maschine noch ein Artifact-Root der Juli-Läufe — Backup, zweiter Rechner,
   Time Machine? Nur Frank kann das wissen; die Aufrufe liegen allenfalls in
   seiner Shell-History. Findet sich einer, ändert sich alles Weitere.
2. **Datierter Defekt-Record auf beiden Real-Runs**, der festhält: 51 Anker,
   `validated` verzeichnet, Snapshots nicht einlösbar. Sichtbar und datiert, nicht
   stillschweigend.
3. **Schema-Schließung, damit der nächste Lauf dasselbe Loch nicht hat:** ein
   Anker führt eine Ablage-Referenz mit, und das RunManifest hält seinen
   `artifact_root` fest. Das ist ein kleines, klar begründetes Paket mit realem
   Defekt als Anlass — genau die Form, die die use-first-Doktrin verlangt.
4. **Erst danach** ein Restore-/Reproduktions-Paket, das dann etwas zu
   rekonstruieren hat.

## Konsequenz für die öffentlichen Projektionen

Falls die Site Verifikations- oder Verankerungszahlen der Real-Runs zeigt, sind
sie durch diesen Befund berührt. Die Aktualitätsregel (Frank, 2026-07-25)
verlangt, dass die Site den Stand zeigt — und der Stand ist, dass die Anker
intern auflösen und extern nicht einlösbar sind. Das gehört auf die
Verification Ladder als eigene, ehrliche Sprosse, nicht weggelassen.

## Was dieser Fact-Lock NICHT behauptet

- **Nicht**, dass die Bytes nie existiert haben. Sie wurden geschrieben; der
  Store verlangt ein Wurzelverzeichnis und legt Sidecars an.
- **Nicht**, dass sie global verloren sind — nur, dass sie auf dieser Maschine an
  den durchsuchten Orten nicht liegen. Backups sind Owner-Wissen.
- **Nicht**, dass die Claims falsch sind. Sie sind unüberprüfbar geworden, was
  etwas anderes ist — und was genau so berichtet gehört.
