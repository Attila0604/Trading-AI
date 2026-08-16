"""
backtest.py — Regelbasierter Backtest über historische Kerzen.
────────────────────────────────────────────────────────────────
Signalquelle = indicators.py (RSI/MACD/EMA/BB), KEINE LLM-Calls.
Damit ist der Backtest deterministisch UND dient als Baseline:
Schlägt das Live-System diese reine Regel-Logik nicht, bringt die
LLM-Schicht keinen echten Mehrwert.

Ablauf:
  1) generiere_trades()  – erzeugt die Trade-Sequenz EINMAL aus den Kerzen
     (kapitalunabhängig: Entry/Exit/Ergebnis hängen nur am Preis).
  2) simuliere_mm()      – legt einen Money-Management-Modus über genau
     diese Sequenz und rechnet Kapitalkurve, ROI, Drawdown, Profit-Faktor.
  3) vergleiche_modi()   – läuft alle 6 Modi über dieselben Trades → Tabelle.
"""

import logging
from indicators import calculate_all_indicators
from money_management import berechne_einsatz, MODI

log = logging.getLogger(__name__)


# ── 1. Signal-/Trade-Sequenz aus Kerzen (einmalig, kapitalunabhängig) ────────
def generiere_trades(candles: list, sl_pct: float = 1.5, tp_pct: float = 3.0,
                     min_confluence: int = 6, warmup: int = 50) -> list:
    """
    Läuft Kerze für Kerze durch und erzeugt abgeschlossene Trades.
    Signal aus den Indikatoren; SL/TP-Treffer via Kerzen-High/Low
    (gleiche konservative Logik wie im Live-Check).
    """
    trades = []
    pos = None

    for i in range(warmup, len(candles)):
        bar = candles[i]
        hi, lo = bar.get("high"), bar.get("low")
        if hi is None or lo is None:
            continue

        # Offene Position gegen diese Kerze prüfen
        if pos:
            if pos["action"] == "long":
                sl_hit, tp_hit = lo <= pos["sl"], hi >= pos["tp"]
            else:
                sl_hit, tp_hit = hi >= pos["sl"], lo <= pos["tp"]

            ergebnis = None
            if sl_hit and tp_hit:
                ergebnis = "verloren"          # beide in einer Kerze → konservativ SL
            elif tp_hit:
                ergebnis = "gewonnen"
            elif sl_hit:
                ergebnis = "verloren"

            if ergebnis:
                trades.append({
                    "action":     pos["action"],
                    "entry":      pos["entry"],
                    "exit":       pos["sl"] if ergebnis == "verloren" else pos["tp"],
                    "ergebnis":   ergebnis,
                    "confidence": pos["confidence"],
                    "vola":       pos["vola"],
                    "entry_i":    pos["entry_i"],
                    "exit_i":     i,
                })
                pos = None
                continue   # keine Neueröffnung in derselben Kerze

        # Keine Position → Signal prüfen
        if pos is None:
            window = candles[max(0, i - 199):i + 1]
            ind = calculate_all_indicators(window)
            if "error" in ind:
                continue

            sig  = ind.get("signal", "neutral")
            conf = ind.get("confluenceScore", 5)
            vola = (ind.get("bollinger") or {}).get("width_pct", 0) or 0

            action = None
            if sig in ("buy", "strong buy"):
                action = "long"
            elif sig in ("sell", "strong sell"):
                action = "short"

            if action and conf >= min_confluence:
                entry = bar.get("close")
                if not entry:
                    continue
                if action == "long":
                    sl = entry * (1 - sl_pct / 100)
                    tp = entry * (1 + tp_pct / 100)
                else:
                    sl = entry * (1 + sl_pct / 100)
                    tp = entry * (1 - tp_pct / 100)
                pos = {"action": action, "entry": entry, "sl": sl, "tp": tp,
                       "confidence": conf, "vola": vola, "entry_i": i}

    return trades


# ── 2. Ein MM-Modus über die feste Trade-Sequenz simulieren ──────────────────
def simuliere_mm(trade_seq: list, mm_modus: str = "fixed_percent",
                 startkapital: float = 1000.0, sl_pct: float = 1.5,
                 tp_pct: float = 3.0, params: dict = None) -> dict:
    kapital  = startkapital
    equity   = [round(kapital, 2)]
    peak     = kapital
    max_dd   = 0.0
    wins = losses = 0
    brutto_gewinn = brutto_verlust = 0.0
    verlauf  = []   # letzte Ergebnisse für Kelly/Anti-Martingale

    for t in trade_seq:
        abgeschlossen = wins + losses
        wr = (wins / abgeschlossen * 100) if abgeschlossen else 0.0

        mm = berechne_einsatz(
            mm_modus, kapital,
            ctx={
                "confidence":           t["confidence"] * 10,   # 1-10 → ~10-100
                "win_rate":             wr,
                "gesamt_abgeschlossen": abgeschlossen,
                "sl_pct":               sl_pct,
                "tp_pct":               tp_pct,
                "volatility_pct":       t["vola"],
                "letzte_trades":        [{"Status": x} for x in verlauf[-10:]],
            },
            params=params,
        )
        einsatz = mm["einsatz"]

        if t["ergebnis"] == "gewonnen":
            pnl = einsatz * (tp_pct / sl_pct) if sl_pct > 0 else einsatz
            wins += 1
            brutto_gewinn += pnl
        else:
            pnl = -einsatz
            losses += 1
            brutto_verlust += einsatz

        kapital += pnl
        verlauf.append(t["ergebnis"])
        equity.append(round(kapital, 2))

        peak = max(peak, kapital)
        dd = (peak - kapital) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

        if kapital <= 0:            # Pleite → Abbruch
            log.warning(f"[Backtest] {mm_modus}: Kapital ≤ 0 nach {wins+losses} Trades")
            break

    abgeschlossen = wins + losses
    profit_factor = round(brutto_gewinn / brutto_verlust, 2) if brutto_verlust > 0 else (
        float("inf") if brutto_gewinn > 0 else 0.0)

    return {
        "mm_modus":         mm_modus,
        "mm_name":          MODI.get(mm_modus, {}).get("name", mm_modus),
        "startkapital":     round(startkapital, 2),
        "endkapital":       round(kapital, 2),
        "roi_pct":          round((kapital - startkapital) / startkapital * 100, 2) if startkapital > 0 else 0,
        "trades":           abgeschlossen,
        "gewonnen":         wins,
        "verloren":         losses,
        "win_rate":         round(wins / abgeschlossen * 100, 1) if abgeschlossen else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor":    profit_factor if profit_factor != float("inf") else "∞",
        "equity_curve":     equity,
    }


# ── 3. Alle Modi über dieselbe Trade-Sequenz vergleichen ─────────────────────
def vergleiche_modi(candles: list, startkapital: float = 1000.0,
                    sl_pct: float = 1.5, tp_pct: float = 3.0,
                    min_confluence: int = 6, warmup: int = 50) -> dict:
    if not candles or len(candles) < warmup + 10:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0} (min {warmup + 10})"}

    trade_seq = generiere_trades(candles, sl_pct, tp_pct, min_confluence, warmup)

    ergebnisse = []
    for modus in MODI.keys():
        res = simuliere_mm(trade_seq, modus, startkapital, sl_pct, tp_pct)
        res.pop("equity_curve", None)   # aus der Vergleichs-Tabelle raus (zu groß)
        ergebnisse.append(res)

    # nach ROI absteigend sortieren
    ergebnisse.sort(key=lambda r: r["roi_pct"], reverse=True)

    return {
        "kerzen":         len(candles),
        "signal_trades":  len(trade_seq),
        "parameter":      {"sl_pct": sl_pct, "tp_pct": tp_pct,
                           "min_confluence": min_confluence, "startkapital": startkapital},
        "baseline_hinweis": ("Regelbasierte Baseline (RSI/MACD/EMA/BB, ohne LLM). "
                             "Alle Modi laufen über dieselben Trades - nur die Positionsgröße unterscheidet sich."),
        "ergebnisse":     ergebnisse,
        "bester_modus":   ergebnisse[0]["mm_modus"] if ergebnisse else None,
    }
