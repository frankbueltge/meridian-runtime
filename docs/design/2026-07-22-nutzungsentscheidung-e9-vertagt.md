# Nutzungs-Entscheidung: E9 vertagt, Verwenden vor Weiterbauen (2026-07-22, Abend)

**Status:** Entschieden — vom Owner (Frank) persönlich, in Session, 2026-07-22 abends,
nach der ehrlichen Bestandsaufnahme („macht die Bürokratie Sinn oder entwickeln wir
belanglose Software?") und deren Antwort. Dieses Dokument ergänzt und ändert den
Reihenfolge-Teil des Entscheidungs-Records vom selben Morgen
(2026-07-22-verifikations-entscheidung-und-bauprogramm.md, Entscheidung 2).

## Die Entscheidung

Owner, sinngemäß wörtlich: „Na dann machen wir das doch — und wenn E9 irgendwann
nützlich wird, bauen wir es nach."

1. **E9-T01…T07 werden NICHT auf Vorrat abgeleitet oder gebaut.** Die sieben
   Plan-Slots bleiben im Implementierungsplan stehen; einzelne Fähigkeiten daraus
   werden erst gebaut, wenn reale Nutzung sie konkret verlangt — dann als je eigenes
   Paket mit benanntem Nutzungsanlass in der Derivation.
2. **Bereits geschlossen bleiben geschlossen:** E9-T00 (Pre-Hardening-Batch,
   2026-07-21), E9-T00b (DRY-Konsolidierung, 2026-07-21), E9-T00c (Timezone-Bug der
   Event-Hash-Kette, 2026-07-22, PR #70) sind gemergt und unberührt.
3. **Der Weg ist Nutzung:** erstes reales Release durch das A4-Gate, Site-Kopplung,
   Publikation des Hammond-Parallaxe-Befunds — alles eigene, explizite Akte des
   Owners auf dessen Timing; nichts davon wird von einer Session terminiert.

## Kontext, ehrlich

E8 ist komplett und funktioniert (Export → PROV → Report → Release-Gate →
Banner/Supersession); der Zwei-Stimmen-Verifikationsentwurf hat am selben Tag einen
echten Befund produziert (Hammond-Dissens). Das Verhältnis Maschinerie zu Inhalt ist
zugleich extrem (~44k LOC Produktion, ~65k LOC Tests für 2 reale Claims); weiteres
Bauen ohne Nutzung würde für einen fiktiven Nutzer bauen. Der Owner wusste bis zur
Bestandsaufnahme nicht, dass das System bereits nutzbar ist — auch das ein Befund
über die Kommunikation der Sessions, nicht nur über die Software.

## Erster identifizierter Nutzungs-Bedarf (Empfehlung der Session, noch kein Auftrag)

Die autoritative Archiv-DB (beide Real-Run-Schemata) lebt auf der ausdrücklich als
Wegwerf-Instanz betriebenen lokalen Postgres (127.0.0.1:54329). „Benutzen" beginnt
sinnvollerweise mit Dauerhaftigkeit: ein minimaler, versionierter Dump ins Repo oder
eine persistente lokale Instanz. Das wäre der erste „E9-nach-Bedarf"-Kandidat
(die Miniatur von E9-T02), gebaut wenn der Owner es beauftragt.
