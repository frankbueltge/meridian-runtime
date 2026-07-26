# E5-T11-Ableitung: die Identitäts-Außenkante — ein Kommando, das aus einem Schlüssel eine Praxis macht (2026-07-26)

**Status:** Ableitung, fact-locked gegen
`docs/design/2026-07-26-fact-lock-praxis-identitaet-und-schluessel.md`.
Governance-Commit **vor** dem Bau. Owner hat Strang 1 (korrigiertes (a)) am
2026-07-26 abends gewählt.

## Anlass — real, benannt, kein Vorratsbau

`mrr federation inbox accept` verlangt `--trusted-sender-practice` als **Datei**.
Solange Meridian kein Practice-Objekt veröffentlicht, kann Ulysses eine Sendung
Meridians nicht annehmen. Das ist der Nutzungsanlass im Sinne der
use-first-Doktrin (2026-07-22): ein benannter Empfänger braucht eine bestimmte
Datei, heute.

Der zweite, unabhängige Anlass: `mrr federation envelope sign --key-id` verlangt
einen `kid`, den **kein Kommando im Repository herstellt** (Fact-Lock Befund 2),
und prüft ihn nicht gegen `--key-file` (Befund 3). Beides zusammen bedeutet, dass
Meridians Schlüssel im Augenblick seiner Entstehung unbenutzbar ist.

## Der tragende Entwurfsgriff — aus E5-T10 übernommen, nicht neu erfunden

E5-T10 stand oder fiel an einer Regel: `payload_content_hash` wird **aus dem
Payload gelesen**, weder berechnet noch vom Aufrufer entgegengenommen — weil der
Absender verweigern muss, was der Empfänger verwerfen würde.

Hier gilt die exakte Entsprechung, eine Ebene tiefer:

**`kid` und `encoded_public_key` werden aus dem privaten Schlüssel abgeleitet,
niemals als Parameter angenommen.** Die Domänenfunktion nimmt den
`Ed25519PrivateKey` entgegen und gewinnt die öffentliche Hälfte selbst
(`private_key.public_key()`), dann `encode_public_key` und `derive_key_id` —
beide unverändert.

Das ist der Unterschied zwischen „Befund 3 abmildern" und „Befund 3 unmöglich
machen": es existiert kein Parameter, über den ein falscher `kid` hineinkäme. Ein
Practice-Objekt aus diesem Kommando kann strukturell keinen Schlüsselnamen
führen, der nicht zu seinem Schlüssel gehört — und `PublicKeyDescriptor`s eigener
Validator (`kid == derive_key_id(encoded_public_key)`) prüft es ein zweites Mal,
gemäß dem im Vertrag dokumentierten „enforced twice"-Präzedenzfall.

## Warum ein eigenes Top-Level-Kommando, nicht `mrr federation practice`

`Practice` ist eine erstklassige Entität (E5-T01), keine Transportsache. Ihre
Verbraucher sind breiter als die Föderation: `practice_key_ring` speist
`manifest_trust`, `task_trust`, `crate_trust`, `transfer_trust`. Sie unter
`federation` zu hängen, würde eine Identität als Eigenschaft ihres Transports
ausgeben. `main.py` registriert bereits vierzehn Unterkommandos über dasselbe
`register_*_subcommand`-Muster; ein fünfzehntes ist die etablierte Form, kein
Sonderweg.

## Ein Kommando, nicht zwei

Erwogen und verworfen: ein zusätzliches `mrr practice key-info`, das nur `kid`
und `encoded_public_key` ausgibt. Verworfen, weil das Practice-JSON diese Werte
bereits autoritativ trägt (`keys[0].kid`) und ein zweites Kommando eine zweite
Quelle für dieselbe Wahrheit schüfe. `init` gibt den `kid` in seiner
Ergebnis-Ausgabe mit aus — das genügt dem realen Bedarf (`--key-id` für
`envelope sign`), ohne eine Oberfläche auf Vorrat zu bauen.

## Die Selbstsignatur ist nicht optional

Der Vertrag lässt `signature: Signature | None` zu. Dieses Kommando signiert
**immer**. Ein Vertrauensanker, der sich nicht selbst signiert, beweist nichts
über den Besitz des privaten Schlüssels — und genau das ist der Zweck der Datei,
die Ulysses als `--trusted-sender-practice` lädt. Signiert wird über die
**unveränderte** `mrr.domain.hashing_policy` (`compute_content_hash`,
`sign_object`); der Vertrag verlangt es wörtlich: „never a second
hashing/signing implementation".

Daraus folgt, dass der private Schlüssel für diesen einen Lauf lokal vorliegen
muss. Das ist deckungsgleich mit der Verwahrungs-Entscheidung vom 2026-07-25:
„Die Schlüsselerzeugung … geschieht lokal; die private Hälfte wird ins
GitHub-Secret eingetragen und danach lokal gelöscht, die öffentliche committet."

## Inhaltliche Felder sind Argumente, niemals Vorgaben

`name`, `description`, `governance_contacts`, `supported_policy_versions`,
`disclosure.max_disclosure`, `disclosure.trust_statement`, das
Schlüssel-Gültigkeitsfenster, `created_by` und optional
`capability_registry_endpoint` sind **Owner-Inhalt**. Das Kommando hat für keines
davon einen Vorgabewert und erfindet keines. Fehlt eines, ist das eine typisierte
Verweigerung, kein stillschweigend gefüllter Platz (AGENTS Regel 3 und 12).

Das entkoppelt den Bau von der Owner-Entscheidung: **das Paket ist vollständig
baubar und testbar, bevor Frank ein einziges Feld beantwortet hat** — mit
Testschlüsseln und Testinhalten, genau wie E5-T10 ohne Schlüssel baubar war.

## Offener Punkt, der beim Fahren zu entscheiden ist (kein Baustopp)

`created_by` ist eine URN und verlangt eine Antwort mit Gewicht: Wird Meridians
Identität von einer **Person** gestiftet (Frank) oder von einer **Agent-Rolle**
(dem Kollektiv)? Die Verfassung der Praktiken spricht für die Rolle, die
Rechenschaft für die Person. Das Kommando nimmt beides entgegen und entscheidet
nichts; die Entscheidung fällt in dem Moment, in dem Frank es fährt, und wird
dann in der Ableitung des Laufs vermerkt.

`id`/`practice_id` werden frisch geprägt (`new_urn("practice")`, Fact-Lock
Befund 4) und sind identisch — eine Practice gehört sich selbst, wie
`examples/practice.example.json` es zeigt und der Validator
(`signature.signer_practice_id == id`) es verlangt.

## Das Akzeptanz-Orakel — VOR dem Bau festgelegt

Der scharfe Fall ist nicht „das JSON validiert". Er ist:

> **Ein von diesem Kommando erzeugtes Practice-Objekt wird von der unveränderten
> Empfängerseite als `--trusted-sender-practice` akzeptiert, für ein Bündel, das
> mit demselben Schlüssel signiert wurde.**

Damit schließt der Test genau die Kante, die die Wegkarte als Punkt 4 führt, und
er prüft sie gegen **fremden, unveränderten Code** statt gegen sich selbst. Die
Gegenprobe dazu ist ebenso scharf: eine Practice aus Schlüssel A darf ein
Envelope aus Schlüssel B **nicht** legitimieren.

Der Prüfer implementiert dieses Orakel unabhängig vom Erbauer (AGENTS Regel 8);
der Erbauer verifiziert sein eigenes Ergebnis nicht.

## Was dieses Paket ausdrücklich nicht tut

- **Keine Identität für eine fremde Praxis.** Kein Schlüssel, keine Node-ID, kein
  Practice-Objekt für Ulysses — die Grenze aus der E5-T08-Ableitung, unverändert.
- **Kein Austausch.** Das Paket erzeugt die Datei; es verschickt nichts.
- **Keine Änderung an der Föderation.** `federation_main.py`,
  `envelope_validation.py`, `offline_bundle.py` bleiben unberührt.
- **Keine zweite Hash-/Signaturimplementierung**, keine Änderung an `keys.py`.
- **Kein Netz, keine Datenbank, kein Modell, keine neue Abhängigkeit, keine
  Migration.**
- **Keine Heilung der Archivsignaturen.** Fact-Lock Befund 5 (`node-key-1`,
  `origin-key-1` sind gegen jede gültige Practice unprüfbar) bleibt bestehen und
  wird von diesem Paket weder behoben noch verdeckt. Er ist ein eigener,
  datierter Defekt.
