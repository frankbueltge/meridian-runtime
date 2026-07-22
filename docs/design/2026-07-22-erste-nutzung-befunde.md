# Erste reale Nutzung: zwei Befunde aus dem ersten echten Export (2026-07-22, Abend)

**Status:** Befund-Record (keine Entscheidung). Entstanden beim allerersten Versuch, mit
der fertigen E8-Maschinerie die **echte** Forschungsausgabe des ersten realen Laufs
(K1-T04) zu exportieren — statt eines Testfixtures. „Benutzen statt weiterbauen" (Owner-
Entscheidung vom selben Tag) hat sofort geliefert, wofür es da ist: zwei konkrete,
handfeste Lücken, die kein weiteres Bauen je gezeigt hätte.

## Befund 1 — Artefakt-Bytes sind nicht dauerhaft gespeichert

Der Export gegen die reale Crate (`urn:mrr:evidence-crate:01KY1SNYPTXWTMHW72M498996J`)
verweigerte zunächst korrekt (fail-closed): `MissingArtifactBytesError` für vier
Content-Hashes. Die vier versiegelten Artefakt-Bytes (Korpus, Method-Protocol,
Protocol-Parameters, Question-Model) existieren **nur in einem alten Session-Scratchpad**
(`.../383b5bf0-.../k1t04-real-run-artifacts-v2/`) — jederzeit aufräumbar. Der heutige
DB-Dump (`scripts/archive-dump.sh`, PR #71) sichert Objekte und Event-Ketten, aber
**nicht die Artefakt-Blobs**. Zwei der vier ließen sich aus committeten Korpus-Dateien
bzw. gespeicherten Objekten (kanonische Form) rekonstruieren, zwei nicht.

**Konsequenz:** Der Archiv-Dump allein kann heute keinen vollständigen Export
reproduzieren. Der `LocalFilesystemArtifactStore`-Shard-Baum gehört mit ins dauerhafte
Archiv (committen, oder in den Dump-Schritt aufnehmen). Kleine, klar umrissene Ergänzung.

## Befund 2 — die Crate ist vom Claim-Graphen abgekoppelt (der wichtige)

Der Export lief nach Bereitstellung der Bytes durch — mit `object_count: 1`. **Nur die
Crate selbst, kein einziger Claim.** Ursache, direkt an der DB geprüft:

- Die reale EvidenceCrate hat **leere** `proposed_claims`, `source_records`,
  `evidence_anchors` (`run_state: completed`).
- Dasselbe Schema enthält aber einen reichen Graphen: 18 SourceRecords, 17
  EvidenceAnchors, 4 Claims (2 real + Revisionen), 3 VerificationResults (die heutigen
  Aufnahmen), 3 EvidenceMatrix, MethodRulings, Charters, …
- Es gibt nur 7 Kanten, Typen `ruled_by` / `operationalizes` / `governed_by_protocol` —
  **keine** verbindet die Crate mit den Claims, und keine ist eine `derived_from`-Kante,
  auf der die E8-Closure-BFS aufsetzen könnte.

Die gesamte E8-Maschinerie (Export, Report, Release) ist **crate-verwurzelt** — bewusst
so entworfen („eine Closure, zwei Konsumenten", E8-T01). Sie setzt voraus, dass
`crate.proposed_claims` gefüllt ist und von dort die Provenance-BFS die Claims erreicht.
Der reale Lauf hat die Crate aber versiegelt, **ohne** die Claims zu verlinken: Die
Crate dichtet die Lauf-**Eingaben**/Artefakte ab, der Claim-Graph entstand als separater
Schritt und referenziert nicht zurück auf die Crate (und die Crate nicht vorwärts auf
ihn).

**Das ist die eine Naht, an der die „Kopplung Runtime ↔ Site" heute bricht.** Nicht die
Site-Anbindung ist das Problem, sondern dass die designierte Export-Wurzel (die Crate)
nicht die Wurzel des Claim-Graphen ist. Die reale Forschungsausgabe — die Claims samt
ihrer widerstreitenden Verifikationen (Hammond-Dissens!) — ist mit dem heutigen Werkzeug
**nicht exportierbar**, obwohl sie vollständig und korrekt im Archiv liegt.

## Zwei mögliche Richtungen (Entscheidung offen, Owner-Sache)

1. **Run-Pipeline-Fix:** Die Crate bei Lauf-Abschluss mit `proposed_claims` (und den
   Source-/Anchor-Referenzen) füllen — dann greift die vorhandene Closure unverändert.
   Berührt aber die Versiegelungs-Semantik (eine versiegelte Crate ist unveränderlich;
   das Füllen müsste **vor** dem Seal geschehen, also eine Pipeline-Reihenfolge-Frage,
   und rückwirkend für den bestehenden Lauf gar nicht möglich ohne neuen Lauf/neue
   Crate-Revision außerhalb des Seals).
2. **Export claim-/run-/schema-verwurzelbar machen:** Eine zweite Wurzel neben der Crate
   (z. B. „exportiere den vollständigen Claim-Graphen dieses Laufs", BFS von den Claims
   statt von der Crate). Additiv, berührt keine versiegelten Bytes, macht die
   **bestehenden** Real-Läufe sofort exportierbar. Wahrscheinlich die ehrlichere und
   rückwärtskompatible Richtung — als eigenes Paket zu entwerfen.

Beides sind eigene Pakete mit Owner-Framing; hier nur der Befund, nicht die Wahl.

## Warum das ein gutes Zeichen ist

Ein Vaporware-System käme nie weit genug, um einen Crate↔Claim-Bruch zu zeigen — es
scheiterte vorher an Trivialerem. Dass zehn Minuten echte Nutzung zwei präzise,
umsetzbare Lücken freilegen, ist genau das Verhalten eines funktionierenden Systems.
Der erste Export lief technisch fehlerfrei (4 Artefakte, 75.621 Bytes, gültige
RO-Crate-1.1-Metadaten mit PROV) — er zeigte nur wahrheitsgemäß, dass die Wurzel am
falschen Objekt hängt.
