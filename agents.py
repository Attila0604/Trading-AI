import os, json, logging
from anthropic import Anthropic

log = logging.getLogger(__name__)
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def call_claude(user_prompt: str, system_prompt: str, web_search: bool = False) -> str:
    kwargs = dict(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    resp = client.messages.create(**kwargs)
    return "".join(b.text for b in resp.content if b.type == "text").replace("```json", "").replace("```", "").strip()


def parse_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception as e:
        log.warning(f"JSON-Parse-Fehler: {e} | Raw: {raw[:200]}")
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


# ── AGENT 2: TECHNICAL ANALYST ──
def tech_agent(assets: list[str], strategy: str, timeframe: str) -> list[dict]:
    log.info(f"[Tech Analyst] Analysiere {assets} | Strategie: {strategy}")
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


# ── MAIN PIPELINE ──
async def run_pipeline(assets: list[str], strategy: str = "adaptive", risk_pct: float = 2.0,
                       sl_pct: float = 1.5, tp_pct: float = 3.0, position_size: float = 1000) -> dict:
    import asyncio

    log.info("=" * 60)
    log.info(f"TRADING PIPELINE START | {', '.join(assets)}")
    log.info("=" * 60)

    strat_info = STRATEGY_MAP.get(strategy, STRATEGY_MAP["adaptive"])
    tf         = strat_info["timeframe"]
    loop       = asyncio.get_event_loop()

    news_data  = await loop.run_in_executor(None, news_agent, assets)
    await asyncio.sleep(30)
    tech_data  = await loop.run_in_executor(None, tech_agent, assets, strategy, tf)
    await asyncio.sleep(30)
    macro_data = await loop.run_in_executor(None, macro_agent)
    await asyncio.sleep(30)
    risk_data  = await loop.run_in_executor(None, risk_agent, news_data, tech_data, macro_data, risk_pct, sl_pct, tp_pct)
    await asyncio.sleep(10)
    strat_data = await loop.run_in_executor(None, strategy_agent, strategy, macro_data, tech_data)
    await asyncio.sleep(30)
    result     = await loop.run_in_executor(None, orchestrator_agent, news_data, tech_data, macro_data,
                                            risk_data, strat_data, assets, risk_pct, sl_pct, tp_pct)

    result["agentReports"] = {
        "news":     news_data,
        "tech":     tech_data,
        "macro":    macro_data,
        "risk":     risk_data,
        "strategy": strat_data,
    }
    result["timestamp"]    = __import__('datetime').datetime.now().isoformat()
    result["assets"]       = assets
    result["strategyUsed"] = strat_data.get("name", strategy)

    log.info(f"PIPELINE FERTIG | Score: {result.get('sessionScore')}/100 | Signale: {len([d for d in result.get('decisions',[]) if d.get('action')!='hold'])}")
    return result
