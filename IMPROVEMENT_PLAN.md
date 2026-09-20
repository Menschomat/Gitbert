# 🔍 Gitbert: Comprehensive Code Review & Improvement Plan

> **Datum**: 20. September 2026  
> **Zielsystem**: Gitbert PR Review Agent (Gitea / Google ADK 2.0 / LiteLLM)  
> **Umfang**: Gesamte Architektur, Sicherheit, Concurrency, Testabdeckung und Betriebssicherheit.

---

## 1. Übersicht & Zusammenfassung

Gitbert verfügt über ein solides Fundament: saubere Pydantic-Domänenmodelle, strenge Sicherheitsgrenzen in `ScopedMRContext`, Multi-Provider-Unterstützung (Gemini & LiteLLM/OpenRouter) und automatische GHCR-Docker-Builds.

Bei der Tiefenprüfung sind **fünf Kernpunkte** aufgefallen:
1. **Sicherheitslücke (Höchste Priorität – SSRF & Token-Leak)**: `get_action_log` ruft unvalidierte `target_url`s mit dem Gitea-Auth-Token auf.
2. **Docker-Mismatch**: `Dockerfile` startet das ADK Dev-Web-UI (`adk api_server`), nicht den Webhook-Server (`main.py server`).
3. **Flüchtiger In-Memory Cache (Multi-Worker / Restart-Problem)**: Reviews warten im RAM auf CI-Fertigstellung; bei Restart oder Multi-Worker-Betrieb verbleibt der PR ewig auf `PENDING`.
4. **Architektur-Disconnect**: Ein vollwertiger ADK-Agent mit 5 Scoped Tools wird gebaut, aber in `ReviewEngine` nie aufgerufen (dort läuft stattdessen ein direkter Single-Shot-Prompt mit gekürztem Diff).
5. **CLI-Disconnect**: `main.py server` wirft Syntaxfehler bei dokumentierten Flags wie `--review-mode`.

---

## 2. Detaillierte Befunde nach Kategorien

### 2.1 Logically Sound & Erwartungshaltung
- **Production Docker Entrypoint**: Das `Dockerfile` (Zeile 59) startet `CMD ["adk", "api_server", ...]`. Damit startet das Entwickler-GUI von Google ADK, **nicht** der Webhook-Server (`gitbert.server:app` bzw. `main.py server`). Ein Docker-Container aus der GitHub Registry reagiert daher nicht auf Webhooks und `/healthz` liefert einen 404.
- **Agent Scoped Tools Disconnect**: `build_reviewer_agent()` instanziiert einen ADK-Agenten mit 5 Tools (`get_file_content`, `list_repository_files`, etc.). In `ReviewEngine.analyze_pr()` wird dieser Agent aber ignoriert und stattdessen ein direkter Prompt mit `diff[:30000]` abgeschickt. Obwohl der Prompt dem Modell sagt „Nutze immer deine Tools“, hat der Call gar keine Tools hinterlegt.
- **CLI-Argumente in `main.py`**: `main.py server` erlaubt nur `--host` und `--port`. Flags wie `--review-mode enforcing` oder `--model-provider litellm` werden von `argparse` mit einem Fehler abgewiesen.
- **Legacy Identifiers**: Status-Checks heißen noch `git-bot/pr-review`, User-Agent ist `git_bot-Reviewer/1.0`.

### 2.2 Scope Creep & Toter Code
- **`gitbert/tools/time_tool.py`**: Ein Überbleibsel aus initialen ADK-Tutorials („Get current time“), das für Code-Reviews keinerlei Funktion hat.
- **`root_agent` in `gitbert/agent/__init__.py`**: Existiert nur als Dummy für die `adk`-CLI, hat aber keine Review-Fähigkeiten.

### 2.3 Dead Ends & Wartbarkeit
- **Flüchtiger In-Memory Cache**: `_cached_reviews` speichert Reviews im RAM, während auf externe CI-Actions gewartet wird. Bei Container-Neustarts oder mehreren Workern geht der Cache verloren. Die Fertigstellung der CI-Action schlägt dann still fehl und der PR bleibt ewig auf `PENDING`.
- **Harte Diff-Kürzung (`[:30000]`)**: Schneidet große Diffs mitten in einer Datei oder Codezeile ab, was zu Halluzinationen oder unvollständigen Reviews führen kann.

### 2.4 Side Effects & Sicherheit
- **SSRF & Token-Leakage in `get_action_log` (Kritisch – Höchste Priorität)**: `client.get(target_url)` führt HTTP-Requests an die URL des Commit-Status durch – mit gesetztem Header `Authorization: token <GITEA_TOKEN>`. Ein manipulierter Webhook kann so Tokens an Fremdserver leiten oder interne Endpunkte wie `169.254.169.254` (Cloud-Metadaten) anfragen.
- **HTTP Client Socket Exhaustion**: In `_get_client()` wird bei fast jedem API-Aufruf ein neuer `httpx.AsyncClient` erstellt und sofort wieder geschlossen (kein Connection Pooling).
- **Fehlendes Error-Handling im Webhook-Worker**: Stürzt `process_event` im Hintergrund ab (z. B. Timeout oder Quota), wird der Fehler geloggt, aber der Commit-Status im PR verbleibt dauerhaft auf `PENDING`.

### 2.5 Testabdeckung
- **Unbemerkt abstürzende Background-Tasks**: `test_webhook_accepted_and_dispatched` wirft eine `RuntimeWarning: coroutine was never awaited`, weil der Mock in `get_pr_metadata` im Hintergrund crasht, während der Test nur den HTTP 202 Status prüft.
- **Fehlende SSRF-Tests**: Keine Absicherung gegen schädliche `target_url`s in CI-Logs.

---

## 3. Konkreter Verbesserungsplan (Phasen)

```mermaid
flowchart TD
    subgraph Phase 1: Sicherheit & Betriebsbereitschaft [Phase 1: Erledigt & Verifiziert]
        P1_1["SSRF & Token-Leak-Schutz in get_action_log"]
        P1_2["Dockerfile Entrypoint reparieren (main.py server)"]
        P1_3["HTTP-Client Connection-Pooling"]
        P1_4["Commit-Status Failure-Recovery bei Crash"]
    end

    subgraph Phase 2: Valkey / Redis Pluggable Cache [Phase 2: Erledigt & Verifiziert]
        P2_1["Cache-Schnittstelle (ICacheProvider)"]
        P2_2["Redis / Valkey Provider via REDIS_URL"]
        P2_3["In-Memory Provider mit TTL als Fallback"]
        P2_4["ReviewEngine an ICacheProvider anbinden"]
    end

    subgraph Phase 3: CLI & Namenskonsistenz [Phase 3: Erledigt & Verifiziert]
        P3_1["Alle Settings-Optionen als CLI-Flags in main.py"]
        P3_2["Status-Context auf gitbert/pr-review vereinheitlichen"]
        P3_3["User-Agent auf Gitbert-Reviewer/1.0 anpassen"]
    end

    subgraph Phase 4: Architektur & Toter Code [Phase 4: Erledigt & Verifiziert]
        P4_1["ReviewEngine und System-Prompt harmonisieren"]
        P4_2["Toten Code entfernen (time_tool.py, root_agent)"]
    end

    subgraph Phase 5: Test-Härtung & Dokumentation [Phase 5: Erledigt & Verifiziert]
        P5_1["SSRF-Sicherheitstests für get_action_log"]
        P5_2["Valkey/Redis & Cache-Unit-Tests"]
        P5_3["Mock-Setup in test_webhook_server.py reparieren"]
        P5_4["README & Konfigurationsdokumentation aktualisiert"]
    end

    Phase 1 --> Phase 2
    Phase 2 --> Phase 3
    Phase 3 --> Phase 4
    Phase 4 --> Phase 5
```

---

### Detaillierte Maßnahmen

#### Phase 1: Sicherheit & Betriebsbereitschaft (Top-Priorität – Sofort)
1. **SSRF & Token-Leak-Schutz**:
   - In `GiteaAdapter.get_action_log(repo, target_url)`:
     - `target_url` validieren: Muss entweder relativ sein oder strikt mit `self.base_url` übereinstimmen.
     - Private IP-Bereiche (`127.0.0.1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254` AWS-Metadaten) rigoros blockieren.
     - Bei externen URLs niemals Gitea-Auth-Header mitsenden.
2. **Dockerfile CMD korrigieren**:
   - `CMD ["python", "main.py", "server"]` statt `adk api_server`.
3. **HTTP-Client Connection Pooling**:
   - Langlebigen, wiederverwendbaren `httpx.AsyncClient` im Adapter nutzen, um Socket-Exhaustion zu verhindern.
4. **Fehler-Rollback bei Absturz**:
   - Stürzt `process_event` im Hintergrund ab, Commit-Status auf `FAILURE` setzen ("AI Review failed due to internal error"), damit Entwickler nicht dauerhaft durch `PENDING` blockiert werden.

#### Phase 2: Valkey / Redis Pluggable Cache
1. **Abstrakte Cache-Schnittstelle (`gitbert/cache/base.py`)**:
   - `async get(key: str) -> ReviewResult | None`
   - `async set(key: str, value: ReviewResult, ttl_seconds: int = 86400) -> None`
2. **Valkey / Redis Implementierung (`gitbert/cache/redis.py`)**:
   - Nutzt `redis.asyncio` bei konfigurierter `REDIS_URL` bzw. `VALKEY_URL`.
   - Serialisierung via Pydantic (`model_dump_json()` / `model_validate_json()`).
3. **In-Memory TTL Fallback (`gitbert/cache/memory.py`)**:
   - Schlanker In-Memory Cache mit Timestamp/TTL-Ablauf, falls keine `REDIS_URL` konfiguriert ist (kein Setup-Zwang für einfache Einzelcontainer).
4. **Integration in `ReviewEngine`**:
   - Ersetzt `self._cached_reviews[cache_key]` durch den injizierten Cache-Provider.

#### Phase 3: CLI & Namenskonsistenz
1. **CLI-Flags in `main.py`**:
   - Alle Flags aus `config.py` (`--review-mode`, `--model-provider`, `--model-name`, `--redis-url` etc.) in `main.py` aktivieren.
2. **Namensbereinigung**:
   - Status-Context auf `gitbert/pr-review` umstellen.
   - User-Agent auf `Gitbert-Reviewer/1.0` anpassen.

#### Phase 4: Architektur & Toter Code
1. **ReviewEngine und Prompt harmonisieren**:
   - Entweder Single-Shot-Prompt von nicht-existenten Tool-Anweisungen befreien oder ADK-Runner für echte Multi-Turn-Toolcalls einbinden.
2. **Toten Code entfernen**:
   - `time_tool.py`, `root_agent` und zugehörige Tests löschen.

#### Phase 5: Test-Härtung
1. **SSRF-Sicherheitstests**:
   - Tests schreiben, die böswillige `target_url`s (z. B. `http://169.254.169.254/latest/meta-data`, interne IP-Ranges) an `get_action_log` übergeben und verifizieren, dass sie abgewiesen werden und kein Token leakt.
2. **Cache-Tests**:
   - Unit-Tests für Valkey/Redis und Memory-TTL-Provider.
3. **Webhook-Test-Fix**:
   - Mocks in `test_webhook_server.py` so definieren, dass der Hintergrund-Task sauber durchläuft und keine unawaited coroutines geworfen werden.
