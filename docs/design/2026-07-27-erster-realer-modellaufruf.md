# Der erste reale Modellaufruf — und was er sofort produzierte (2026-07-27, nach Mitternacht)

**Status:** Ereignis-Vermerk. Kein Lauf, kein `RunManifest`, kein Archivobjekt.
Ein Operator-Handgriff außerhalb des Systems, unmittelbar nach dem Merge von
E4-T08. Er wird hier festgehalten, weil er die Modell-Außenkante zum ersten Mal
von **gebaut** auf **benutzt** stellt — und weil er in derselben Minute einen
Befund lieferte, der die These des Projekts betrifft.

## Der Aufruf

| | |
|---|---|
| Zeitpunkt | 2026-07-27, kurz nach Mitternacht (Session vom 26. Juli) |
| Adapter | `GeminiModelAdapter` (E4-T08), `UrllibHTTPTransport`, Umleitungen abgeschaltet |
| Modell | **`gemini-2.5-flash-lite`** — bewusst gepinnt, nicht `gemini-flash-latest` |
| Endzustand | **`completed`** |
| `prompt_config_hash` | `sha256:9e619ea5acaa2e04abe30374a5998e3d4832c3e62cfdf13f48d433997a801072` |
| `response_hash` | `sha256:5eaf8b164ba6dcad5f9f2dec5e33e6bc7e43821627e0bef7b9be885f9955939a` |
| Token | 35 Prompt, 87 Antwort, 122 gesamt |
| Redaktionspolitik | `raw_permitted` (Owner-Entscheidung vom 26. Juli) |

Die Frage lautete, in höchstens drei Sätzen: was ist „model collapse" bei
generativen Modellen, die auf synthetischen Daten trainiert werden, und was ist
der meistgenannte Fehlermodus?

## Warum das Modell gepinnt gehört, und nicht `…-latest`

Fünf Modelle waren auf diesem Konto verfügbar, darunter `gemini-flash-latest`.
Gewählt wurde die **gepinnte** Fassung. Ein gleitender Alias wäre ein Loch genau
an der Stelle, die dieses System zu schließen versucht: `ModelProfile` nagelt das
Modell per Inhaltshash fest, während ein `…-latest`-Name irgendwann
stillschweigend auf ein anderes Modell zeigt. Zwei Messungen wären dann nicht
vergleichbar, ohne dass sich im Repository eine Zeile geändert hätte.

**Für jedes künftige Experiment gilt: Modellversion gepinnt und im Befund
genannt.** Das gehört in die Ableitung von Sprosse 2.

## Der Befund: die erste echte Antwort war teilweise falsch

Wörtlich geantwortet wurde unter anderem:

> „The single most commonly cited failure mode is **mode collapse**, where the
> model generates only a small subset of the possible data variations …"

Das ist eine Verwechslung zweier verschiedener Konzepte.

### Gegengeprüft am gepinnten Theorie-Atlas des Repositories

`corpora/model-collapse/theory-atlas.snapshot.json`,
`sha256:f712ea4e9c6b9137fa180ad91e73a86d8d09862792f33174c77acd76a891e610`,
87 kuratierte Einträge — derselbe Atlas, auf dem die beiden Real-Runs vom
21. Juli fußen.

| Prüfung | Ergebnis |
|---|---|
| Vorkommen von „mode collapse" im Atlas | **0 von 87 Einträgen** |
| Kanonische Definition im Atlas (`shumailov-curse-of-recursion`) | „generative models degrade when trained on their own output — model collapse: **the tails of the distribution vanish first**"; peer-reviewed als *Nature* 631 (2024) |

Der meistgenannte Fehlermodus ist im Atlas also das **Verschwinden der
Verteilungsränder** über rekursive Generationen — nicht *mode collapse*, ein
Begriff, der in diesem Korpus überhaupt nicht auftaucht. *Mode collapse* stammt
aus der GAN-Literatur und beschreibt einen Generator, der viele Eingaben auf
wenige Ausgaben abbildet: ähnliches Symptom (Vielfalt geht verloren),
verschiedener Mechanismus, verschiedene Literatur.

Auch der erste Satz der Antwort verschiebt: er nennt „overfit to the
characteristics of that synthetic data", während die kanonische Fassung den
rekursiven Generationenprozess in den Mittelpunkt stellt. Das ist unscharf, aber
nicht falsch — die Verwechslung im zweiten Satz ist es.

**Grenze dieser Prüfung, ausdrücklich:** verglichen wurde gegen den gepinnten
Atlas dieses Projekts, nicht gegen die Gesamtliteratur. Der Atlas ist eine
kuratierte Auswahl; „kommt hier nicht vor" ist ein starkes, aber kein
erschöpfendes Argument.

## Warum das mehr ist als eine Anekdote

Der erste reale Aufruf dieses Systems hat in 87 Wörtern genau das produziert,
wogegen das System gebaut wurde: eine flüssige, selbstsichere, in einem prüfbaren
Punkt falsche Auskunft. Ohne Prüfung wäre sie durchgegangen — sie klingt richtig.

Und der Adapter hat sie **korrekt** als `completed` verzeichnet. Das ist kein
Mangel: an der Leitung ist ein inhaltlicher Fehler nicht sichtbar. Es ist exakt
die Grenze, die beim Bau von E4-T08 gemeldet und in dessen Ableitung datiert
korrigiert wurde — `refused` erkennt Schweigen, nicht Irrtum.

Damit steht die Arbeitsteilung, die das ganze Projekt behauptet, an ihrem ersten
echten Fall:

- Die **Transportebene** sagt wahrheitsgemäß: eine Antwort kam an, sie hat diesen
  Hash, sie kostete 122 Token.
- Ob der **Inhalt** trägt, sagt sie nicht und darf sie nicht sagen.
- Das musste eine andere Instanz prüfen — heute Nacht von Hand, gegen einen
  gepinnten Snapshot.

Genau diese Trennung ist der Grund für die fünf getrennten Endzustände, für
`response_hash` statt Text als autoritatives Feld, und für AGENTS Regel 7 („No
model output may directly become authoritative state").

## Was daraus folgt

1. **Sprosse 2 hat ihren ersten realen Testfall.** Diese eine Antwort ist ein
   fertiges Beispiel für den Goldstandard-Vergleich: eine prüfbare Behauptung,
   ein gepinnter Referenzkorpus, ein feststellbarer Fehler.
2. **Die Unterscheidung „Weigerung / Irrtum" ist ein Messgegenstand**, kein
   Vorverarbeitungsschritt — ein Modell, das flüssig irrt, sieht an der Leitung
   aus wie eines, das richtig antwortet.
3. **Offene Beobachtung aus demselben Abend, hier nur vermerkt:** HTTP 429
   (Kontingent erschöpft) und ein unparsbarer Antwortkörper landen beide auf
   `error`, obwohl das eine ein Wiederholen nahelegt und das andere ein Aufgeben.
   Das sitzt nicht im Adapter, sondern in `TerminalStatus` aus E4-T01. Kandidat
   für eine Spezifikationsfrage, kein schneller Patch.

## Was dieser Vermerk NICHT behauptet

- **Nicht**, dass ein Lauf stattgefunden hätte. Es gibt kein `RunManifest`, keine
  Orchestrierung, kein Archivobjekt. Die Verdrahtung bleibt gesperrt, bis die
  Netzpolitik wahr gemacht ist.
- **Nicht**, dass ein Aufruf eine Aussage über das Modell erlaubt. Eine einzelne
  Antwort ist eine Anekdote, keine Messung — das ist der Unterschied zwischen
  diesem Vermerk und Sprosse 2.
- **Nicht**, dass die Verwechslung ein Beleg für Modellversagen im Allgemeinen
  ist. Sie ist ein Datenpunkt, gegen einen kuratierten Korpus geprüft, mehr nicht.
