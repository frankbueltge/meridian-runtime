# MRR — Abstract und ehrliche Positionierung

**Status:** Kommunikations-/Positionierungs-Record. Verfasst 2026-07-22 (abends, auf
Nachfrage des Owners: „wie würdest du die Engine in einem Abstract zusammenfassen — und ist
das relevant oder belanglos?"), committet 2026-07-23. Kein Anspruchs-Dokument: die
Neuheits-Behauptungen unten sind bewusst kalibriert, mit benannten Grenzen.

---

## 1. Wissenschaftlicher Abstract (Publikations-Stil, EN)

**Meridian Research Runtime: A Deterministic Substrate for Verifiable, AI-Assisted Research**

> AI-assisted inquiry produces plausible claims faster than they can be checked, and
> existing tooling treats verification, provenance, and disclosure as aspirations rather
> than enforced properties. We present the Meridian Research Runtime (MRR), a runtime in
> which those properties are load-bearing by construction. MRR separates an append-only,
> hash-chained event log and content-addressed sealed artifacts (the authoritative record)
> from all narrative output (strictly derived projections that are never the primary
> record). No language model participates in the runtime core: models are declared,
> auditable instruments whose outputs enter the claim graph only after schema and domain
> validation, never as authoritative state. Claims carry evidence and counter-evidence
> with source families that distinguish corroboration from mere source count; verification
> is mandatory, self-verification is structurally prohibited, and independence is recorded
> as an honestly-declared profile rather than assumed. Critically, MRR *preserves
> disagreement*: conflicting verifications of the same claim both remain on record, never
> averaged into false consensus. We demonstrate MRR end-to-end on a real study of "model
> collapse" in contemporary AI art, in which a blind primary-source re-check and an
> externally-governed second reviewer reached *opposing* verdicts on the same
> classification — a substantive finding that no single verification path would have
> surfaced, and which the runtime exports as an offline-verifiable RO-Crate/W3C-PROV bundle
> and renders as a human-readable report with the disagreement preserved. External
> publication is gated behind an explicit human approval event. We discuss what remains
> unproven: independence under a single responsible principal, and federation across
> practices as designed-but-unexercised.

---

## 2. In einfachen Worten (DE, für Nicht-Fachleute)

Künstliche Intelligenz stellt heute in Minuten Behauptungen auf, die glaubwürdig klingen —
aber niemand kann so schnell prüfen, ob sie stimmen. Fast alle Werkzeuge, die KI beim
Forschen helfen, *versprechen* Vertrauenswürdigkeit und Nachvollziehbarkeit, aber sie
*erzwingen* sie nicht: Am Ende steht eine Antwort, und man muss dem System glauben.

MRR dreht das um. Es ist kein Assistent, der Antworten ausspuckt, sondern ein Archiv mit
eingebauten Regeln, die sich nicht umgehen lassen:

- Jede Behauptung muss Belege mitführen — **und Gegenbelege.**
- Nichts gilt als „bestätigt", bevor es jemand **unabhängig** geprüft hat — und niemand
  darf die eigene Arbeit selbst abnehmen.
- Die KI trifft **keine** Entscheidungen. Sie ist ein Werkzeug, dessen Vorschläge erst
  geprüft werden, bevor sie ins Archiv dürfen — nie ein Orakel, dem man glaubt.
- Alles im Archiv trägt eine **fälschungssichere Kette aus Prüfsummen**: jede nachträgliche
  Änderung fällt sofort auf.
- Und das Kernstück: **Widersprechen sich zwei Prüfer, bleiben beide Urteile stehen.** Das
  System mittelt sie nicht zu einer Scheinzahl weg.

Erprobt an einem echten Fall — der Frage, ob bestimmte KI-Kunstwerke „Modell-Kollaps"
wirklich *verkörpern* oder nur *davon handeln*. Zwei unabhängige Prüfungen kamen zu
**entgegengesetzten** Ergebnissen; genau dieser Widerspruch war der eigentliche Befund.

## 2a. „Dissens-Erhaltung" am konkreten Fall

Normale Systeme machen aus zwei widersprechenden Prüfungen *eine Zahl* („73 % Konfidenz")
und verstecken damit die interessanteste Information: dass es einen Widerspruch gibt und
warum. MRR verbietet das. Der Hammond-Fall:

1. Die Pipeline klassifizierte Felicity Hammonds *Model Collapse* als echte **Verkörperung**
   von Modell-Kollaps (eigener KI-Output über Generationen als Trainingsinput zurückgefüttert).
2. Ein **blinder Prüfer**, der nur die gespeicherte Beschreibung sah, stimmte zu.
3. Ein **externer Prüfer** (die Ulysses-Praxis) ging an die Ausstellungsdokumentation und
   fand: übertrieben. Dokumentiert ist genau **ein** Durchlauf, nicht die geforderten zwei;
   und das Werk fotografiert jedes Mal echte Installationen neu ab — was laut den zitierten
   Theorie-Papern das **Gegenmittel** gegen Kollaps ist, nicht die Krankheit. Das Werk
   *benennt* den Kollaps, während sein Mechanismus das *Heilmittel* vollzieht.

Zwei entgegengesetzte Urteile (`pass`/`fail`) bleiben dauerhaft nebeneinander im Archiv,
der Claim bleibt ehrlich „umstritten". Der Widerspruch ist kein Fehler, der aufzulösen wäre
— **er ist der Befund**: Er zeigt, dass die Quellbeschreibung selbst ungenau war. Kein
einzelner Prüfer hätte das gesehen, keine Durchschnittszahl es je verraten.

---

## 3. Ehrliche Positionierung: relevant oder belanglos?

**Weder trivial noch fertig — aber die Idee ist echt, und der eine Satz, der Forscher
interessieren würde, ist wahr:** *„das Verfahren produzierte einen Dissens, den kein
einzelner Prüfpfad gefunden hätte."* Demonstrierbar an echten Daten, nicht behauptet.

**Genuin ungewöhnlich** (nicht „einzigartig" — aber selten als *laufendes System*):
- **Dissens-Erhaltung als erste-Klasse-Eigenschaft** (Invariante MRR-FR-077). Die stärkste,
  am ehesten verteidigbare Neuheit.
- **Kein LLM im Kern.** Die meisten „trustworthy-AI-research"-Systeme *sind* LLM-Wrapper.
- **Ehrliche Unabhängigkeits-Deklaration statt Independence-Laundering** — ein Problem, das
  die Community benennt, aber selten operationalisiert.

**Nächste Verwandte** (zur Einordnung): W3C PROV und RO-Crate (Standards, keine Runtime),
Nanopublications (Assertion+Provenance+Publikationsinfo — konzeptionell am nächsten),
Reproducibility-Infrastruktur (Whole Tale, Renku). MRRs Alleinstellung wäre die *Verzahnung*
— erzwungene Verifikation + Dissens-Erhaltung + deterministischer Kern in einem Stück —
nicht die Einzelteile.

**Was ehrlich noch fehlt, bevor das Feld es ernst nimmt:**
1. **N ist winzig** — 18 klassifizierte Zeilen, 2 reale Claims. Ein zweites, thematisch
   unabhängiges Fallbeispiel würde aus „Prototyp" „Methode" machen.
2. **Föderation ist Vaporware** — designt, nie mit einem zweiten Node/einer zweiten Praxis
   über den Runtime-Signatur-Kanal gelaufen (der Ulysses-Austausch lief menschlich/git,
   nicht über das Protokoll). Im Abstract als Limitation deklariert, nicht versteckt.
3. **(erledigt 2026-07-22)** Die Naht, die die reale Forschungsausgabe unexportierbar
   machte (Crate↔Claim-Abkopplung, Befund 2), ist mit E8-T06 geschlossen — die 2 realen
   Claims samt Hammond-Dissens sind jetzt als RO-Crate/PROV exportierbar und als Report
   darstellbar.

**Fazit:** Nicht belanglos. Ein ungewöhnlich ehrlich gebautes, konzeptionell scharfes System
mit einem echten, vorzeigbaren Befund — dünne Empirie (Punkt 1), ungenutzte Föderation
(Punkt 2). Aufmerksamkeit wäre erreichbar in dieser Reihenfolge: **Empirie verbreitern
(zweites Fallbeispiel) → das Verfahren sichtbar machen (der Graph-/Site-Zugang) → dann das
Paper.**
