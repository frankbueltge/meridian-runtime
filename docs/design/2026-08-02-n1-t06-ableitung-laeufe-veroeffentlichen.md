# Ableitung N1-T06: die Läufe veröffentlichen — ein Exportweg über die Repo-Grenze

**Status:** Ableitung mit Fact-Lock. **Kein Bau in dieser Notiz.** Das Paket
`task-packets/N1-T06.yaml` folgt aus ihr; gebaut wird erst danach.

**Anlass:** Owner-Auftrag vom 2026-08-02. Die Site sagt heute etwas Falsches
über Meridian, und sie kann es nicht selbst richtigstellen, weil der Weg vom
Archiv zur Site von Hand gegangen wurde und niemand ihn seither gegangen ist.

**Zwei Vorgaben binden gleichzeitig:**

- *Aktualitäts-Regel* (`frankbueltge.de/CLAUDE.md`, Frank 2026-07-25): Die Site
  muss stets den neuesten Stand zeigen; jede Session prüft auf Drift.
- *Owner, 2026-08-02:* **„Trag NICHT nur Lauf 2 nach, sonst ist die Site sofort
  wieder veraltet."** Das ist die eigentliche Anforderung: nicht ein Nachtrag,
  sondern eine Struktur, die den nächsten Nachtrag überflüssig macht.

---

## 1. Fact-Lock — nachgerechnet am 2026-08-02

### Die Site sagt „two claims from a single run", und das stimmt nicht mehr

`frankbueltge.de/src/components/pages/MethodenblattOnRecord.astro` sagt es an
**zwei** Stellen, nicht an einer:

- `:81` — *„The empirical base is thin: two claims from a single run."* Der Satz,
  den der Owner nennt.
- `:30` — *„The closure holds the two claims, {claimAnchors} evidence anchors…"*
  Hier ist „two" gerechnet: `object_count` und die Anker kommen aus
  `parallax.json`, aber das Zahlwort **„two claims"** steht als Wort im Text.
- `:83` — *„In this run no source backs more than one classification…"* —
  ebenfalls auf einen einzigen Lauf geschrieben.

Ein Nachtrag, der nur `:81` anfasst, lässt die Site an zwei Stellen falsch.

### Was wirklich im Archiv liegt

`archive/dumps/` führt **drei** Läufe (nachgerechnet aus den SQL-Dumps):

| Dump | Claims | Verifikationen | Status-Verteilung |
|---|---|---|---|
| `mrr_k1t04_real_run_v2.sql` | 2 | 3 | 3 accepted, 1 contested, 13 draft |
| `mrr_run2_corroboration_floor_v1.sql` | 4 | 9 | 3 accepted, 2 contested, 18 draft |
| `mrr_run3_e2e_claims_v1.sql` | 1 | 0 | 3 accepted, 1 contested, 11 draft |
| **Summe** | **7** | **12** | |

Veröffentlicht ist **Lauf 1**: `src/data/meridian/export/` hält
`ro-crate-metadata.json` mit 89 Graph-Einträgen — davon 2 `mrr:Claim`,
3 `mrr:VerificationResult`, 17 `mrr:SourceRecord`, 17 `mrr:EvidenceAnchor`,
42 `File`. `parallax.json` bestätigt: `object_count: 42`, `artifact_count: 0`,
Claim `…3RQFS`, `supporting: 1`, `contradicting: 13`, `verification_count: 2`.

**Die Site zeigt also 2 von 7 Claims und 3 von 12 Verifikationen** und nennt das
„a single run". Der Satz war am 22.07. richtig und ist es seit Lauf 2 nicht mehr.

### Der Engpass ist echt

`mrr export ro-crate` verlangt eine erreichbare Postgres:

- `export_main.py:185` — `--database-url` mit `required=True`.
- `export_main.py:316-327` — Erreichbarkeitsprüfung; schlägt sie fehl, endet das
  Kommando mit `_EXIT_DEPENDENCY_UNAVAILABLE` und der Meldung *„Refusing to
  fabricate a substitute result (MRR-NFR-012)"* (`:321`).

Die Dumps sind SQL, keine Datenbank. **Kein Workflow restauriert oder
exportiert heute** — nachgesehen in allen vier Workflows von `meridian-runtime`
(`ci.yml`, `field-watch.yml`, `gold-classification.yml`, `research-run.yml`):
keiner ruft `mrr export` auf. Die committete Crate wurde von Hand erzeugt.

**Das Muster für die Reparatur liegt aber schon im Haus.** `research-run.yml`
fährt bereits genau die Maschinerie, die hier fehlt (`:120-165`): einen
`postgres:16`-Service-Container mit Healthcheck, `alembic upgrade head` gegen
`postgresql+psycopg://mrr:mrr@localhost:5432/mrr_run`, und danach `pg_dump` in
`archive/dumps/`. Der Exportweg ist derselbe Weg rückwärts: `psql < dump`
statt `pg_dump > dump`.

### Die Zustellung über die Repo-Grenze ist gelöst

`ECOLOGY_TOKEN` existiert als Repo-Secret (`gh secret list`, angelegt
2026-08-01T22:22:28Z). `field-watch.yml:137-186` zeigt das Muster vollständig:
klonen mit `x-access-token`, hineinschreiben, als Maschinenidentität an einer
`.invalid`-Adresse committen, pushen — **und ehrlich degradieren**, wenn das
Secret fehlt (`::warning::` plus `exit 0`, nie stilles Durchlaufen).

### Der Prüfstein auf der Site-Seite

`frankbueltge.de/.github/workflows/ci.yml:73-87`, Job-Name **„Meridian view
model (derivation matches the committed export)"**: installiert
`pipelines/meridian/requirements.txt`, fährt `pipelines/meridian/refresh.py`
und verlangt danach

```
git diff --exit-code -- src/data/meridian/mrr-graph.ttl src/data/meridian/parallax.json
```

Der Job prüft also genau die Drift, um die es hier geht: ein neuer Export ohne
nachgezogene Ableitung ist rot. **Er muss grün bleiben**, und das heißt: wer die
Crate ändert, committet `mrr-graph.ttl` und `parallax.json` im selben Zug mit.

### Warum ein bloßer Nachtrag die Site sofort wieder veraltet

`pipelines/meridian/refresh.py` ist auf **genau einen** Export verdrahtet:

- `:24` — `EXPORT = ROOT / "src" / "data" / "meridian" / "export"`
- `:88` — `feature, context = claims[0], (claims[1] if len(claims) > 1 else None)`

Das Sichtmodell kennt einen Lauf, einen Feature-Claim und einen Kontext-Claim.
Lauf 2 „nachzutragen" hieße, eine zweite Crate danebenzulegen und die Prosa neu
zu tippen — und beim vierten Lauf wieder. Genau das hat der Owner untersagt.

---

## 2. Was gebaut wird: ein Weg, keine Nachträge

**Kernentscheidung:** Die Site hört auf, eine Zahl zu *behaupten*, und fängt an,
sie zu *zählen*. Danach ist ein neuer Lauf ein committetes Verzeichnis und kein
Textänderungsauftrag.

### 2.1 Auf der Runtime-Seite: `.github/workflows/export-crates.yml`

Ein Actions-Job nach dem Muster `research-run.yml` + `field-watch.yml`:

1. **Postgres-Service-Container** (`postgres:16`, Healthcheck), wie
   `research-run.yml:120-137`.
2. **Je Dump in `archive/dumps/`**, in einer Matrix: eine frische Datenbank
   anlegen, `psql < <dump>` restaurieren.
3. **`mrr export ro-crate --all-claims`** gegen diese Datenbank in ein
   Ausgabeverzeichnis, benannt nach dem Dump.
4. **Zustellen** nach `frankbueltge.de` über `ECOLOGY_TOKEN`, Muster
   `field-watch.yml:137-186` — als Branch mit Pull Request, **nie** auf `main`.
5. **Anlassgetrieben, kein Nightly.** `workflow_dispatch` **und** `push` auf
   `archive/dumps/**`. Ein neuer Dump ist eine neue Crate; eine unveränderte
   Nacht ist nichts. Damit gibt es weiterhin genau zwei nächtliche Routinen.

**Ohne `ECOLOGY_TOKEN`:** die Crates entstehen, werden als Artefakt angehängt,
und der Job sagt laut, dass die Zustellung nicht stattfand — dieselbe ehrliche
Degradation wie bei der Wache.

### 2.2 Auf der Site-Seite: ein Sichtmodell für n Läufe

- `src/data/meridian/export/<lauf>/` — je Lauf ein Verzeichnis statt einer
  flachen Crate. Der bestehende Inhalt wandert nach
  `export/k1t04-real-run-v2/`.
- `pipelines/meridian/refresh.py` iteriert über die Verzeichnisse. `parallax.json`
  bekommt ein `runs`-Array (je Lauf: Kennung, `object_count`, Claims,
  Verifikationen, `date_published`) und behält daneben unverändert `claim`,
  `verifications`, `sources`, `export_meta` für den **Feature-Lauf**, damit
  `/on-record` als Instrument bleibt, was es ist: **ein** Parallaxe-Fall,
  gründlich gezeigt.
- `MethodenblattOnRecord.astro` liest die Zahlwörter aus `runs` statt sie zu
  buchstabieren. Die Limitation auf `:81` wird von *„two claims from a single
  run"* zu einem gerechneten Satz über alle Läufe; `:30` und `:83` werden
  ebenso an die Ableitung gebunden.
- Der CI-Job bleibt unverändert und wird dadurch schärfer: er prüft jetzt, dass
  das Sichtmodell **aller** committeten Crates entspricht.

**Damit ist Lauf 4 kein Textauftrag mehr.** Der Export-Workflow legt ein
Verzeichnis vor, `refresh.py` zählt es mit, die Prosa stimmt von selbst, und der
CI-Job wird rot, wenn jemand das eine ohne das andere committet.

## 3. Was ausdrücklich nicht getan wird

- **Kein Nightly.** Es bleiben zwei nächtliche Routinen. Der Export feuert auf
  einen neuen Dump.
- **Kein Schreiben auf `main`** — weder hier noch drüben. Beide Seiten kommen
  als Pull Request.
- **Keine Änderung an den Dumps.** `archive/**` ist unantastbar; der Job liest
  sie und restauriert in eine Wegwerf-Datenbank.
- **Keine Änderung an `mrr export ro-crate`.** Das Kommando kann, was gebraucht
  wird; es fehlte nur der Anrufer. Das ist ein **Integrations-Paket** im Sinne
  von `AGENTS.md` („builds no capability … its acceptance criterion is a named
  operator path that works end to end") — genau die Lücke, die dort viermal
  beschrieben ist: eine fertige Schicht, deren Außenkante niemandem gehört.
- **Keine neue Aussage über die Läufe.** Der Export zeigt, was in den Dumps
  steht. Ob Lauf 2 inhaltlich etwas taugt, entscheidet dieser Vorgang nicht.

## 4. Offener Punkt, der beim Owner liegt

**Lauf 3 (`e2e-claims`) hat null Verifikationen.** Ein Claim ohne
Verifikationsergebnis ist im Archiv ein gültiger, aber unfertiger Zustand. Er
wird exportiert und gezählt wie die anderen; ob `/on-record` ihn zeigt oder das
Sichtmodell ihn als „nicht verifiziert" ausweist, ist eine Darstellungsfrage.
Vorschlag: mitzählen und ausweisen — verschweigen wäre die unehrlichere Variante,
und die Seite lebt davon, dass sie unfertige Zustände aushält.

## 5. Offene Befunde, unverändert weitergereicht

Aus N1-T04 §8 und N1-T05 §8, von diesem Paket **nicht** berührt: der
`verified`-Stempel im Extraktions-Arm; `--expect-sha256` optional im CLI; der
Guard-Fehler in `field-watch.yml:98-106`; kein stabiler Fingerabdruck eines
Befunds; die nicht implementierte Korroborations-Regel; null von sechzig
Unentscheidbaren.

**Und der neue aus N1-T05 §8.7, hier besonders einschlägig:** Lauf 2 der
*Messung* (`cc6df74`, Accuracy 0,5263, κ 0,2792) liegt auf
`origin/feat/n1-t04-modellgestuetzte-einordnung` und **nicht auf `main`**. Wer
die Streuung von 1,76 Prozentpunkten zitiert — und die Site sollte sie zitieren,
sobald sie eine Genauigkeitszahl zeigt — zitiert heute einen Branch.

> **Nicht verwechseln:** „Lauf 1/2/3" meint in dieser Notiz die **Synthese**läufe
> (die drei Dumps). Die **Messungs**läufe von N1-T04 sind eine andere Zählung
> mit denselben Ziffern. Wo beide vorkommen, werden sie benannt.
