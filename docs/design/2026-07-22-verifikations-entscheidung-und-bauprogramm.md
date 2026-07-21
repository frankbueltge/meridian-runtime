# Verifikations-Entscheidung und Bauprogramm (2026-07-22)

**Status:** Entschieden — **vom Owner (Frank) persönlich, in Session, 2026-07-22 kurz nach
Mitternacht** (keine Delegation; die Optionen wurden ihm mit dem vollständigen
Verifikations-Design-Memo vorgelegt und er hat direkt gewählt). Dieses Dokument ist der
Entscheidungs-Record; die Umsetzung folgt dem Paket-Idiom (ein Packet, ein Branch, ein PR).

## Entscheidung 1 — Wer verifiziert die zwei realen Claims: **Kombination (c) + (d)**

Bezug: `docs/design/2026-07-21-verifikations-design-memo.md` (Kandidaten §4, Tabelle §6,
Urteilsfragen §7).

1. **(c) jetzt, für BEIDE Claims:** eine strukturell getrennte Session (frischer
   Worktree/Kontext, keine Wiederverwendung der Produktions-Pipeline) liest die
   Quellenbelege **neu gegen die Primärquellen** — die 15 Werk-Klassifikationen gegen die
   `decisive_move`-Texte des gepinnten Werk-Atlas-Snapshots und Hammonds
   Ausstellungsdokumentation, die 3 Theorie-Paper direkt — und bildet ein **eigenes
   Urteil** je Zeile, das erst danach mit der Pipeline-Klassifikation verglichen wird.
   Ein Pipeline-Re-Run zählt ausdrücklich NICHT (MTH-014-Grenze, `REPLICATION_NOT_
   INDEPENDENT` sinngemäß auf Verifikation angewandt): computational reproduction ist
   bereits geschehen (PR #53) und war keine Verifikation.
2. **(d) zusätzlich, für den Werk-Atlas-Claim:** eine Cross-Praxis-Prüfung durch Ulysses
   (`irrtum-als-methode`) als genuin externe zweite Stimme — als **Encounter-Angebot**
   über Ulysses' eigenen Kanal (Offers, not orders; Ulysses' Standing Terms gelten;
   Schweigen blockiert nichts). Für den **Theorie-Atlas-Claim wird (d) bewusst NICHT
   angefragt** — Ulysses ist dessen Kurator; eine Kurator-Prüfung wäre die engere Frage
   „hat die Pipeline unseren Atlas korrekt gelesen", nicht Quellenverifikation (Memo §4d).
3. **Ehrlichkeits-Vermerk, der in jedes `VerificationResult` gehört:** der
   letztverantwortliche Mensch bleibt in allen Instanzen derselbe (Frank); die
   Unabhängigkeit von (c) ist substanziell (Rückgang auf Primärquellen, getrennter
   Kontext, kein Pipeline-Code), nicht institutionell. `IndependenceProfile` bleibt
   Selbstauskunft — genau so wird sie deklariert, nicht besser.

## Entscheidung 2 — Bauumfang: **alles außer E7**

Der Owner hat den vollen offenen Rest beauftragt: Verifikation, E8 komplett, E9-Rest.
E7 (qualitatives Profil) bleibt als einziges „later". Reihenfolge:

| # | Schritt | Form |
|---|---------|------|
| 1 | **K1-T05** — `VerificationResult`-Recording-Pfad im CLI (`mrr verification record`); die Tooling-Lücke, die JEDER Verifizierer-Kandidat braucht (Memo §6: „ein CLI-Recording-Pfad existiert aktuell nicht") | Packet, klein |
| 2 | **K1-T06** — Verifikationslauf (c): getrennte Primärquellen-Session, Urteil je Zeile, Vergleich, `VerificationResult`s für beide Claims via K1-T05-Pfad in das dauerhafte Schema | Packet |
| 3 | **Ulysses-Angebot (d)** — Encounter-Offer im Kanal von `irrtum-als-methode`; läuft parallel über die Engine-Zyklen; das Ergebnis wird, falls Ulysses annimmt, als weiteres `VerificationResult` mit voller Attribution übertragen | Offer, kein Packet |
| 4 | **E8-T01…T05** — RO-Crate-Export, PROV-Mapping, Report-Projektion, Publikations-Freigabe, Korrektur-Banner (`06_IMPLEMENTATION_PLAN.md` §3/E8) | Pakete, je einzeln |
| 5 | **E9-T01…T07** — Härtung inkl. des notierten Timezone-Bugs der Event-Hash-Kette (Rework-Memo §8) | Pakete, je einzeln |

## Nebenentscheidungen / Klarstellungen

- **K2 bleibt vertagt.** Trigger 3 der K2-Tor-Entscheidung („ein zweiter realer Lauf")
  ist durch K1-T04b formal erfüllt, aber der Owner hat heute Nacht bewusst die
  Verifikations-/Export-Schiene priorisiert — das ist zugleich der Weg zu Trigger 2
  (MTH-018 ✓ via K1-T03b; unabhängige Verifikation = dieses Programm). Das Tor wird
  danach mit besserer Evidenzlage neu bewertet, nicht nebenbei.
- **Site-Kopplung:** weiterhin offene Werk-Entscheidung des Owners; E8 ist ihre
  technische Voraussetzung, greift ihr aber nicht vor.
- **Verhältnis zu Meridian Classic:** unverändert der Koexistenz-Plan
  (`06_IMPLEMENTATION_PLAN.md` §5) — Parallelbetrieb, deklarierte Vergleichsläufe,
  fähigkeitsweise umkehrbare Adoption. Kein Vergleichslauf ist Teil dieses Programms;
  der erste (Dual/Challenger) wird erst NACH E8 sinnvoll (transportfähiger Output) und
  wäre der Praxis als Angebot zu machen, nicht zu verordnen.
