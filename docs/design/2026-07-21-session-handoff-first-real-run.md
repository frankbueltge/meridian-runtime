# Session-Handoff — nach der ersten echten Forschungsausgabe (2026-07-21)

**Status:** Übergabedokument für eine frische Session. Selbstständig lesbar; eine Session
ohne Vorkontext kann hieraus handeln. Vorgänger-Handoff:
`2026-07-21-research-method-kernel-handoff.md` (Kontext der Kernel-Entscheidung).

## 1. Was in dieser Session passiert ist (Kurzbilanz)

Eine durchgehende Session (Nacht 20.→21.07., Owner-Delegation: „volle Autonomie, zieh
durch") hat gebaut und gemergt — **PRs #40–#52, alle mit unabhängigem Review vor dem
Merge**, Suite von ~1.300 auf ~2.000 Tests:

- **E5 Federation komplett** (inkl. #40-Fix: `RETURNING` statt `CursorResult.rowcount` —
  SQLAlchemy memoiert rowcount nur für UPDATE/DELETE; psycopg 3 liefert nach Cursor-Close
  `-1`; Lehre gilt für JEDEN künftigen ON-CONFLICT-Store) und E5-T07b (Revocation-Record).
- **E6 komplett (6/6):** TransferContract (#41), Obligations (#43, mit review-getriebenem
  Idempotenz-Guard), CorrectionNotification (#46), CorrectionResponse (#48), öffentliche
  Unresolved-Projektion (#50), Offline-Delivery-Tracking (#51, Migration `cbef83b8ef50`).
- **Kernel K0+K1 komplett:** MethodProfile+Registry (#44), Capability-Dispatch (#45),
  sechs Governance-Verträge (#47), Edges/Ceilings/Gates (#49, Migration `aa2adabee4de`,
  mit review-getriebenem Gated-Edge-Bypass-Fix und Bandit-B101-Fix), Synthese-Executor
  (#52). Spec `08_RESEARCH_METHOD_KERNEL.md` ist ACCEPTED, zweimal amendiert
  (MethodProtocol-Re-Review-Zyklus `amended → reviewed`; Finding- vs. Claim-Status in §5).
  ADR-0010 (Objekt-Level-Klassifikation, baseObject-Feld) ist ACCEPTED, Feld noch NICHT
  implementiert (staged adoption, Schritt 1 = eigenes Packet).
- **Der erste echte Forschungslauf: PR #53 — OFFEN, wartet auf den persönlichen Merge des
  Owners** (bewusst nicht delegiert). Inhalt: Model-Collapse-Frage über beide Atlanten;
  Ergebnis ehrlich gemischt — von 15 Cluster-7-Werken instanziiert genau eines (Hammond,
  *V3: Model Collapse*) den Mechanismus, 14 referenzieren nur (inkl. Kurant/*Errorism*:
  trainiert auf eigenem Schreiben, nicht auf Modell-Vorgenerationen); Werk-Analyse endet
  `contested`, Theorie-Positivkontrolle `supported`-track mit Claim bewusst auf `draft`
  (keine Selbst-Verifikation). Unabhängiger Review: alle 15 Klassifikationen gegen die
  Atlas-Originale geprüft, Pipeline zweimal byte-identisch reproduziert, keine Blocker;
  Review-Fixes bereits eingearbeitet (Commit `4afc07d`). Reale Crate liegt in der lokalen
  Test-Postgres, Schema `mrr_k1t04_real_run_v2`.
- Außerdem: Task-Packet-Hygiene (47/47 strikt parsebar), §5.2-Event-Refresh in
  `03_API_AND_EVENTS.md`, E9-T00-Packet (Entwurf) abgeleitet.

## 2. Lokale Infrastruktur dieser Session (geht bei Reboot verloren)

- **Wegwerf-Postgres 16** (Homebrew `postgresql@16`, Prefix `/usr/local`), Daten in der
  Session-Scratchpad. Neustart-Kommando für eine neue Session (neues PGDATA anlegen, falls
  Scratchpad weg):
  ```
  /usr/local/opt/postgresql@16/bin/initdb -D <dir>/pgdata -U mrr --auth=trust -E UTF8
  /usr/local/opt/postgresql@16/bin/pg_ctl -D <dir>/pgdata \
    -o "-p 54329 -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c timezone=UTC" start
  /usr/local/opt/postgresql@16/bin/createdb -h 127.0.0.1 -p 54329 -U mrr mrr_test
  export MRR_TEST_DATABASE_URL='postgresql+psycopg://mrr@127.0.0.1:54329/mrr_test'
  ```
  **timezone=UTC ist Pflicht** — die Event-Hash-Kette ist session-timezone-sensitiv
  (bekannter E9-Hardening-Punkt, auf PR #40 dokumentiert).
- Implementierungs-Worktrees liegen in der Session-Scratchpad (`wt-*`) — verzichtbar,
  alle Branches sind gepusht.

## 3. Das Arbeitsmuster, das sich bewährt hat (beibehalten)

Pro Task: **Packet ableiten (Agent, read-only, strikt valides YAML mit `- >-`-Skalaren) →
Review/Approval durch die Hauptsession (dokumentierte reviewer_resolution bei
Abweichungen) → Implementierung in eigenem Worktree-Agenten (Branch von origin/main,
Rebase vor Push, additive Konflikte beidseitig behalten) → unabhängiger Review-Agent
(read-only, eigene Worktree, ALLE Gates auf dem Merge-Baum) → Findings im PR fixen →
Merge erst nach grünem PR-Check.** Gate-Liste IMMER inklusive `make test-e2e` und
`make security-check` (Bandit lehnt nackte asserts im Produktionscode ab). Ein Packet
kann von der Implementierung ehrlich überstimmt werden, wenn reviewer_resolution/Spec
Vorrang haben — Konflikt immer im PR-Body dokumentieren.

## 4. Nächste Arbeit, in empfohlener Reihenfolge

1. **PR #53 mergen** — Owner persönlich (Review liegt als Kommentar an, Empfehlung: mergen).
2. **E9-T00** (`task-packets/E9-T00.yaml`, Entwurf): sieben kleine Review-Follow-ups
   (notification_received-Event, maxItems-Bound, Re-Notification-Doku, Delivery-Atomarität
   dokumentieren, zwei Test-Lücken, EvidenceCrateSealer-Erweiterung). Zwei markierte
   Urteilsfragen brauchen Reviewer-Sign-off, dann approve + implementieren.
3. **ADR-0010 Schritt 1** (eigenes Packet ableiten): optionales `classification`-Feld auf
   baseObject + Contract; danach Schritt 2/3 (Projektion liest Stored-Field;
   TransferService-Gate schließen) und MTH-012-Gate-Verdrahtung.
4. **DRY-Konsolidierung** (eigenes Packet): fünf Trust-Resolver, drei UoW-Helfer,
   build_dispatch_table-Dedup ist schon erledigt (#52).
5. **K2-Entscheidungstor** (Plan-Dokument §K2): Mit dem realen K1-Output entscheiden, ob
   kausale Verträge (propose + human ruling, KEINE Engines) abgeleitet werden.
6. **E7/E8** (Roadmap): E7 als Method-Profile-Reframing (qualitativ), E8 Exporte — jetzt
   mit echtem Inhalt. E9 formal nach E9-T00.
7. **Site-Kopplung des ersten Outputs** — Werk-Entscheidung des Owners, nicht
   Runtime-Mechanik: ob/wie das Claim-Landscape auf frankbueltge.de erscheint
   (AuthorshipNote-Ethik der Site beachten).

## 5. Offene Urteilsfragen, bewusst NICHT entschieden

- E9-T00s zwei markierte Calls (Event-Keying des Receipt-Events; §5.3-Lesart der
  Delivery-Atomarität).
- Re-Notification-Semantik bei wachsendem notified-Set (nur dokumentiert).
- `MethodProfile.executor_task_family` wird noch nicht gegen das Capability-Synonym
  geprüft (PR #52-Review, lokaler Guard vorhanden; Cross-Check = Folgetask).
- MRR-MTH-018: `sensitivity_variations` wird befüllt, aber nicht ausgeführt
  (Executor-Erweiterung, Folgetask).
- Kein Rücktransport der CorrectionResponse zum Sender in E6 (Epic-Lücke, in E6-T04
  dokumentiert) — Kandidat für ein E6-T07 oder eine Spec-Amendment-Runde.

## 6. Bindende Regeln (unverändert, Kurzliste)

AGENTS.md-Disziplin; ein Packet pro Branch/PR; nie ohne Owner-Go auf main mergen —
**mit Ausnahme der in dieser Session erteilten, dokumentierten Delegation**, die eine
neue Session sich NICHT stillschweigend borgt (neu einholen); keine KI-Produkt-Credits in
Git; Git-Identität `Frank Bültge <f.bueltge@gmail.com>` (NIE `frank@bueltge.de` — andere
reale Person); Subagenten default Sonnet; Lizenz noncommercial.
