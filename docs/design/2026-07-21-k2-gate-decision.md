# K2-Tor: Entscheidung — VERTAGT (2026-07-21)

**Status:** Entschieden. Getroffen von der Hauptsession unter der dokumentierten
Owner-Delegation vom 2026-07-21 (siehe Governance-Commit zu E1-T03b); der Owner
kann diese Entscheidung jederzeit umstoßen.

**Gegenstand:** `docs/design/2026-07-21-research-method-kernel-plan.md`, Abschnitt K2,
stellt ein Entscheidungstor: Nach Vorliegen des realen K1-T04-Outputs ist zu
entscheiden, ob die kausalen Verträge (propose + human ruling, ausdrücklich KEINE
Kausal-Engines, Spec 08 §7) als nächstes abgeleitet werden.

## Entscheidung

**K2 wird jetzt NICHT abgeleitet.** Das Tor bleibt offen und wird bei einem der
unten genannten Trigger neu bewertet.

## Evidenzlage (Kurzfassung; Vollmemo in der Session-Scratchpad, DB-verifiziert
gegen Schema `mrr_k1t04_real_run_v2`)

| Prüfpunkt | Befund |
|---|---|
| K2-Vorbedingung (K1-T04-Output existiert) | ERFÜLLT |
| K1-T04-Akzeptanzkriterien (Claim-Tabelle, Unabhängigkeits-Check, ≥1 contested/insufficient) | ERFÜLLT |
| K1-Exit-Kriterien (Nachvollziehbarkeit, Ceiling-Disziplin, Lock-vor-Extraktion, Reproduzierbarkeit) | ERFÜLLT |
| Explizite „lohnt sich"-Checkliste im Plan | EXISTIERT NICHT — das Tor nennt nur Vorbedingung und Grenze |
| MRR-MTH-018 Sensitivitäts-Variationen ausgeführt | NICHT ERFÜLLT (befüllt, nie konsumiert) |
| Unabhängige Verifikation der beiden realen Claims | NICHT ERFÜLLT (`verification_ids` beidseitig leer, by design) |

## Begründung

1. **n=1, und der falsche Testfall für Kausalität.** Der einzige reale Lauf
   (Model-Collapse über kuratierten Kunst-Atlanten) bietet keine interventionale
   oder quasi-experimentelle Variation; genau ein Werk instanziiert den
   Mechanismus. Eine neue Claim-FORM einzuführen, die kein vorhandener
   Evidenzbestand sinnvoll ausüben kann, erzeugt ungetestete Governance-Fläche.
2. **Zwei billigere, nähere Lücken am BESTEHENDEN Profil sind offen.** Die
   Sensitivitäts-Variationen (MRR-MTH-018) werden befüllt, aber nicht
   ausgeführt, und keiner der beiden realen Claims ist unabhängig verifiziert.
   Beide Lücken schmälern den Wert des ersten echten Outputs direkt; ihre
   Schließung erhöht ihn — vor jeder neuen Claim-Form.
3. **E7/E8 sind entblockt und konkurrieren.** Der ursprüngliche Einwand gegen
   E7/E8 („leere Epics ohne echten Inhalt") ist mit dem realen K1-Output
   entfallen. Insbesondere E8 (Exporte) würde genau das Claim-Landscape
   transportfähig machen, über dessen Site-Kopplung der Owner noch entscheidet.
   Der Plan selbst nennt die Reihenfolge K2-vor-E7/E8 „a recommendation, not a
   decision".

## Wiedervorlage-Trigger (jeder einzelne genügt)

1. Eine Forschungsfrage mit echter kausaler Form UND einem Datenbestand mit
   interventionaler/quasi-experimenteller Variation liegt an.
2. MRR-MTH-018-Ausführung UND unabhängige Verifikation der beiden realen Claims
   sind gelandet (dann ist das bestehende Profil ausgereizt und die nächste
   Fähigkeitsstufe wieder dran).
3. Ein zweiter realer Lauf ist abgeschlossen (n≥2 verändert die Abwägung unter 1.).

## Konsequenz für die Arbeitsreihenfolge

Nach E1-T03b und der DRY-Konsolidierung: **MTH-018-Sensitivitäts-Ausführung als
eigenes Packet** vor neuen Claim-Formen; die Verifikations-Frage (wer verifiziert
die realen Claims, ohne AGENTS-Regel 8 zu verletzen?) wird als eigene
Design-Frage aufgemacht, nicht nebenbei entschieden. E8-vor-E7 wird erwogen,
falls die Site-Kopplung kommt (Owner-Werk-Entscheidung, aussteht).

Die harte Grenze aus Spec 08 §7 — keine Kausal-Engines, nur propose + human
ruling — bleibt von dieser Vertagung unberührt und gilt unverändert, wann immer
K2 später abgeleitet wird.
