# Bench Bench
English · [Русский](README.ru.md) · [Українська](README.uk.md) · [Deutsch](README.de.md)

Ein reproduzierbarer Benchmark für Programmieragenten. Verglichen wird die Kombination aus Modell,
Agentenoberfläche (Claude Code, Codex oder Pi) und Reasoning-Aufwand. Der Agent arbeitet in einer Sandbox;
verdeckte Tests bewerten das Ergebnis. Eigene Modelle lassen sich anschließen. Ein SAP-System ist nicht nötig.

## Schnellstart

Benötigt werden Linux (auch WSL2), Docker, ein Modellendpunkt und das separat verteilte ABAP-Aufgabenpaket
(bei den Verantwortlichen erhältlich). `PACKAGE_ROOT` muss auf das vollständige Paket einschließlich `repo/`
zeigen. Das Ausgabeverzeichnis muss für UID 1000 beschreibbar sein; der Host muss die von bubblewrap benötigten
Namespaces erlauben. Nach dem Klonen des Repositorys im Hauptverzeichnis ausführen:

```bash
docker build -f docker/Dockerfile -t abap-bench .
export PACKAGE_ROOT=/path/to/abap-package
export API_KEY  # Wert vorher in der Shell oder einem Secret-Manager setzen
mkdir -p output
docker run --rm --security-opt seccomp=unconfined -p 8080:8080 \
  -v "$PACKAGE_ROOT:/pkg:ro" -v "$PWD/output:/out" -e API_KEY \
  abap-bench --agent claude --model demo-model --api-base https://example.org \
  --api-key-env API_KEY --tier ember --env 3 --no-internet --port 8080
```

Modell und Adresse durch die eigenen Angaben ersetzen. Claude benötigt eine Anthropic-kompatible API,
`--agent codex` eine Responses API (Basisadresse meist mit `/v1`), `--agent pi` eine Chat Completions API.
Mit `--port` erscheint der Fortschritt unter `http://localhost:8080`.
[Ausführliche Startanleitung](README.md#one-command-native-abap-runs).

## Aufgaben und Werkzeuge

Die Aufgabensätze werden in getrennten Ergebnistabellen ausgewertet:

- **PR-6:** 6 Aufgaben aus tatsächlich zusammengeführten Pull Requests, Schwierigkeitsstufen L1–L3.
  Der Agent erhält das Repository beim Eltern-Commit des PR und eine kurze Aufgabenbeschreibung.
  Innerhalb des Zeitlimits soll er die Änderung umsetzen; die verdeckten Tests des PR bewerten das Ergebnis.
  Beschreibungen unter `tasks/`: `L1a-osg678`, `L1b-osg680`, `L2a-osg693`, `L2b-abapiti87`, `L3a-minz39`,
  `L3b-abapiti95`. [Aufgabenübersicht](README.md#the-tasks).
- **Native ABAP-Aufgaben:** ein separates Paket mit 337 Aufgaben aus abapGit, open-abap-core, open-abap-apc,
  open-steamgate (osg), osg-demo, pia, zllm, zork-abap, zvdb und zmjs. Die Quellen verteilen sich auf älteren
  öffentlichen Code (190), neuen Code (109) und eigene öffentliche Repositorys des Maintainers (38).
  Bei **L0 infill** fehlt eine Zeile, ein Block oder ein Anweisungsrumpf innerhalb einer Methode;
  bei **L1** ist eine ganze Methode zu ergänzen. Bewertet wird nur der Rumpf der Zielmethode: Er wird in
  die Originalquellen eingesetzt und mit verdeckten Tests geprüft. Eine zusätzliche Prüfung mit falschen
  Antworten stellt sicher, dass der Bewerter diese ablehnt.
  Umfang der Stufen: **ember / SMOKE-4 (4)**, **SMOKE (12)**, **MEDIUM (69 in v2.2–v2.3; 54 ab v2.4)**,
  **FULL (337)**. Aufgaben sind versioniert (`id@n`); jeder Lauf hält die Umgebungsversion fest
  (v2.3 = env 3). Dieses Repository enthält den [Adapter](bin/abap-tasks.sh) und
  [dessen Anleitung](tasks-abap/README.md); das Paket wird separat verteilt.
- **Weitere ABAP-Sätze mit eigenen Tabellen:** agentic umfasst 10 Aufgaben aus echten zusammengeführten
  Änderungen, Stufen L1–L3, mit 30–60 Minuten Zeitlimit. synthetic umfasst 15 Aufgaben mit eingebauten
  Fehlern oder entfernten Funktionen. Zur Bewertung dienen die Tests der Änderung, aus der Referenzlösung
  abgeleitete Tests und 200 differenzielle Szenarien pro Aufgabe mit festgelegtem Seed.

In der Sandbox steht **`osg-abap`** im PATH bereit:

| Befehl | Prüfung |
|---|---|
| `osg-abap lint` | Schnelle Quellcode-Diagnose |
| `osg-abap check` | Syntax und Aktivierung |
| `osg-abap test` | Unit-Tests im Arbeitsverzeichnis, auch selbst geschriebene in `<class>.clas.testclasses.abap` |

Die verdeckten Tests bleiben unsichtbar. `AGENTS.md` / `CLAUDE.md` im Arbeitsverzeichnis erklären die
Befehle und regen ab v2.3 zu eigenen Tests an. Das gebündelte Werkzeug funktioniert offline;
die Build-Skripte des Quellrepositorys werden nicht benötigt.

Bei ABAP sind die Web-Werkzeuge der Agentenoberflächen abgeschaltet. Netzwerkzugriff ist nur zum
Modellendpunkt sowie zu `help.sap.com`, `rules.abaplint.org` und `abaplint.app` erlaubt;
`--no-internet` lässt nur den Modellendpunkt zu. Schlüssel für eigene Endpunkte, z.ai, Alibaba,
die Anthropic API und Azure bleiben außerhalb der Sandbox. Anmeldedaten für Claude OAuth und ChatGPT
gelangen weiterhin hinein.

Alles Weitere zu Methodik, Ergebnissen, PR-6 und Sicherheit steht im [englischen README](README.md).
