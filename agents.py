import os, json, logging
from anthropic import Anthropic

log = logging.getLogger(__name__)

# Kerzen-Basis für die Analyse. Wochen-Haltedauer -> Tageskerzen.
# Über Env umstellbar, falls doch kurzfristiger gehandelt werden soll.
KERZEN_AUFLOESUNG = os.getenv("KERZEN_AUFLOESUNG", "DAY").strip() or "DAY"
try:
    KERZEN_ANZAHL = int(os.getenv("KERZEN_ANZAHL", "300"))
except (ValueError, TypeError):
    KERZEN_ANZAHL = 300
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─── Wer entscheidet die Richtung? ───────────────────────────────────────────
# "regeln" (Standard): feste Indikator-Regeln bestimmen Long/Short/nichts.
#   Die KI darf einen Trade nur noch BLOCKIEREN (Veto), nie vorschlagen.
#   Hintergrund: In den ersten 52 Trades war die KI-Konfidenz umgekehrt
#   aussagekräftig (90%+ -> schlechteste Gruppe), und öffentliche News sind
#   bei EUR/USD, S&P, Gold, BTC längst eingepreist. Wo ein Sprachmodell
#   dagegen stark ist: Termine und Ereignisrisiken erkennen.
# "ki": alter Ablauf mit sechs Agenten, die KI entscheidet die Richtung.
SIGNAL_QUELLE = (os.getenv("SIGNAL_QUELLE", "regeln").strip().lower() or "regeln")
if SIGNAL_QUELLE not in ("regeln", "ki"):
    SIGNAL_QUELLE = "regeln"

# Modell für alle KI-Aufrufe - über Railway umstellbar, ohne Code-Änderung.
AGENT_MODEL = os.getenv("AGENT_MODEL", "").strip() or "claude-haiku-4-5-20251001"

# Mindest-Confluence der Regel-Signale. 6 = derselbe Wert wie im Backtest,
# damit Live-Regeln und Backtest exakt dieselbe Logik haben.
try:
    REGEL_MIN_CONFLUENCE = int(os.getenv("REGEL_MIN_CONFLUENCE", "6"))
except (ValueError, TypeError):
    REGEL_MIN_CONFLUENCE = 6

# Asset → Capital.com Epic Mapping
EPIC_MAP = {
    "BTC/USD": "BTCUSD",
    "ETH/USD": "ETHUSD",
    "EUR/USD": "EURUSD",
    "XAU/USD": "GOLD",
    "US500":   "US500",
}


def call_claude(user_prompt: str, system_prompt: str, web_search: bool = False,
                max_tokens: int = 1500) -> str:
    kwargs = dict(
        model=AGENT_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    resp = client.messages.create(**kwargs)
    return "".join(b.text for b in resp.content if b.type == "text").replace("```json", "").replace("```", "").strip()


def parse_json(raw: str, fallback):
    """Robuster JSON-Parser: behandelt Vorspann und Trailing-Text."""
    if not raw or not raw.strip():
        log.warning("Leere LLM-Antwort")
        return fallback
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    obj_start = raw.find('{')
    arr_start = raw.find('[')
    candidates = [s for s in (obj_start, arr_start) if s != -1]
    if not candidates:
        log.warning(f"JSON-Parse-Fehler: Kein JSON gefunden | Raw: {raw[:200]}")
        return fallback
    start = min(candidates)
    open_char = raw[start]
    close_char = '}' if open_char == '{' else ']'
    depth = 0
    for i, char in enumerate(raw[start:], start):
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start:i+1])
                    log.info("JSON aus Antwort mit Vorspann extrahiert")
                    return parsed
                except json.JSONDecodeError as e:
                    log.warning(f"JSON-Parse-Fehler: {e} | Raw: {raw[:200]}")
                    return fallback
    log.warning(f"JSON-Parse-Fehler: Unbalanciertes JSON | Raw: {raw[:200]}")
    return fallback


# ── AGENT 1: NEWS SENTINEL ──
def news_agent(assets: list[str]) -> list[dict]:
    log.info(f"[News Sentinel] Analysiere Nachrichten für {assets}")
    raw = call_claude(
        f"""Analysiere aktuelle Finanznachrichten für: {', '.join(assets)}.
Datum/Zeit: {__import__('datetime').datetime.now().strftime('%d.%m.%Y %H:%M')}.
Antworte NUR mit JSON-Array (kein Markdown):
[{{"asset":"string","sentiment":"bullish|bearish|neutral","score":-100 bis 100,"keyNews":["news1","news2"],"tradingImplication":"string auf Deutsch","urgency":"low|medium|high"}}]""",
        "Du bist ein Financial News Intelligence Agent. Antworte AUSSCHLIESSLICH mit validem JSON-Array, kein Markdown.",
        web_search=True,
    )
    fallback = [{"asset": a, "sentiment": "neutral", "score": 0, "keyNews": ["N/A"], "tradingImplication": "Neutral", "urgency": "low"} for a in assets]
    return parse_json(raw, fallback)


# ── AGENT 2: TECHNICAL ANALYST (mit echten Marktdaten!) ──
def tech_agent(assets: list[str], strategy: str, timeframe: str, market_data: dict = None) -> list[dict]:
    log.info(f"[Tech Analyst] Analysiere {assets} | Strategie: {strategy}")
    from indicators import calculate_all_indicators

    indicators_per_asset = {}
    if market_data:
        for asset in assets:
            candles = market_data.get(asset, [])
            if candles and len(candles) >= 30:
                ind = calculate_all_indicators(candles)
                if "error" not in ind:
                    indicators_per_asset[asset] = ind
                    log.info(
                        f"[Tech Analyst] {asset}: Preis={ind.get('currentPrice')} "
                        f"RSI={ind.get('rsi')} Trend={ind.get('trend')} "
                        f"MACD={ind.get('macd', {}).get('trend')} "
                        f"BB={ind.get('bollinger', {}).get('position')}"
                    )

    # Wenn echte Daten da → Claude interpretiert die echten Werte
    if indicators_per_asset:
        market_context = "\n".join([
            f"- {asset}: Preis={d.get('currentPrice')}, RSI={d.get('rsi')}, "
            f"Trend={d.get('trend')}, MACD={d.get('macd', {}).get('trend') if d.get('macd') else 'n/a'} "
            f"(hist={d.get('macd', {}).get('histogram') if d.get('macd') else 'n/a'}), "
            f"EMA20={d.get('ema20')}, EMA50={d.get('ema50')}, "
            f"BB-Position={d.get('bollinger', {}).get('position') if d.get('bollinger') else 'n/a'}, "
            f"BB-Width={d.get('bollinger', {}).get('width_pct') if d.get('bollinger') else 'n/a'}%, "
            f"Confluence={d.get('confluenceScore')}/10"
            for asset, d in indicators_per_asset.items()
        ])
        raw = call_claude(
            f"""Du erhältst ECHTE berechnete Indikator-Werte aus 4H-Kerzen. Interpretiere sie.

Strategie-Fokus: {strategy}. Zeitrahmen: {timeframe}.

ECHTE MARKTDATEN:
{market_context}

Bewerte das Setup pro Asset. Verwende die ECHTEN RSI/MACD-Werte (nicht raten!).
Antworte NUR mit JSON-Array:
[{{"asset":"string","trend":"uptrend|downtrend|sideways","signal":"strong buy|buy|neutral|sell|strong sell","rsi":<echter RSI>,"macdSignal":"bullish|bearish|neutral","emaAlignment":"bullish|bearish|mixed","bbPosition":"upper|middle|lower|breakout","confluenceScore":1-10,"notes":"konkrete Begründung auf Deutsch"}}]""",
            "Du bist ein Technical Analysis Agent. Du erhältst ECHTE berechnete Indikatorwerte und interpretierst sie. Antworte AUSSCHLIESSLICH mit validem JSON-Array. Übernimm die echten RSI-Werte aus den Daten.",
        )
        # Fallback: berechnete Werte direkt nutzen
        fallback = []
        for asset in assets:
            ind = indicators_per_asset.get(asset, {})
            fallback.append({
                "asset":           asset,
                "trend":           ind.get("trend", "sideways"),
                "signal":          ind.get("signal", "neutral"),
                "rsi":             ind.get("rsi", 50),
                "macdSignal":      ind.get("macd", {}).get("trend", "neutral") if ind.get("macd") else "neutral",
                "emaAlignment":    ind.get("emaAlignment", "mixed"),
                "bbPosition":      ind.get("bollinger", {}).get("position", "middle") if ind.get("bollinger") else "middle",
                "confluenceScore": ind.get("confluenceScore", 5),
                "notes":           "Aus berechneten Indikatoren (Claude-Fallback)",
            })
        return parse_json(raw, fallback)

    # Notfall-Pfad: keine Marktdaten verfügbar
    log.warning("[Tech Analyst] ⚠️ Keine Marktdaten - falle auf Schätzung zurück")
    raw = call_claude(
        f"""Technische Analyse für: {', '.join(assets)}.
Strategie-Fokus: {strategy}. Zeitrahmen: {timeframe}.
Antworte NUR mit JSON-Array:
[{{"asset":"string","trend":"uptrend|downtrend|sideways","signal":"strong buy|buy|neutral|sell|strong sell","rsi":0-100,"macdSignal":"bullish|bearish|neutral","emaAlignment":"bullish|bearish|mixed","bbPosition":"upper|middle|lower|breakout","confluenceScore":1-10,"notes":"string DE"}}]""",
        "Du bist ein professioneller Technical Analysis Agent. Antworte NUR mit validem JSON-Array.",
    )
    fallback = [{"asset": a, "trend": "sideways", "signal": "neutral", "rsi": 50, "macdSignal": "neutral", "emaAlignment": "mixed", "bbPosition": "middle", "confluenceScore": 5} for a in assets]
    return parse_json(raw, fallback)


# ── AGENT 3: MACRO SCOUT ──
def macro_agent() -> dict:
    log.info("[Macro Scout] Makroökonomische Analyse...")
    raw = call_claude(
        f"""Analysiere das aktuelle makroökonomische Umfeld ({__import__('datetime').datetime.now().strftime('%d.%m.%Y')}).
Faktoren: Fed/EZB Zinspolitik, USD-Stärke, VIX-Level, Inflation, Risikoappetit, wichtige Wirtschaftsdaten.
Antworte NUR mit JSON:
{{"environment":"risk-on|risk-off|mixed","score":-100 bis 100,"usdStrength":"strong|neutral|weak","riskAppetite":"high|medium|low","keyFactors":["f1","f2","f3"],"outlook":"1 Satz auf Deutsch"}}""",
        "Du bist ein Makroökonomie-Analyse Agent. Antworte AUSSCHLIESSLICH mit validem JSON.",
        web_search=True,
    )
    return parse_json(raw, {"environment": "mixed", "score": 0, "usdStrength": "neutral", "riskAppetite": "medium", "keyFactors": ["N/A"], "outlook": "Neutral"})


# ── AGENT 4: RISK GUARDIAN ──
def risk_agent(news: list, tech: list, macro: dict, risk_pct: float, sl: float, tp: float) -> dict:
    log.info("[Risk Guardian] Risiko-Assessment...")
    bullish_news   = sum(1 for n in news if n.get("sentiment") == "bullish")
    bearish_news   = sum(1 for n in news if n.get("sentiment") == "bearish")
    avg_confluence = sum(t.get("confluenceScore", 5) for t in tech) / max(len(tech), 1)
    macro_env      = macro.get("environment", "mixed")

    risk_score = avg_confluence * 10
    if macro_env == "risk-off":
        risk_score *= 0.7
    if bearish_news > bullish_news:
        risk_score *= 0.85

    return {
        "approved":         risk_score > 40,
        "riskScore":        round(risk_score),
        "maxRiskPct":       risk_pct,
        "stopLossPct":      sl,
        "takeProfitPct":    tp,
        "avgConfluence":    round(avg_confluence, 1),
        "macroEnvironment": macro_env,
        "message":          "Risiko akzeptabel" if risk_score > 40 else "Zu hohes Risiko – Abwarten empfohlen",
    }


# ── AGENT 5: STRATEGY COMMANDER ──
STRATEGY_MAP = {
    "trend":     {"name": "Trend Following",  "timeframe": "4H–1D",     "risk": "MITTEL"},
    "reversion": {"name": "Mean Reversion",   "timeframe": "1H–4H",     "risk": "NIEDRIG"},
    "news_play": {"name": "News Catalyst",    "timeframe": "5M–1H",     "risk": "HOCH"},
    "breakout":  {"name": "Breakout Hunter",  "timeframe": "1H–4H",     "risk": "MITTEL"},
    "scalping":  {"name": "Scalp Modus",      "timeframe": "1M–5M",     "risk": "HOCH"},
    "adaptive":  {"name": "KI Adaptiv",       "timeframe": "Dynamisch", "risk": "VARIABEL"},
}

def strategy_agent(strategy_id: str, macro: dict, tech: list) -> dict:
    log.info(f"[Strategy AI] Konfiguriere Strategie: {strategy_id}")
    strat = STRATEGY_MAP.get(strategy_id, STRATEGY_MAP["adaptive"])
    if strategy_id == "adaptive":
        env     = macro.get("environment", "mixed")
        appetite = macro.get("riskAppetite", "medium")
        if env == "risk-on" and appetite == "high":
            strat = STRATEGY_MAP["trend"]
        elif env == "risk-off":
            strat = STRATEGY_MAP["reversion"]
        else:
            avg_score = sum(t.get("confluenceScore", 5) for t in tech) / max(len(tech), 1)
            strat = STRATEGY_MAP["breakout"] if avg_score > 7 else STRATEGY_MAP["trend"]
    return strat


# ── AGENT 6 & 7: ORCHESTRATOR + EXECUTOR ──
def orchestrator_agent(news: list, tech: list, macro: dict, risk: dict, strategy: dict,
                       assets: list, risk_pct: float, sl: float, tp: float) -> dict:
    log.info("[Orchestrator] Synthetisiere alle Agent-Reports...")
    raw = call_claude(
        f"""Du bist der Master Trading Orchestrator. Synthetisiere alle Agent-Berichte zu finalen Trade-Entscheidungen.

NEWS REPORT:
{json.dumps(news, ensure_ascii=False)}

TECHNICAL REPORT:
{json.dumps(tech, ensure_ascii=False)}

MACRO REPORT:
{json.dumps(macro, ensure_ascii=False)}

RISK ASSESSMENT:
{json.dumps(risk, ensure_ascii=False)}

STRATEGIE: {strategy.get('name')} | TF: {strategy.get('timeframe')} | Risk: {strategy.get('risk')}

RISIKO-SETTINGS:
- Max Risiko: {risk_pct}% pro Trade
- Stop Loss: {sl}% (PROZENTSATZ vom Entry-Preis, NICHT absoluter Preis!)
- Take Profit: {tp}% (PROZENTSATZ vom Entry-Preis, NICHT absoluter Preis!)

WICHTIG: stopLoss und takeProfit MÜSSEN Prozentsätze zwischen 0.1 und 20.0 sein!
Beispiel: stopLoss: 1.5 bedeutet 1.5% unter dem Entry-Preis.
NIEMALS den absoluten Preis (z.B. 6550 oder 44775) als stopLoss/takeProfit angeben!

Antworte NUR mit JSON:
{{
  "decisions": [{{
    "asset": "string",
    "action": "buy|sell|hold",
    "direction": "long|short|none",
    "confidence": 0-100,
    "entryReason": "string kurz auf Deutsch",
    "riskReward": number,
    "stopLoss": {sl},
    "takeProfit": {tp},
    "urgency": "immediate|wait|watch",
    "summary": "2-3 Sätze auf Deutsch"
  }}],
  "marketOverview": "2 Sätze auf Deutsch",
  "sessionScore": 0-100,
  "recommendation": "string auf Deutsch"
}}""",
        "Du bist der Master Trading Orchestrator. Antworte AUSSCHLIESSLICH mit validem JSON. stopLoss und takeProfit sind IMMER Prozentsätze (z.B. 1.5 für 1.5%), NIEMALS absolute Preise!",
    )
    fallback = {
        "decisions": [{"asset": a, "action": "hold", "direction": "none", "confidence": 30,
                       "entryReason": "Fehler", "riskReward": 0, "stopLoss": sl, "takeProfit": tp,
                       "urgency": "watch", "summary": "Analyse fehlgeschlagen."} for a in assets],
        "marketOverview": "Analyse fehlgeschlagen.",
        "sessionScore":   0,
        "recommendation": "Manuell prüfen",
    }
    return parse_json(raw, fallback)


# ── AGENT 8: VETO-PRÜFER (nur im Modus "regeln") ──
def veto_agent(signale: list[dict]) -> dict:
    """
    Prüft Regel-Signale auf konkrete Ereignisrisiken. Darf NUR blockieren.

    Gibt zurück: {"pruefungen": {asset: {"veto", "grund", "ereignis"}},
                  "marktlage": str, "fehler": bool}

    Scheitert der Aufruf oder die Antwort ist unbrauchbar, gibt es KEIN Veto
    (die Regeln handeln trotzdem) - aber es wird als Fehler markiert, damit
    solche Tage bei der Auswertung erkennbar bleiben.
    """
    from config import jetzt   # Railway läuft in UTC - der Prüfer braucht Wiener Zeit
    log.info(f"[Veto-Prüfer] Prüfe {len(signale)} Regel-Signal(e) mit {AGENT_MODEL}")

    zeilen = []
    for s in signale:
        ind = s.get("indikatoren", {})
        zeilen.append(
            f"- {s['asset']}: geplant {s['direction'].upper()} | Preis {ind.get('currentPrice')} | "
            f"Trend {ind.get('trend')} | RSI {ind.get('rsi')} | MACD {ind.get('macd_trend')} | "
            f"Confluence {ind.get('confluenceScore')}/10 | Tages-ATR {ind.get('atrPct')}%"
        )

    raw = call_claude(
        f"""Datum/Zeit: {jetzt().strftime('%d.%m.%Y %H:%M')} Uhr Wiener Zeit ({['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag'][jetzt().weekday()]}).

Ein regelbasiertes System will folgende Positionen eröffnen. Geplante Haltedauer:
mehrere Wochen, weite volatilitätsabhängige Stops.

{chr(10).join(zeilen)}

Recherchiere für JEDES dieser Assets:
1. Termine mit hoher Tragweite in den NÄCHSTEN 48 STUNDEN, die genau dieses Asset
   stark bewegen können: Zinsentscheid Fed/EZB, US-Inflation (CPI/PCE),
   US-Arbeitsmarktbericht (NFP), bei US500 sehr große Quartalszahlen,
   bei BTC regulatorische Entscheidungen oder ETF-Beschlüsse.
2. Eine AKTUELLE, KONKRETE Nachrichtenlage, die der geplanten Richtung klar
   widerspricht (z.B. überraschende Notenbank-Aussage, Kriegseskalation,
   Börsen-Hack, Handelsstopp).

REGELN FÜR DICH:
- Du bestimmst NICHT die Richtung. Du kannst einen Trade nur blockieren.
- Blockiere NUR mit konkretem, benennbarem Grund (Ereignis + Datum oder
  konkrete Meldung). Allgemeine Unsicherheit, "hohe Volatilität" oder deine
  eigene Meinung über die Richtung sind KEIN Grund.
- Im Zweifel: KEIN Veto.

Antworte NUR mit JSON:
{{"pruefungen":[{{"asset":"string","veto":true|false,"grund":"1 Satz auf Deutsch","ereignis":"Name und Datum oder null"}}],"marktlage":"2 Sätze auf Deutsch zur allgemeinen Lage"}}""",
        "Du bist ein Risiko-Prüfer für ein regelbasiertes Handelssystem. Die Handelsrichtung "
        "bestimmen feste Regeln, nicht du. Du darfst Trades ausschließlich blockieren, und nur "
        "bei konkretem Ereignisrisiko. Antworte AUSSCHLIESSLICH mit validem JSON.",
        web_search=True,
        max_tokens=2500,
    )

    parsed = parse_json(raw, None)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("pruefungen"), list):
        log.warning("[Veto-Prüfer] Keine brauchbare Antwort → kein Veto (Regeln handeln)")
        return {"pruefungen": {}, "marktlage": "KI-Prüfung nicht verfügbar.", "fehler": True}

    pruefungen = {}
    for p in parsed["pruefungen"]:
        if not isinstance(p, dict) or not p.get("asset"):
            continue
        veto = p.get("veto") is True or str(p.get("veto")).lower() == "true"
        pruefungen[str(p["asset"]).strip()] = {
            "veto":     veto,
            "grund":    str(p.get("grund") or "").strip()[:200],
            "ereignis": (str(p.get("ereignis")).strip()[:120]
                         if p.get("ereignis") not in (None, "", "null") else None),
        }
    n_veto = sum(1 for v in pruefungen.values() if v["veto"])
    log.info(f"[Veto-Prüfer] {n_veto} von {len(signale)} blockiert")
    return {"pruefungen": pruefungen,
            "marktlage": str(parsed.get("marktlage") or "").strip()[:400],
            "fehler": False}


def regel_signal(asset: str, candles: list) -> dict:
    """
    Richtung aus festen Regeln - EXAKT dieselbe Logik wie backtest.py
    (letzte 200 Kerzen, Indikator-Signal, Mindest-Confluence). So sind
    Live-Ergebnisse und Backtest direkt vergleichbar.
    """
    from indicators import calculate_all_indicators
    fenster = candles[-200:]
    ind = calculate_all_indicators(fenster)
    if "error" in ind:
        return {"asset": asset, "signal": None, "grund": ind["error"], "indikatoren": {}}

    kompakt = {
        "currentPrice":    ind.get("currentPrice"),
        "trend":           ind.get("trend"),
        "rsi":             ind.get("rsi"),
        "macd_trend":      (ind.get("macd") or {}).get("trend"),
        "emaAlignment":    ind.get("emaAlignment"),
        "bbPosition":      (ind.get("bollinger") or {}).get("position"),
        "confluenceScore": ind.get("confluenceScore"),
        "atrPct":          ind.get("atrPct"),
    }
    sig  = ind.get("signal", "neutral")
    conf = int(ind.get("confluenceScore") or 0)
    richtung = "long" if sig in ("buy", "strong buy") else ("short" if sig in ("sell", "strong sell") else None)

    if richtung is None:
        grund = f"kein Signal ({kompakt['trend']}, RSI {kompakt['rsi']}, MACD {kompakt['macd_trend']})"
        return {"asset": asset, "signal": None, "grund": grund, "indikatoren": kompakt}
    if conf < REGEL_MIN_CONFLUENCE:
        grund = f"{richtung} zu schwach (Confluence {conf}/10 < {REGEL_MIN_CONFLUENCE})"
        return {"asset": asset, "signal": None, "grund": grund, "indikatoren": kompakt}

    grund = (f"{sig} | Trend {kompakt['trend']}, MACD {kompakt['macd_trend']}, "
             f"RSI {kompakt['rsi']}, Confluence {conf}/10")
    return {"asset": asset, "signal": richtung, "confluence": conf,
            "grund": grund, "indikatoren": kompakt}


async def _marktdaten_holen(assets: list[str], capital) -> dict:
    market_data = {}
    for asset in assets:
        epic = EPIC_MAP.get(asset, asset.replace("/", ""))
        candles = await capital.get_historical_prices(epic, KERZEN_AUFLOESUNG, KERZEN_ANZAHL)
        if candles:
            market_data[asset] = candles
        else:
            log.warning(f"[Market Data] {asset}: keine Kerzen empfangen")
    return market_data


def _vola_und_atr(market_data: dict) -> tuple:
    """Bollinger-Breite und ATR% je Asset aus den ROHEN Indikatoren."""
    from indicators import calculate_all_indicators
    volatility, atr_werte = {}, {}
    for asset, candles in market_data.items():
        try:
            if candles and len(candles) >= 30:
                ind = calculate_all_indicators(candles)
                bb = ind.get("bollinger") or {}
                if bb.get("width_pct") is not None:
                    volatility[asset] = bb["width_pct"]
                if ind.get("atrPct") is not None:
                    atr_werte[asset] = ind["atrPct"]
        except Exception as e:
            log.warning(f"[Volatilität] {asset}: {e}")
    return volatility, atr_werte


async def _regel_pipeline(assets: list[str], market_data: dict) -> dict:
    """Regeln bestimmen die Richtung, die KI darf nur blockieren."""
    import asyncio
    loop = asyncio.get_event_loop()

    regel = {}
    for asset in assets:
        candles = market_data.get(asset) or []
        if len(candles) < 60:
            regel[asset] = {"asset": asset, "signal": None,
                            "grund": f"zu wenige Kerzen ({len(candles)})", "indikatoren": {}}
        else:
            regel[asset] = regel_signal(asset, candles)
        r = regel[asset]
        log.info(f"[Regeln] {asset}: {(r['signal'] or '—').upper()} | {r['grund']}")

    signale = [r for r in regel.values() if r.get("signal")]
    for s in signale:
        s["direction"] = s["signal"]

    # KI nur aufrufen, wenn es überhaupt etwas zu prüfen gibt
    if signale:
        veto = await loop.run_in_executor(None, veto_agent, signale)
    else:
        log.info("[Veto-Prüfer] Keine Regel-Signale → kein KI-Aufruf nötig")
        veto = {"pruefungen": {}, "marktlage": "Keine Regel-Signale heute.", "fehler": False}

    decisions = []
    for asset in assets:
        r = regel[asset]
        if not r.get("signal"):
            decisions.append({
                "asset": asset, "action": "hold", "direction": "none", "confidence": 0,
                "riskReward": 0, "urgency": "watch", "signalQuelle": "regeln",
                "entryReason": r["grund"], "summary": f"Regeln: {r['grund']}",
            })
            continue
        p = veto["pruefungen"].get(asset, {})
        ist_veto = bool(p.get("veto"))
        conf = int(r["confluence"]) * 10
        summary = f"Regeln: {r['grund']}"
        if ist_veto:
            summary = f"⛔ KI-VETO: {p.get('grund') or 'ohne Begründung'}" + \
                      (f" ({p['ereignis']})" if p.get("ereignis") else "") + f" | {summary}"
        elif veto.get("fehler"):
            summary += " | KI-Prüfung fehlgeschlagen - ohne Veto gehandelt"
        elif p.get("grund"):
            summary += f" | KI: {p['grund']}"
        decisions.append({
            "asset":        asset,
            "action":       "buy" if r["signal"] == "long" else "sell",
            "direction":    r["signal"],
            "confidence":   conf,
            "riskReward":   0,
            "urgency":      "wait" if ist_veto else "immediate",
            "status":       "rejected" if ist_veto else "pending",   # Dashboard: ✕ ABGELEHNT
            "signalQuelle": "regeln",
            "veto":         ist_veto,
            "vetoGrund":    p.get("grund") if ist_veto else "",
            "vetoEreignis": p.get("ereignis") if ist_veto else None,
            "vetoFehler":   bool(veto.get("fehler")),
            "entryReason":  r["grund"],
            "summary":      summary,
        })

    n_sig = len(signale)
    avg_conf = (sum(int(s["confluence"]) for s in signale) / n_sig) if n_sig else 0
    return {
        "decisions":      decisions,
        "marketOverview": veto.get("marktlage", ""),
        "sessionScore":   round(avg_conf * 10),
        "recommendation": f"{n_sig} Regel-Signal(e), "
                          f"{sum(1 for d in decisions if d.get('veto'))} per KI-Veto blockiert",
        "strategyUsed":   "Regeln + KI-Veto",
        "agentReports": {
            # Risk-Gate aus dem alten Ablauf: hier immer frei, der Schutz läuft
            # über Veto + Portfolio-Schutz im demo_tracker.
            "risk":   {"approved": True, "message": "Regel-Modus"},
            "regeln": regel,
            "veto":   veto,
        },
    }


async def _ki_pipeline(assets: list[str], strategy: str, risk_pct: float, sl_pct: float,
                       tp_pct: float, market_data: dict) -> dict:
    """Alter Ablauf: sechs Agenten, die KI entscheidet die Richtung."""
    import asyncio
    strat_info = STRATEGY_MAP.get(strategy, STRATEGY_MAP["adaptive"])
    tf         = strat_info["timeframe"]
    loop       = asyncio.get_event_loop()

    news_data  = await loop.run_in_executor(None, news_agent, assets)
    await asyncio.sleep(30)
    tech_data  = await loop.run_in_executor(None, tech_agent, assets, strategy, tf, market_data)
    await asyncio.sleep(30)
    macro_data = await loop.run_in_executor(None, macro_agent)
    await asyncio.sleep(30)
    risk_data  = await loop.run_in_executor(None, risk_agent, news_data, tech_data, macro_data, risk_pct, sl_pct, tp_pct)
    await asyncio.sleep(10)
    strat_data = await loop.run_in_executor(None, strategy_agent, strategy, macro_data, tech_data)
    await asyncio.sleep(30)
    result     = await loop.run_in_executor(None, orchestrator_agent, news_data, tech_data, macro_data,
                                            risk_data, strat_data, assets, risk_pct, sl_pct, tp_pct)
    for d in result.get("decisions", []):
        d["signalQuelle"] = "ki"
    result["agentReports"] = {
        "news": news_data, "tech": tech_data, "macro": macro_data,
        "risk": risk_data, "strategy": strat_data,
    }
    result["strategyUsed"] = strat_data.get("name", strategy)
    return result


# ── MAIN PIPELINE ──
async def run_pipeline(assets: list[str], strategy: str = "adaptive", risk_pct: float = 2.0,
                       sl_pct: float = 1.5, tp_pct: float = 3.0, position_size: float = 1000,
                       capital_client=None, skip_assets: list[str] = None) -> dict:
    from datetime import datetime
    from capital_client import CapitalClient

    log.info("=" * 60)
    log.info(f"TRADING PIPELINE START | {', '.join(assets)} | Signalquelle: {SIGNAL_QUELLE} | Modell: {AGENT_MODEL}")
    log.info("=" * 60)

    # Assets mit offenem Trade gar nicht erst analysieren - ein neuer Trade
    # würde ohnehin abgelehnt, die KI-Aufrufe wären verschenkt.
    skip  = {a for a in (skip_assets or []) if a in assets}
    aktiv = [a for a in assets if a not in skip]
    if skip:
        log.info(f"[Pipeline] Übersprungen (Trade offen): {', '.join(sorted(skip))}")

    basis = {
        "timestamp":    datetime.now().isoformat(),
        "assets":       assets,
        "signalQuelle": SIGNAL_QUELLE,
        "agentModel":   AGENT_MODEL,
        "uebersprungen": sorted(skip),
    }
    if not aktiv:
        log.info("[Pipeline] Alle Assets haben offene Trades → nichts zu analysieren, keine KI-Aufrufe")
        return {**basis, "decisions": [], "marketOverview": "Alle Assets haben bereits offene Trades.",
                "sessionScore": 0, "recommendation": "Warten, bis Trades geschlossen sind",
                "strategyUsed": "Regeln + KI-Veto" if SIGNAL_QUELLE == "regeln" else strategy,
                "agentReports": {"risk": {"approved": True}}, "volatility": {}, "atr_pct": {}}

    # ── 0. ECHTE MARKTDATEN HOLEN ──
    # TAGES-Kerzen: die Haltedauer liegt bei Wochen, nicht Stunden.
    log.info(f"[Market Data] Hole {KERZEN_AUFLOESUNG}-Kerzen für {', '.join(aktiv)}...")
    # Vorhandenen Client mitbenutzen statt eine ZWEITE Sitzung aufzubauen.
    # Capital.com erlaubt nur eine Anmeldung pro Sekunde.
    capital = capital_client or CapitalClient()
    if not capital.is_connected():
        await capital.connect()
    market_data = await _marktdaten_holen(aktiv, capital)

    if SIGNAL_QUELLE == "ki":
        result = await _ki_pipeline(aktiv, strategy, risk_pct, sl_pct, tp_pct, market_data)
    else:
        result = await _regel_pipeline(aktiv, market_data)

    volatility, atr_werte = _vola_und_atr(market_data)
    result.update(basis)
    result["volatility"] = volatility
    result["atr_pct"]    = atr_werte
    log.info(f"[Volatilität] {volatility}")
    log.info(f"[ATR%] {atr_werte}")

    log.info(f"PIPELINE FERTIG | Score: {result.get('sessionScore')}/100 | "
             f"Signale: {len([d for d in result.get('decisions', []) if d.get('action') != 'hold'])} | "
             f"Vetos: {len([d for d in result.get('decisions', []) if d.get('veto')])}")
    return result
