# Meridian Classic — versiegelte Baseline 2026-07

Immutable Vergleichsreferenz gemäß ADR-0002 und MRR-GOV-022.

| Feld | Wert |
|---|---|
| Repository | `frankbueltge/field-research` (Meridian Classic) |
| Commit | `e60d6816e20d271431fe57c7c0dbcb75031bdd43` |
| Stand | origin/main, 2026-07-18 (Research-Session 44, Konsolidierung) |
| Tag | `meridian-classic-baseline-2026-07` (annotiert, auf obigem Commit) |
| Manifest | `MANIFEST.sha256` — SHA-256 aller 178 Dateien des Commit-Baums |

## Was diese Versiegelung bedeutet — und was nicht

- Die Baseline ist eine **Lesemarke für Vergleiche**: Jede spätere
  Gegenüberstellung von Classic- und MRR-Verhalten referenziert diesen
  exakten Zustand (GOV-024: exakte Versions-Attribution).
- Sie ist **kein Shutdown, keine Migration, kein Einfrieren der Praxis**
  (GOV-021, GOV-026). Meridian Classic arbeitet unter eigener Governance
  normal weiter; spätere Classic-Stände sind über Commits/Tags von dieser
  Baseline unterscheidbar (GOV-022).
- Importierte Classic-Forschungsobjekte erhalten in MRR den Status
  `legacy_unverified`, bis sie die MRR-Evidenz- und Verifikations-Contracts
  erfüllen (GOV-028).

## Verifikation

```bash
git -C <field-research> archive e60d6816e20d271431fe57c7c0dbcb75031bdd43 | tar -x -C /tmp/baseline
cd /tmp/baseline && shasum -a 256 -c <dieses Verzeichnis>/MANIFEST.sha256
```

Offene Rest-Deliverables von E0 (Inventar, Legacy-Objektkatalog,
Capability-Mapping, Benchmark-Seeds) sind eigene spätere Tasks.
