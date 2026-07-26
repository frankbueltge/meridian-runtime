# Fact-Lock: die Modell-Außenkante — was ein konkreter Provider-Adapter wirklich verlangt (2026-07-26, Nacht)

**Status:** Fact-Lock vor der Ableitung, Owner-Vorlage. **Kein Bau, keine
Änderung am Code.** Geprüft wurde, was gebaut werden müsste, damit MRR zum ersten
Mal ein reales Modell aufruft — Sprosse 1 der Leiter aus
`2026-07-26-ableitung-eigenexperiment-orchestrierung.md`.

Der Port ist vollständig und gut. Das Hindernis liegt woanders.

## Befund 1 — der Port verlangt wenig, und was er verlangt, ist sauber

`ModelAdapter` ist ein `runtime_checkable` Protocol mit **einer** Methode:

```python
def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome: ...
```

Beide Seiten sind eng geschnitten und tragen die Disziplin des Projekts bereits
in sich:

- **`ModelInvocationRequest`** führt `model_profile_id` **und**
  `model_profile_hash` — welches Profil in **welcher exakten Fassung** den Aufruf
  regiert, per Inhaltshash gepinnt. Dazu `prompt_text` (nur im Speicher),
  `operation_kind`, `redaction_policy`, `tool_names_available`.
- **`ModelInvocationOutcome`** hält fünf **getrennte** Endzustände —
  `completed, refused, content_filtered, error, timed_out` — ausdrücklich nie zu
  einem generischen Fehler zusammengezogen (AGENTS: „collapsing … into one
  generic error"). `response_hash` existiert **genau dann**, wenn
  `status == "completed"`; der Konstruktor erzwingt beide Richtungen.
- **`apply_redaction`** macht die sichere Wahl strukturell: unter
  `"hashes_only"` gibt es den Rohtext **nie** zurück. Rohtext zu behalten
  verlangt ein ausdrückliches `"raw_permitted"`, nie eine implizite Ableitung.

Ein Adapter muss also nichts erfinden. Er muss einen HTTP-Aufruf machen und
dessen Ausgang **ehrlich** auf diese fünf Zustände abbilden.

## Befund 2 — die Netzpolitik wird verzeichnet, nicht durchgesetzt

Das ist der eigentliche Befund dieses Fact-Locks.

Beide Orchestrierungspfade setzen die Netzpolitik jedes Laufs **hart** auf
Verweigerung:

```
services/control_plane/mrr/services/cli/orchestration.py:417
services/control_plane/mrr/services/cli/synthesis_orchestration.py:604
    "network_policy": {"mode": "deny_all", "allowlist": []}
```

Und `run_manifest.py::_network_permitted` schreibt daraus in jedes `RunManifest`,
welche Netzzugriffe **erlaubt waren** — bei `deny_all` eine leere Liste.

**Es gibt jedoch nirgends eine Durchsetzung.** Die Isolationszusagen — „non-root,
read-only base filesystem, explicit writable mounts, **deny-by-default network
egress**, cgroup limits" — stehen in `exceptions.py` als Beschreibung dessen, was
der `ReferenceTaskExecutor` **nicht** leistet:

> „isolation is **the deferred OCI-executor adapter's responsibility**"

Dieser Adapter existiert nicht. `ReferenceTaskExecutor` wirft sogar beim Versuch,
ihn mit `require_isolation=True` zu bauen.

Heute ist diese Lücke folgenlos: **nichts im System macht einen Netzaufruf.** Die
Aussage „nichts war erlaubt" und die Wirklichkeit „nichts geschah" stimmen
zufällig überein.

**Ein Modell-Adapter beendet diese Übereinstimmung.** Er wäre der erste
Bestandteil, der aus einem Lauf heraus wirklich ins Netz geht — in einen Lauf
hinein, dessen eigenes Manifest festhält, dass nichts erlaubt war. Ohne
Gegenmaßnahme entstünde **eine falsche Aussage im Archiv**, und zwar in genau der
Kategorie, für die dieses Projekt gebaut wurde.

Das Paket ist damit nicht „schreib einen Adapter". Es ist „schreib einen Adapter
**und mach die Akte darüber wahr**".

## Befund 3 — es gibt keinen HTTP-Client, und die Wahl hat eine bekannte Falle

Die Produktionsabhängigkeiten sind sieben, alle netzfrei:
`rfc8785, cryptography, python-ulid, pydantic, sqlalchemy, psycopg, alembic`.

Für den Aufruf gibt es drei Wege, und keiner ist folgenlos:

| Weg | Kosten | Falle |
|---|---|---|
| stdlib `urllib` | keine neue Abhängigkeit; wie `scripts/` es schon tut | **erbt die offene Redirect-Lücke**: urllib folgt Umleitungen, die Allowlist prüft nur die *angeforderte* URL (S1-Ableitung, bis heute offen) |
| generischer HTTP-Client (`httpx`) | eine Abhängigkeit, `pip-audit`-Fläche | Redirect-Verhalten muss ausdrücklich abgeschaltet werden |
| Provider-SDK | eine große Abhängigkeit mit Unterabhängigkeiten | E4-T01 hat SDKs namentlich ausgeschlossen (`openai, anthropic, google-generativeai, boto3, litellm`) — ein Wiedereinzug wäre zu begründen |

Bemerkenswert: **die Redirect-Lücke ist damit keine Nebensache mehr.** Sie war
Option (c) des Handoffs, ein kleines Sicherheitsloch. Sobald ein Adapter über
`urllib` gegen eine Provider-Adresse spricht, wird sie zur Frage, ob der
Prompt — samt allem, was er trägt — auch wirklich beim gemeinten Empfänger
landet. Die beiden Aufgaben hängen zusammen, was vorher niemand gesehen hat.

## Befund 4 — der Schlüssel ist geregelt, der Weg dorthin nicht gebaut

Die Verwahrung ist entschieden (2026-07-25): Provider-Key als GitHub-Secret, nie
im Repo, **nie in einem Prompt** (AGENTS Regel 11). Daraus folgt, dass der
Modell-Schritt in Actions läuft, nicht am Schreibtisch.

Geprüft: **kein Workflow im Repository referenziert bisher irgendein Secret.**
Die Entscheidung ist getroffen und die Leitung dorthin fehlt — dasselbe Muster
wie beim Signieren.

Für den Adapter heißt das konkret: er liest den Schlüssel aus der Umgebung, nie
aus einem Argument, nie aus einer Datei im Repo; und **keine Fehlermeldung darf
ihn tragen** — auch nicht mittelbar über eine URL mit Query-String.

## Befund 5 — eine Entscheidung mit Datenschutzgewicht, die leicht durchrutscht

`DEFAULT_REDACTION_POLICY` ist `"hashes_only"`: standardmäßig wird **kein**
Prompt- und Antworttext aufbewahrt, nur dessen Hash.

Für ein Experiment, das Modellverhalten **misst**, ist das vermutlich zu wenig —
ohne Rohtext lässt sich hinterher nicht zeigen, *woran* ein Modell scheiterte,
nur *dass* es scheiterte. Der Wechsel auf `"raw_permitted"` ist technisch ein
Wort. Er ist inhaltlich die Entscheidung, Modelltexte dauerhaft ins Archiv zu
legen.

Das ist eine Owner-Entscheidung und keine Implementierungsdetailfrage. Sie wird
hier benannt, damit sie nicht als Nebenwirkung eines Pakets getroffen wird.

## Was daraus für das Paket folgt

Ein ehrlicher Zuschnitt hat **drei** Teile, nicht einen:

1. **Der Adapter selbst** — ein konkreter Provider, ein HTTP-Aufruf, ehrliche
   Abbildung auf die fünf Endzustände (besonders: `refused` und
   `content_filtered` getrennt halten, `timed_out` nicht zu `error` machen),
   Schlüssel aus der Umgebung, kein Leck in Fehlermeldungen.
2. **Die Netzpolitik wahr machen** — ein Lauf mit Modell-Schritt darf nicht
   `deny_all` verzeichnen. Andernfalls lügt das Manifest.
3. **Die Redirect-Lücke schließen**, falls der Weg über `urllib` führt (Befund 3).

Teil 2 ist der Grund, warum dieses Paket nicht klein ist. Teil 2 ist auch der
Grund, warum es sich lohnt: es zwingt das System, über seinen ersten Netzaufruf
die Wahrheit zu sagen.

## Zwei Owner-Entscheidungen, die dieser Fact-Lock nicht rät

1. **Welcher Provider** — bestimmt die Abhängigkeitsfrage aus Befund 3 mit.
2. **`hashes_only` oder `raw_permitted`** für das Experiment (Befund 5).

Beide sind vor dem Bau nötig, keine vor der Ableitung.

## Was dieser Fact-Lock NICHT behauptet

- **Nicht**, dass der Port geändert werden müsste. Er ist vollständig und wird
  unverändert erfüllt.
- **Nicht**, dass heute jemand eine falsche Aussage ins Archiv geschrieben hat.
  Die Übereinstimmung „nichts erlaubt / nichts geschehen" gilt bis zum ersten
  Adapter.
- **Nicht**, dass Isolation gebaut werden muss. Ein Adapter, der in Actions
  läuft, braucht keinen OCI-Executor — er braucht ein Manifest, das nicht das
  Gegenteil behauptet.
