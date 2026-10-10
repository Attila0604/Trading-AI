# Code-Analyse Trading Multi-Agent v3.3 (Stand 10.10.2026)

> **Update 10.10.2026:** H1–H5, H7, H8 und der Modus-Fehler „Nur Analyse“
> (Dashboard speichert `analysis`, Backend prüfte nur `analyse`) sind behoben,
> siehe Abschnitt 6. Offen: K1–K4, H6, H9, M1–M7.

Grundlage: vollständiges Lesen aller Module (~6.600 Zeilen), statische Prüfung
(pyflakes) und eine neue Testsuite (`tests/`, 62 Tests). Capital.com, Claude und
WhatsApp sind in den Tests durch Attrappen ersetzt. Echte Marktdaten wurden nicht
geladen (keine Zugangsdaten in dieser Umgebung).

```bash
pip install -r requirements-dev.txt
pytest -q          # Stand nach den Fixes: 67 passed, 4 xfailed
```

Jeder `xfail`-Test ist ein **dokumentierter Befund**: Er beschreibt das
gewünschte Verhalten und schlägt heute absichtlich fehl. Wird der Fehler behoben,
meldet pytest `XPASS` und der Marker kann weg (die Marker sind `strict=True`).

---

## 1. Kritisch – bevor `AUTO_TRADE` / `ORDERS_ENABLED` jemals auf `true` gehen

Heute schützt nur die Order-Sperre (`ORDERS_ENABLED=false`, Default). Der Live-Pfad
ist so nicht einsatzfähig:

| # | Befund | Ort | Test |
|---|---|---|---|
| K1 | Richtung wird als `LONG`/`SHORT` an Capital.com geschickt, die API erwartet `BUY`/`SELL`. Die Preisauswahl prüft `direction == "BUY"` und nimmt deshalb immer den Bid. | `main.py:823`, `capital_client.py:241` | `test_auto_trade_richtung_capital_format` |
| K2 | `POSITION_SIZE_EUR` (1000) geht als **Kontraktgröße** raus: bei BTC 1000 Coins, bei Gold 1000 Unzen. Die Größe müsste aus Einsatz, SL-Distanz und Kontraktwert berechnet werden. | `main.py:824` | dito |
| K3 | Auto-Trade ignoriert KI-Veto **und** Portfolio-Schutz: blockierte Signale würden echt gehandelt. | `main.py:801-804` | `test_auto_trade_respektiert_veto` |
| K4 | `create_position` speichert die `dealReference` als `dealId`. Zum Schließen braucht man die echte `dealId` (über `GET /confirms/{dealReference}`). | `capital_client.py:263-265` | – |

## 2. Hoch – verfälscht die Ergebnisse oder die Sicherheit

| # | Befund | Ort | Test |
|---|---|---|---|
| H1 | **Signal-Logik zählt Gegenargumente als Bestätigung.** RSI < 30 (überverkauft = Kaufargument) hebt die Confluence eines Short-Signals von 7 auf 8, RSI > 70 die eines Longs. Ein Bollinger-Ausbruch zählt +1 egal in welche Richtung. In einer reinen Seitwärts-Welle feuern so Long-Signale mit Confluence 8 genau am Hoch. | `indicators.py:177-192` | `test_rsi_ueberverkauft_…`, `test_rsi_ueberkauft_…` |
| H2 | Bei völlig flachem Kurs (eingefrorener Feed) liefert `rsi()` 100 statt 50 → `sell` mit Confluence 7, über der Handelsschwelle. | `indicators.py:54-55` | `test_flacher_kurs_erzeugt_kein_signal` |
| H3 | **Drawdown-Pause ist eine Sackgasse.** Der Drawdown kommt nur aus geschlossenen Trades. In der Pause wird nichts eröffnet, also kann er nie sinken – das System steht für immer. | `demo_tracker.py:485-488` | `test_drawdown_pause_hat_ausweg` |
| H4 | `MAX_RISK_PCT` und der Risiko-Regler im Dashboard wirken nicht. `fixed_percent` nimmt immer den Modus-Default von 2 % (pyflakes: `RISIKO_PROZENT` importiert, aber nie benutzt). | `demo_tracker.py:31, 586-599` | `test_max_risk_pct_wirkt_auf_einsatz` |
| H5 | Default `MIN_CONFIDENCE=70`, Regel-Signale mit Confluence 6 haben aber Konfidenz 60. Ohne gespeicherte `config.json` mit `conf: 60` verschwinden alle reinen RSI-Signale still: Sie werden weder gehandelt noch als Veto protokolliert. Live läuft dann eine andere Strategie als im Backtest (`min_confluence 6`). | `config.py:80`, `main.py:653` | `test_default_konfidenz_passt_zu_regel_confluence` |
| H6 | **Backtest misst eine andere Strategie als live.** Live: ATR-Stops (3×/6× ATR), 45-Tage-Timeout, nachgezogener Stop, Konfidenzfilter, Portfolio-Limits, Einstieg um 07:00 in die noch offene Tageskerze. Backtest: fester SL/TP aus der Config (1,5 / 3 %), nichts davon, Einstieg zum Tagesschluss, Mid-Preise ohne Spread. Die Aussage „exakt dieselbe Logik“ gilt nur für die Richtungsentscheidung. | `backtest.py`, `main.py:1074` | – |
| H7 | **Stored XSS im Dashboard.** KI-Texte (`summary`, Veto-Grund) stammen aus der Websuche und landen ungefiltert per `innerHTML` im Dashboard. Eine präparierte Webseite kann so Script einschleusen und den API-Token aus `localStorage` stehlen. | `dashboard.html:1131` u. a. (35× `innerHTML`) | – |
| H8 | Mit gesetztem `API_TOKEN` sind trotzdem Kontostand, Positionen, Statistik und der **komplette Excel-Download** ohne Token abrufbar. Ohne `API_TOKEN` ist alles offen, auch `/trade` und `/close`. | `main.py:1127-1173` | `test_token_schuetzt_auch_lesende_endpoints` |
| H9 | Excel als Datenbank: Jede Änderung schreibt das ganze Sheet neu, **nicht atomar**. Ein Absturz oder Redeploy mitten in `wb.save()` hinterlässt eine kaputte Datei. Danach wirft `get_statistik()` bei jedem Aufruf, auch in `/status` (Healthcheck). | `demo_tracker.py:129-156` | – |

## 3. Mittel

- **M1 Spread-Behandlung inkonsistent:** Shorts werden zum Ask eröffnet (geschenkter Spread). Die SL/TP-Prüfung läuft auf Mid-Kerzen, der Momentanpreis immer auf Bid, auch für Shorts. → `test_short_einstieg_zum_bid`
- **M2 Veto-Zuordnung über exakten Asset-String:** Antwortet das Modell mit `EURUSD` statt `EUR/USD`, wird das Veto stillschweigend ignoriert. → `test_veto_robust_gegen_asset_schreibweise`
- **M3 Trendfilter `Kurs > EMA20 > EMA50`** ist sehr empfindlich: Eine Sinuswelle mit 2 % Amplitude wechselt alle 6–12 Kerzen zwischen „uptrend“ und „downtrend“ und erzeugt dabei abwechselnd Long- und Short-Signale (in den Tests nachgestellt). Ohne Mindestabstand oder Steigung der EMAs entsteht ein Trend-Signal aus Rauschen.
- **M4 Blockierende Excel-IO in `async`-Endpoints** (`/status`, `/demo/statistik` …) blockiert den Event-Loop. Zudem lädt jeder `signal_oeffnen` die Datei 2×.
- **M5 Doppelter Code:** `generiere_trades` und `trades_aus_signalen` sind doppelt implementiert (heute identisch, per Test abgesichert). `asset_to_epic` (main) und `EPIC_MAP` (agents) sind zwei Mappings; `TRADING_ASSETS`/`DASHBOARD_URL`/`API_TOKEN` werden in `main.py` erneut aus der Env gelesen statt aus `config.py`.
- **M6 Kleine Stichproben:** Das Veto-Urteil fällt ab je 10 abgeschlossenen Trades mit einer festen Schwelle von 0,1 R. Bei der Streuung von ±1 bis +2 R pro Trade ist das Zufall. Besser: Konfidenzintervall bzw. Bootstrap angeben und frühestens ab ~30 je Gruppe urteilen.
- **M7 Veraltete Abhängigkeiten:** `anthropic==0.34.0` (Aug. 2024) ist älter als das Websuche-Tool, das benutzt wird. `stop_reason == "pause_turn"` (lange Websuche) wird nicht behandelt, die Antwort ist dann leer → „kein Veto“.

## 4. Niedrig / Aufräumen

- Prompt im Tech-Agent spricht noch von „4H-Kerzen“; `news_agent`, `macro_agent` und `excel_tracker` nutzen `datetime.now()` (UTC) statt `jetzt()`.
- `/selftest` prüft `CAPITAL_API_KEY`, der Login braucht aber E-Mail/Passwort; der Marktdaten-Check nutzt `HOUR_4` statt `DAY`.
- FastAPI-Titel „v3.1“, README nennt teils noch alte Sheets/Dateien (`Trading_Analyse.xlsx` vs. `Trading_Tracker.xlsx`).
- `search_markets` encodiert den Suchbegriff nicht; pro Request wird ein neuer `httpx.AsyncClient` erzeugt.
- pyflakes: 7 ungenutzte Imports in `excel_tracker.py`, 2 f-Strings ohne Platzhalter in `main.py`.

---

## 5. Verbesserungsvorschläge (priorisiert)

**Sofort (klein, großer Effekt)**
1. `indicators.py`: RSI nur dann zur Confluence zählen, wenn er **in Signalrichtung** zeigt (für Trendfolge eher RSI 50–70 bei Long statt > 70). Bollinger-Ausbruch nur in Signalrichtung zählen. `rsi()` bei `avg_gain == avg_loss == 0` → 50. → H1, H2
2. Drawdown-Pause mit Ausweg: z. B. nach N Tagen Pause mit halbem Einsatz wieder starten. Oder die Erholung über die Schatten-Trades (V-Zeilen) messen. → H3
3. `risk_pct` aus `active_config` als `mm_params={"risk_pct": …}` an `signal_oeffnen` übergeben. → H4
4. `MIN_CONFIDENCE`-Default auf 60 setzen **oder** `REGEL_MIN_CONFLUENCE` auf 7. Hauptsache Backtest und Live passen zusammen. → H5
5. Dashboard: eine `esc()`-Funktion für alle externen Texte (oder `textContent`). → H7
6. `pruefe_token` auf alle Endpoints außer `/` und `/status` legen. `/trade`, `/close`, `/connect` ohne gesetztes `API_TOKEN` ganz sperren. → H8

**Kurzfristig**
7. Backtest an die Live-Logik angleichen: ATR-Stops, Timeout, nachgezogener Stop, Spread und Konfidenzfilter. Am besten **eine** gemeinsame Funktion `trade_management(trade, kerze)`, die Live-Check und Backtest beide aufrufen. Erst dann sagt der Backtest etwas über das Live-System. → H6
8. Excel-Schreiben atomar machen (in Temp-Datei speichern, dann `os.replace`), dazu ein tägliches Backup. Mittelfristig auf SQLite umsteigen und Excel nur noch als Export erzeugen. → H9, M4
9. Einheitliche Preis-Konvention: Long rein zum Ask und raus zum Bid, Short umgekehrt. Kerzen-Prüfung mit Bid- bzw. Ask-Hoch/Tief statt Mid. → M1
10. Veto-Zuordnung über normalisierte Asset-Namen (`_norm_asset` + Epic-Alias). → M2
11. `anthropic` aktualisieren und `pause_turn` behandeln. `AGENT_MODEL` bleibt per Env umstellbar. → M7

**Bevor echtes Geld fließt**
12. Live-Order-Pfad komplett neu: Positionsgröße aus Einsatz/SL-Distanz/Kontraktgröße (`GET /markets/{epic}` liefert `minDealSize`, `lotSize`), BUY/SELL-Mapping, Deal-Bestätigung über `/confirms`, Veto- und Portfolio-Filter. Dazu ein eigener Integrationstest gegen das Demo-Konto. → K1–K4
13. Statistische Auswertung: Erwartungswert in R mit 95 %-Konfidenzintervall, getrennt nach Asset und Signaltyp (RSI-Signal vs. Trend-Signal). Walk-forward-Backtest statt In-Sample-Optimierung.
14. CI: GitHub-Action, die `pytest` bei jedem Push laufen lässt.

---

## 6. Umgesetzt (10.10.2026)

| Befund | Änderung |
|---|---|
| H1 | `indicators.py`: RSI-Extrem zählt nur in Signalrichtung, Bollinger-Ausbruch nur in Signalrichtung (beim RSI-Signal auf der überdehnten Seite). |
| H2 | `rsi()` liefert 50 bei völlig flachem Kurs. |
| H3 | Neue Stufe `NEUSTART`: `DD_PAUSE_TAGE` (14) nach dem letzten Verlust im Pause-Bereich wird mit Einsatz × `DD_VORSICHT_FAKTOR` wieder gehandelt, jeder neue Verlust startet die Wartezeit neu. |
| H4 | `risk_pct` (Dashboard-Regler) bzw. `MAX_RISK_PCT` wirkt jetzt auf den Einsatz (`demo_tracker._mm_params`). |
| H5 | `MIN_CONFIDENCE`-Default 60. **Achtung:** Ein in `config.json` gespeicherter Wert (`conf`) hat Vorrang, im Dashboard prüfen. |
| H7 | Dashboard: `esc()` für alle Server-/KI-Texte, IDs in `onclick` nur noch nach Whitelist. |
| H8 | Token gilt global (außer `/` und `/status`); Excel-Download schickt den Token mit; `/trade` und `/close` ohne gesetzten `API_TOKEN` gesperrt. |
| neu | Modus „Nur Analyse“ greift jetzt (`analysis` und `analyse`). |
