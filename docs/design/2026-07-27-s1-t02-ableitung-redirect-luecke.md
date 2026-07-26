# S1-T02-Ableitung mit Fact-Lock: die Redirect-Lücke (2026-07-27, Nacht)

**Status:** Ableitung, Fact-Lock inline (der Befund ist klein und an zwei Stellen
vollständig nachweisbar). Governance-Commit **vor** dem Bau.

## Der Befund, erstverifiziert

Beide Fetch-Skripte prüfen die Allowlist **vor** dem Abruf und danach nie wieder:

```
scripts/fetch_source_content.py:371-374
scripts/fetch_citation_resolutions.py:305-311
    _check_allowlisted(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=…) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
```

`_check_allowlisted` ist gut: `urlsplit(...).hostname`, kein Teilstring-Vergleich,
typisierte Verweigerung vor dem ersten Socket, `https` erzwungen, Hosts exakt
`export.arxiv.org` und `api.crossref.org`.

Geprüft wird damit aber **die angeforderte URL, nicht die abgerufene.** Weder
`response.url` noch `response.geturl()` noch eine Umleitungskette wird danach
angesehen, und keines der Skripte installiert einen eigenen Opener — also ist
`urllib`s vorgegebener `HTTPRedirectHandler` aktiv und folgt 30x still.

**Ein erlaubter Host kann den Abruf an jeden beliebigen anderen Host
weiterreichen, und die Bytes von dort kommen zurück, als wären sie von ihm.**

## Warum das hier schwerer wiegt als bei einem gewöhnlichen Abrufer

Die Bytes dieser Skripte werden nicht angezeigt, sie werden **Quellinhalt hinter
einem EvidenceAnchor**. Eine stille Umleitung holt also nicht bloß die falsche
Seite — sie legt ungeprüfte Bytes hinter einen Hash, den das Archiv als
verankerte Quelle ausweist. Das trifft genau die Zusage, für die dieses System
existiert.

Dass die beiden erlaubten Hosts seriös sind, ändert daran nichts: die Zusage
lautet „nur diese Hosts", und sie ist heute nicht eingelöst, sondern angenommen.

## Was ausdrücklich NICHT der Fall ist

Geprüft, weil es der naheliegende größere Verdacht war: **die Skripte werden
nicht aus einem Lauf heraus aufgerufen.** `grep` über `packages/` und
`services/` findet keinen Aufrufer; es sind Operator-Werkzeuge außerhalb der
Lauf-Maschinerie.

Damit verzeichnet **heute kein `RunManifest` fälschlich `deny_all`, während
etwas telefoniert.** Die Netzpolitik-Frage aus dem Fact-Lock zum Provider-Adapter
bleibt rein prospektiv — sie entsteht erst, wenn ein Modell-Schritt in einen Lauf
verdrahtet wird. **Ein Paket dafür wäre heute Vorratsbau** und unterbleibt.

## Die Entwurfsentscheidung: verweigern, nicht nachprüfen und folgen

Zwei Wege stehen offen:

1. Umleitungen folgen und **jeden Sprung** gegen die Allowlist prüfen.
2. Umleitungen **gar nicht** folgen; ein 30x ist eine typisierte Verweigerung.

Gewählt ist (2), aus einem Grund, der über Sicherheit hinausgeht: Bei (1) bliebe
die aufgezeichnete URL und das tatsächlich abgerufene Dokument **verschieden**,
auch wenn beide Hosts erlaubt sind. Für ein System, das Belege an URLs bindet,
ist genau diese Divergenz das Problem — nicht nur der Sprung nach draußen.

(2) ist außerdem fail-closed und passt zur Hausregel „Ausfälle ehrlich vermerken,
nie still überbrücken": ein verweigerter Sprung endet laut und wird als
Feststellung-entfällt vermerkt; ein stillschweigend gefolgter nicht.

Der Präzedenzfall ist von gestern Nacht: `_NoRedirectHandler` in
`adapters/llm/mrr/adapters/llm/transport.py` (E4-T08) überschreibt
`redirect_request` und gibt `None` zurück. Dasselbe Muster, zweimal dupliziert —
denn `fetch_source_content.py` begründet für seine Allowlist bereits ausdrücklich,
warum sie **nicht** geteilt wird („this script's allowlist is its own, so it stays
correct even if T02a's ever changed"). Diese Isolation wird nicht aufgeweicht.

## Das Akzeptanz-Orakel — VOR dem Bau festgelegt

Der scharfe Fall ist **nicht** „ein Sprung nach draußen wird abgewiesen". Er ist:

> **Auch ein Sprung von einem erlaubten Host auf einen anderen erlaubten Host
> wird abgewiesen.**

Denn die Zusage lautet „kein Umweg", nicht „kein Umweg an der Allowlist vorbei".
Wer nur den ersten Fall prüft, kann die Lücke schließen und die Divergenz von URL
und Dokument bestehen lassen.

Zweitens: **auf einem verweigerten Sprung wird kein Körper gelesen** — geprüft
mit einem Verbindungs-Double, das den Test scheitern lässt, wenn nach dem 30x
noch gelesen wird.

Drittens: die neue Verweigerung bleibt **getrennt** von `EgressRefusedError`. Die
beiden bedeuten Verschiedenes — „wir haben nie einen Socket geöffnet" gegen „wir
haben geöffnet und wurden weitergeschickt" — und dürfen nicht zu einem
generischen Fehler verschmelzen (AGENTS, verbotene Abkürzung).

Der Prüfer implementiert das Orakel unabhängig; der Erbauer verifiziert sein
eigenes Ergebnis nicht.

## Was dieses Paket nicht tut

- **Kein gemeinsames Egress-Modul für `scripts/`.** Die Duplizierung ist gewollt
  und benannt.
- **Keine Änderung an `ALLOWED_HOSTS`,** an der Größengrenze, am Zeitlimit oder
  an irgendeiner Auswertung.
- **Keine Berührung der Netzpolitik-/RunManifest-Frage** — siehe oben, sie ist
  prospektiv.
- **Keine neue Abhängigkeit, keine Migration.**
