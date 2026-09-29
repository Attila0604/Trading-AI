# Trading Multi-Agent v3.3 🤖📈

KI-gestütztes Trading-System mit 6 Claude-Agenten, Capital.com API-Integration und WhatsApp-Benachrichtigungen.

---

## 🏗️ Architektur

```
📡 News Sentinel    → Web-Suche + Sentiment-Analyse
📊 Tech Analyst     → RSI, MACD, EMA 20/50/200, Bollinger Bands
🌐 Macro Scout      → Fed/EZB, VIX, DXY, Risikoappetit
🛡️ Risk Guardian    → Position Sizing, SL/TP Berechnung
🎯 Strategy AI      → Strategie-Auswahl & Optimierung
⚡ Executor         → Capital.com API Orders
🧠 Orchestrator     → Master-Koordination & finale Entscheidungen
```

---

## 🚀 Deployment: GitHub → Railway

### Schritt 1: GitHub Repository

1. Gehe zu **github.com** → **New Repository**
2. Name: `trading-agent` (Private empfohlen)
3. Lade alle Dateien hoch (Upload Files):
   - `main.py`
   - `agents.py`
   - `capital_client.py`
   - `excel_tracker.py`
   - `whatsapp.py`
   - `requirements.txt`
   - `railway.toml`

### Schritt 2: Railway Setup

1. **railway.app** → **New Project** → **Deploy from GitHub repo**
2. Repository auswählen: `trading-agent`
3. Railway erkennt automatisch Python/FastAPI

### Schritt 3: Volume erstellen (für Excel-Persistenz)

1. Railway Dashboard → **Add Volume**
2. Mount Path: `/app/data`
3. Name: `trading-data`

### Schritt 4: Environment Variables

Railway Dashboard → **Variables** → folgende eintragen:

```env
# Pflicht
ANTHROPIC_API_KEY=sk-ant-...

# Capital.com (Demo-Account)
CAPITAL_EMAIL=deine@email.com
CAPITAL_PASSWORD=deinPasswort
CAPITAL_API_KEY=           # optional, falls vorhanden
CAPITAL_DEMO=true          # false für Live-Account

# WhatsApp (CallMeBot)
CALLMEBOT_PHONE=4312345678   # Österreich: 43 + Nummer ohne 0
CALLMEBOT_APIKEY=123456

# Trading-Konfiguration
TRADING_ASSETS=EUR/USD,BTC/USD,XAU/USD,US500
TRADING_STRATEGY=adaptive
MAX_RISK_PCT=2
STOP_LOSS_PCT=1.5
TAKE_PROFIT_PCT=3.0
POSITION_SIZE_EUR=1000
AUTO_TRADE=false           # true = vollautomatisch

# System
DATA_DIR=/app/data
```

### Schritt 5: Deploy

Railway deployt automatisch nach dem GitHub-Push.

---

## 📱 WhatsApp (CallMeBot) einrichten

1. Sende eine WhatsApp-Nachricht an **+34 644 59 78 13**:
   ```
   I allow callmebot to send me messages
   ```
2. Du bekommst einen API Key zurück
3. Trage Phone + API Key in Railway Variables ein

---

## 🌐 API Endpoints

| Method | Endpoint | Beschreibung |
|--------|----------|--------------|
| GET  | `/` | Dashboard (HTML) |
| POST | `/analyze` | Analyse-Pipeline starten |
| POST | `/trade` | Trade manuell platzieren |
| GET  | `/positions` | Offene Positionen |
| POST | `/close/{deal_id}` | Position schließen |
| GET  | `/balance` | Kontostand |
| GET  | `/signals` | Letzte Signale |
| GET  | `/history` | Trade-History |
| GET  | `/status` | System-Status |
| POST | `/connect` | Capital.com verbinden |

---

## 📊 Analyse starten

```bash
# Via curl
curl -X POST https://deine-app.railway.app/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "assets": ["EUR/USD", "BTC/USD", "XAU/USD"],
    "strategy": "adaptive",
    "risk_pct": 2,
    "sl_pct": 1.5,
    "tp_pct": 3.0,
    "auto_execute": false
  }'

# Trade manuell platzieren
curl -X POST https://deine-app.railway.app/trade \
  -H "Content-Type: application/json" \
  -d '{
    "asset": "EUR/USD",
    "direction": "long",
    "size": 1000
  }'
```

---

## 📈 Verfügbare Strategien

| ID | Name | Zeitrahmen | Risiko |
|----|------|-----------|--------|
| `trend` | Trend Following | 4H–1D | MITTEL |
| `reversion` | Mean Reversion | 1H–4H | NIEDRIG |
| `news_play` | News Catalyst | 5M–1H | HOCH |
| `breakout` | Breakout Hunter | 1H–4H | MITTEL |
| `scalping` | Scalp Modus | 1M–5M | HOCH |
| `adaptive` | KI Adaptiv | Dynamisch | VARIABEL |

---

## 📁 Excel Tracker

Zwei Dateien im `DATA_DIR` (Railway-Volume `/app/data`):

- `Trading_Tracker.xlsx` – Blatt `Demo-Kapital`: alle Demo-Trades (T-Zeilen) und
  KI-Vetos (V-Zeilen). Daraus werden Statistik, Drawdown und Portfolio-Schutz
  berechnet. Download über den Knopf **📊 EXCEL** bzw. `GET /excel-download`.
- `Trading_Analyse.xlsx` – Blätter `Dashboard`, `Trades` (echte Orders),
  `Analyse-Log`, `Performance`, `Einstellungen`. Nur auf dem Volume, kein Download-Endpunkt.

---

## ⚠️ Wichtige Hinweise

- **IMMER** zuerst mit Demo-Account (`CAPITAL_DEMO=true`) testen!
- `AUTO_TRADE=false` lassen bis das System vollständig getestet ist
- Capital.com Demo-API: `https://demo-api-capital.backend-capital.com`
- Capital.com Live-API: `https://api-capital.backend-capital.com`
- `API_TOKEN` setzen: ohne ihn kann jeder mit der URL Analysen starten, Trades buchen und den Tracker zurücksetzen
- Echte Orders gehen nur mit `ORDERS_ENABLED=true` raus (Standard: gesperrt)

---

## 🔗 Links

- Capital.com API Docs: https://open-api.capital.com
- CallMeBot: https://www.callmebot.com/blog/free-api-whatsapp-messages/
- Railway Docs: https://docs.railway.app

## Portfolio-Schutz (v3.1, Backend)

Greift in `demo_tracker.get_risiko_status()` **vor** jedem Demo-Trade – nicht nur im Dashboard:

| Env-Variable | Default | Wirkung |
|---|---|---|
| `DD_PAUSE_PCT` | 10 | Aktueller Drawdown vom Kapital-Hoch ≥ X % → keine neuen Trades |
| `DD_VORSICHT_PCT` | 5 | Drawdown ≥ X % → Einsatz × `DD_VORSICHT_FAKTOR` |
| `DD_VORSICHT_FAKTOR` | 0.5 | Einsatz-Faktor in der Vorsicht-Zone |
| `MAX_EXPOSURE_PCT` | 15 | Summe offener Einsätze max. X % des Kapitals |
| `MAX_OFFENE_TRADES` | 6 | Max. gleichzeitig offene Demo-Trades |
| `EIN_TRADE_PRO_ASSET` | true | Kein zweiter Trade (auch nicht gegenläufig) im selben Asset |
| `ZEITZONE` | Europe/Vienna | Alle Zeitstempel (Excel, Timeout, Logs) |
| `BREAKEVEN_NACH_TAGEN` | 14 | Erst nach X Tagen Stop nachziehen. 0 = aus |
| `BREAKEVEN_AB_R` | 1.0 | Mindestgewinn in R, bevor nachgezogen wird (verhindert Nachziehen bei Rauschen) |
| `BREAKEVEN_STOP_R` | 0.3 | Wo der Stop dann liegt, in R — nicht auf dem Entry, sondern im Gewinn |
| `MAX_TRADE_TAGE` | 45 | Danach Schließen zum Marktpreis (Wochen-Horizont) |
| `MAX_GLEICHE_RICHTUNG` | 2 | Max. offene Positionen in dieselbe Richtung (Klumpenrisiko) |

### Volatilitäts-adaptive SL/TP

Fester Stop = bei jedem Asset etwas anderes: 2 % sind bei EUR/USD (~0,5 % Tagesbewegung) vier Tagesbewegungen weit weg, bei BTC (~3 %) weniger als eine. Deshalb SL/TP als Vielfaches des Tages-ATR.

| Env-Variable | Default | Wirkung |
|---|---|---|
| `VOLA_ADAPTIV` | true | ATR-basierte SL/TP statt fester Prozentsätze |
| `ATR_SL_FAKTOR` | 3.0 | SL = 3 × Tages-ATR (≈ 1,5 Wochen-ATR) |
| `ATR_TP_FAKTOR` | 6.0 | TP = 6 × Tages-ATR → R:R 2,0 |
| `ATR_SL_MIN_PCT` / `ATR_SL_MAX_PCT` | 1 / 10 | Unter-/Obergrenze gegen ATR-Ausreißer |
| `KERZEN_AUFLOESUNG` | DAY | Kerzenbasis der Analyse (vorher HOUR_4) |

Ein weiterer Stop kostet **kein** zusätzliches Euro-Risiko: der Einsatz *ist* der maximale Verlust, `sl_pct` bestimmt nur die Preisdistanz und damit die (kleinere) Position.

### Übernacht-Finanzierung

| Env-Variable | Default | Wirkung |
|---|---|---|
| `FINANZIERUNG_AN` | true | Swap-Kosten beim Schließen abziehen |
| `SWAP_FX_PCT` / `SWAP_INDEX_PCT` | 0.015 | % pro Tag auf das Nominal (= Einsatz / SL%) |
| `SWAP_GOLD_PCT` | 0.020 | " |
| `SWAP_CRYPTO_PCT` | 0.060 | " |

Manuelles Schließen im Dashboard: **💱 Markt** = zum aktuellen Kurs (echter P&L, mit Vorschau), ✅/❌ = voller TP/SL.
API: `GET /demo/trade/{id}/markt` (Vorschau), `POST /demo/trade/{id}/schliessen?ergebnis=markt|gewonnen|verloren|breakeven`.

Status: `GET /demo/risiko`, außerdem in `/status` und `/selftest`.

### Signalquelle: Regeln + KI-Veto (v3.3)

Die Handelsrichtung kommt aus festen Regeln (`indicators.py`, exakt dieselbe Logik
wie `backtest.py`). Die KI darf einen Trade nur noch **blockieren**, wenn ein
konkretes Ereignis ansteht (Fed/EZB, CPI, NFP …) oder die Nachrichtenlage klar
dagegen spricht. Vorschlagen oder umdrehen kann sie nichts.

Jedes blockierte Signal wird als **V-Zeile** (`V0001` …) ins Excel geschrieben und
im 4-Stunden-Check mitverfolgt, als wäre es gehandelt worden, aber ohne Kapital.
Das Dashboard vergleicht das Ø-Ergebnis in R von ausgeführten und blockierten
Signalen. Ab je 10 abgeschlossenen gibt es ein Urteil, ob das Veto hilft.

Assets mit offenem Trade werden gar nicht erst analysiert. Gibt es kein
Regel-Signal, fällt der KI-Aufruf ganz weg.

| Env-Variable | Default | Wirkung |
|---|---|---|
| `SIGNAL_QUELLE` | regeln | `regeln` = Regeln + KI-Veto, `ki` = alter Ablauf mit sechs Agenten |
| `AGENT_MODEL` | claude-haiku-4-5-20251001 | Modell für alle KI-Aufrufe |
| `REGEL_MIN_CONFLUENCE` | 6 | Mindest-Confluence eines Regel-Signals (wie im Backtest) |
| `CONFLUENCE_RICHTUNGSTREU` | true | RSI- und Bollinger-Punkte zählen bei einem Trend-Signal nur, wenn sie in dieselbe Richtung zeigen. `false` = alte Wertung |

Signale unter der Mindest-Konfidenz werden nicht mehr stillschweigend verworfen,
sondern im Log und in der WhatsApp-Nachricht aufgeführt.

Hinweis: Auch Regel-Signale laufen durch den Konfidenz-Filter im Dashboard
(Konfidenz = Confluence × 10). Min. Konfidenz 60 % entspricht Confluence 6.

### Neuer Abschnitt (Tracker-Reset)

`POST /demo/reset?bestaetigung=RESET` — benennt die laufende `Trading_Tracker.xlsx` in
`Trading_Tracker_archiv_<Zeitstempel>.xlsx` um und startet die Statistik bei 0.
**Es wird nichts gelöscht.** Geht nur, wenn kein Trade mehr offen ist (sonst 409).
Knopf dafür im Konfigurations-Tab.

Gedacht für einen sauberen Schnitt zwischen zwei Parameter-Generationen: stehen
Trades mit alten und neuen SL/TP-Regeln in derselben Statistik, lässt sich hinterher
nicht mehr sagen, woran ein Ergebnis lag.

### Dashboard-Einstellungen, die wirklich wirken (v3.3.1)

| Einstellung | Wirkung |
|---|---|
| Max Risiko / Trade | Einsatz in % vom Kapital bei *Fixer Prozentsatz*, Fallback bei *Half-Kelly*, Basis bei *Anti-Martingale* |
| Stop Loss / Take Profit | nur bei `VOLA_ADAPTIV=false`, sonst gelten die ATR-Stops |
| Position Size | nur für echte Orders (`/trade`, Auto-Trade) |
| Min. Konfidenz | Signale darunter werden nicht gehandelt (Konfidenz = Confluence × 10) |
| Modus *Demo-Trades* | Signale werden automatisch als Demo-Trades eröffnet |
| Modus *Nur Analyse* | nur Signale, keine Demo-Trades, keine Orders |
| Modus *Demo + echte Orders* | wie Demo-Trades, beim Knopf ANALYSE zusätzlich echte Orders (nur mit `ORDERS_ENABLED=true`) |
| ⏸ Pause | pausiert die 07:00-Analyse, bleibt auch nach Neustart pausiert |

Der Backtest (`POST /backtest`) rechnet jetzt wie live: ATR-Stops je Trade
(bei `VOLA_ADAPTIV=true`), Timeout nach `MAX_TRADE_TAGE`, Tageskerzen als Standard
im Dashboard. Der nachgezogene Break-even-Stop ist im Backtest nicht enthalten.
