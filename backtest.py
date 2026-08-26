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
def berechne_signale(candles: list, warmup: int = 50) -> list:
    """
    Berechnet die Indikator-Signale EINMAL pro Kerze.
    Teuerster Teil des Backtests - wird für alle SL/TP-Kombinationen
    wiederverwendet, sonst dauert eine Optimierung ewig.
    Liefert Liste von (index, action, confluence, vola, close).
    """
    signale = []
    for i in range(warmup, len(candles)):
        window = candles[max(0, i - 199):i + 1]
        ind = calculate_all_indicators(window)
        if "error" in ind:
            continue
        sig = ind.get("signal", "neutral")
        action = "long" if sig in ("buy", "strong buy") else ("short" if sig in ("sell", "strong sell") else None)
        if not action:
            continue
        signale.append({
            "i":      i,
            "action": action,
            "conf":   ind.get("confluenceScore", 5),
            "vola":   (ind.get("bollinger") or {}).get("width_pct", 0) or 0,
            "close":  candles[i].get("close"),
        })
    return signale


def trades_aus_signalen(candles: list, signale: list, sl_pct: float, tp_pct: float,
                        min_confluence: int = 6) -> list:
    """Erzeugt Trades aus vorberechneten Signalen für EIN SL/TP-Paar."""
    trades = []
    pos = None
    sig_by_i = {s["i"]: s for s in signale}

    for i in range(len(candles)):
        bar = candles[i]
        hi, lo = bar.get("high"), bar.get("low")
        if hi is None or lo is None:
            continue

        if pos:
            if pos["action"] == "long":
                sl_hit, tp_hit = lo <= pos["sl"], hi >= pos["tp"]
            else:
                sl_hit, tp_hit = hi >= pos["sl"], lo <= pos["tp"]
            ergebnis = None
            if sl_hit and tp_hit:
                ergebnis = "verloren"
            elif tp_hit:
                ergebnis = "gewonnen"
            elif sl_hit:
                ergebnis = "verloren"
            if ergebnis:
                trades.append({"action": pos["action"], "entry": pos["entry"],
                               "exit": pos["sl"] if ergebnis == "verloren" else pos["tp"],
                               "ergebnis": ergebnis, "confidence": pos["confidence"],
                               "vola": pos["vola"], "entry_i": pos["entry_i"], "exit_i": i})
                pos = None
                continue

        if pos is None and i in sig_by_i:
            s = sig_by_i[i]
            if s["conf"] < min_confluence or not s["close"]:
                continue
            entry = s["close"]
            if s["action"] == "long":
                sl, tp = entry * (1 - sl_pct / 100), entry * (1 + tp_pct / 100)
            else:
                sl, tp = entry * (1 + sl_pct / 100), entry * (1 - tp_pct / 100)
            pos = {"action": s["action"], "entry": entry, "sl": sl, "tp": tp,
                   "confidence": s["conf"], "vola": s["vola"], "entry_i": i}
    return trades


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

    # Haltedauer messen: zeigt, ob der Live-Timeout (MAX_TRADE_TAGE) lang genug
    # ist. Der Backtest selbst kennt keine Begrenzung - laufen Trades hier im
    # Schnitt länger als der Live-Timeout, werden live die Gewinner gekappt.
    dauern = sorted((t["exit_i"] - t["entry_i"]) for t in trade_seq)
    if dauern:
        median = dauern[len(dauern)//2]
        p90    = dauern[int(len(dauern)*0.9)] if len(dauern) > 1 else dauern[0]
        haltedauer = {"median_kerzen": median, "p90_kerzen": p90,
                      "max_kerzen": dauern[-1], "schnitt_kerzen": round(sum(dauern)/len(dauern), 1)}
    else:
        haltedauer = {}

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
        "haltedauer":     haltedauer,
        "ergebnisse":     ergebnisse,
        "bester_modus":   ergebnisse[0]["mm_modus"] if ergebnisse else None,
    }


# ── 4. Parameter-Optimierung: welche SL/TP/Schwelle funktioniert? ────────────
def optimiere_parameter(candles: list, startkapital: float = 1000.0,
                        mm_modus: str = "fixed_percent", min_trades: int = 25,
                        warmup: int = 50) -> dict:
    """
    Probiert systematisch SL/TP-Verhältnisse und Signal-Schwellen durch.

    WICHTIG - ehrliche Einordnung: Wenn man viele Kombinationen testet, findet
    man fast immer eine, die auf DIESEN Daten gut aussieht. Das ist oft blosse
    Kurvenanpassung und sagt wenig über die Zukunft. Deshalb:
      - Kombinationen mit zu wenigen Trades werden aussortiert
      - "benoetigte_wr" zeigt, welche Win-Rate das SL/TP-Verhaeltnis rechnerisch
        braucht, um bei null zu landen - erst ein Abstand nach oben ist ein Edge
      - Ein Ergebnis zaehlt erst, wenn es auf einem ZWEITEN Asset ebenfalls haelt
    """
    if not candles or len(candles) < warmup + 30:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0}"}

    signale = berechne_signale(candles, warmup)   # nur EINMAL berechnen
    if len(signale) < 5:
        return {"error": f"Zu wenige Signale in den Daten ({len(signale)})"}

    ergebnisse = []
    for min_conf in (5, 6, 7):
        for sl in (0.5, 1.0, 1.5, 2.0):
            for rr in (1.0, 1.5, 2.0, 3.0):      # TP als Vielfaches des SL
                tp = round(sl * rr, 2)
                seq = trades_aus_signalen(candles, signale, sl, tp, min_conf)
                if len(seq) < min_trades:
                    continue
                res = simuliere_mm(seq, mm_modus, startkapital, sl, tp)
                res.pop("equity_curve", None)
                benoetigt = round(100.0 / (1.0 + rr), 1)     # Break-even-Win-Rate
                res.update({
                    "sl_pct": sl, "tp_pct": tp, "rr": rr,
                    "min_confluence": min_conf,
                    "benoetigte_wr": benoetigt,
                    "vorsprung": round(res["win_rate"] - benoetigt, 1),
                })
                ergebnisse.append(res)

    if not ergebnisse:
        return {"error": f"Keine Kombination erreicht {min_trades} Trades - mehr Kerzen waehlen",
                "signale": len(signale)}

    # Nach Vorsprung sortieren (Edge), nicht nach ROI - ROI belohnt Zufallstreffer
    ergebnisse.sort(key=lambda r: (r["vorsprung"], r["profit_factor"] if isinstance(r["profit_factor"], (int, float)) else 0), reverse=True)

    profitabel = [r for r in ergebnisse if r["vorsprung"] > 0]
    return {
        "kerzen":        len(candles),
        "signale":       len(signale),
        "getestet":      len(ergebnisse),
        "profitabel":    len(profitabel),
        "mm_modus":      mm_modus,
        "beste":         ergebnisse[:8],
        "hinweis": ("Kein Parametersatz hat einen echten Vorsprung - die Signal-Logik "
                    "traegt auf diesem Asset nicht." if not profitabel else
                    "Vor dem Uebernehmen auf einem ZWEITEN Asset gegenpruefen - sonst ist es "
                    "nur Kurvenanpassung an diese Daten."),
    }
