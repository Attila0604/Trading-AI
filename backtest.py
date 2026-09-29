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
from demo_tracker import swap_kosten
from config import (ATR_SL_FAKTOR, ATR_TP_FAKTOR, ATR_SL_MIN_PCT, ATR_SL_MAX_PCT,
                    MAX_TRADE_TAGE)

log = logging.getLogger(__name__)


# Kerzen-Auflösung → Tage pro Kerze (Finanzierungskosten und Timeout)
TAGE_PRO_KERZE = {
    "MINUTE": 1/1440, "MINUTE_5": 5/1440, "MINUTE_15": 15/1440, "MINUTE_30": 30/1440,
    "HOUR": 1/24, "HOUR_4": 4/24, "DAY": 1.0, "WEEK": 7.0,
}


def atr_sl_tp(atr_pct) -> tuple:
    """SL/TP in % wie live (main.py): SL = Faktor × ATR, begrenzt; TP im festen Verhältnis."""
    if not atr_pct:
        return None, None
    sl = max(ATR_SL_MIN_PCT, min(ATR_SL_MAX_PCT, atr_pct * ATR_SL_FAKTOR))
    tp = sl * (ATR_TP_FAKTOR / ATR_SL_FAKTOR) if ATR_SL_FAKTOR > 0 else sl * 2
    return round(sl, 2), round(tp, 2)


def max_kerzen_fuer(resolution: str):
    """Live-Timeout (MAX_TRADE_TAGE) in Kerzen umgerechnet. None = kein Timeout."""
    tage = TAGE_PRO_KERZE.get(str(resolution).upper(), 1.0)
    if not MAX_TRADE_TAGE or MAX_TRADE_TAGE <= 0 or tage <= 0:
        return None
    return max(1, int(MAX_TRADE_TAGE / tage))


def _pruefe_position(pos: dict, bar: dict, i: int, max_kerzen) -> dict | None:
    """
    SL/TP/Timeout einer offenen Position gegen eine Kerze prüfen.
    Ergebnis in R (Vielfaches des Einsatzes): SL = -1, TP = +TP/SL,
    Timeout = Kursbewegung bis zum Schluss / SL, gedeckelt wie live.
    """
    hi, lo = bar.get("high"), bar.get("low")
    if pos["action"] == "long":
        sl_hit, tp_hit = lo <= pos["sl"], hi >= pos["tp"]
    else:
        sl_hit, tp_hit = hi >= pos["sl"], lo <= pos["tp"]
    rr = pos["tp_pct"] / pos["sl_pct"] if pos["sl_pct"] > 0 else 1.0
    ergebnis = exit_ = r = None
    if sl_hit:                          # beide in einer Kerze → konservativ SL
        ergebnis, exit_, r = "verloren", pos["sl"], -1.0
    elif tp_hit:
        ergebnis, exit_, r = "gewonnen", pos["tp"], rr
    elif max_kerzen and i - pos["entry_i"] >= max_kerzen and bar.get("close"):
        exit_ = bar["close"]
        move = (exit_ - pos["entry"]) / pos["entry"] * 100
        if pos["action"] == "short":
            move = -move
        r = max(-1.0, min(rr, move / pos["sl_pct"])) if pos["sl_pct"] > 0 else 0.0
        ergebnis = "gewonnen" if r > 0 else "verloren"
    if ergebnis is None:
        return None
    return {"action": pos["action"], "entry": pos["entry"], "exit": exit_,
            "ergebnis": ergebnis, "r": round(r, 4), "timeout": not (sl_hit or tp_hit),
            "sl_pct": pos["sl_pct"], "tp_pct": pos["tp_pct"],
            "confidence": pos["confidence"], "vola": pos["vola"],
            "entry_i": pos["entry_i"], "exit_i": i}


def _eroeffne(action: str, entry: float, sl_pct: float, tp_pct: float, conf, vola, i: int) -> dict:
    if action == "long":
        sl, tp = entry * (1 - sl_pct / 100), entry * (1 + tp_pct / 100)
    else:
        sl, tp = entry * (1 + sl_pct / 100), entry * (1 - tp_pct / 100)
    return {"action": action, "entry": entry, "sl": sl, "tp": tp, "sl_pct": sl_pct,
            "tp_pct": tp_pct, "confidence": conf, "vola": vola, "entry_i": i}


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
            "atr":    ind.get("atrPct"),
            "close":  candles[i].get("close"),
        })
    return signale


def trades_aus_signalen(candles: list, signale: list, sl_pct: float, tp_pct: float,
                        min_confluence: int = 6, max_kerzen=None) -> list:
    """Erzeugt Trades aus vorberechneten Signalen für EIN festes SL/TP-Paar."""
    trades = []
    pos = None
    sig_by_i = {s["i"]: s for s in signale}

    for i in range(len(candles)):
        bar = candles[i]
        if bar.get("high") is None or bar.get("low") is None:
            continue
        if pos:
            t = _pruefe_position(pos, bar, i, max_kerzen)
            if t:
                trades.append(t)
                pos = None
            continue            # keine Neueröffnung in derselben Kerze

        if i in sig_by_i:
            s = sig_by_i[i]
            if s["conf"] < min_confluence or not s["close"]:
                continue
            pos = _eroeffne(s["action"], s["close"], sl_pct, tp_pct, s["conf"], s["vola"], i)
    return trades


def generiere_trades(candles: list, sl_pct: float = 1.5, tp_pct: float = 3.0,
                     min_confluence: int = 6, warmup: int = 50,
                     atr_modus: bool = False, max_kerzen=None) -> list:
    """
    Läuft Kerze für Kerze durch und erzeugt abgeschlossene Trades.
    Signal aus den Indikatoren; SL/TP-Treffer via Kerzen-High/Low
    (gleiche konservative Logik wie im Live-Check).

    atr_modus=True: SL/TP je Trade aus dem ATR bei Eröffnung - wie live bei
    VOLA_ADAPTIV. max_kerzen: Live-Timeout, danach Schluss zum Schlusskurs.
    """
    trades = []
    pos = None

    for i in range(warmup, len(candles)):
        bar = candles[i]
        if bar.get("high") is None or bar.get("low") is None:
            continue

        if pos:
            t = _pruefe_position(pos, bar, i, max_kerzen)
            if t:
                trades.append(t)
                pos = None
            continue            # keine Neueröffnung in derselben Kerze

        window = candles[max(0, i - 199):i + 1]
        ind = calculate_all_indicators(window)
        if "error" in ind:
            continue
        sig  = ind.get("signal", "neutral")
        conf = ind.get("confluenceScore", 5)
        vola = (ind.get("bollinger") or {}).get("width_pct", 0) or 0
        action = ("long" if sig in ("buy", "strong buy")
                  else "short" if sig in ("sell", "strong sell") else None)
        entry = bar.get("close")
        if not action or conf < min_confluence or not entry:
            continue
        t_sl, t_tp = sl_pct, tp_pct
        if atr_modus:
            a_sl, a_tp = atr_sl_tp(ind.get("atrPct"))
            if a_sl:
                t_sl, t_tp = a_sl, a_tp
        pos = _eroeffne(action, entry, t_sl, t_tp, conf, vola, i)

    return trades


# ── 2. Ein MM-Modus über die feste Trade-Sequenz simulieren ──────────────────
def simuliere_mm(trade_seq: list, mm_modus: str = "fixed_percent",
                 startkapital: float = 1000.0, sl_pct: float = 1.5,
                 tp_pct: float = 3.0, params: dict = None,
                 asset: str = None, resolution: str = "DAY") -> dict:
    kapital  = startkapital
    equity   = [round(kapital, 2)]
    peak     = kapital
    max_dd   = 0.0
    wins = losses = 0
    brutto_gewinn = brutto_verlust = 0.0
    # Übernacht-Finanzierung: ohne sie zeigt der Backtest Ergebnisse, die es
    # so nie geben wird. Bei dünnen Vorteilen (PF um 1.0) entscheidet allein
    # diese Position über Plus oder Minus.
    tage_je_kerze = TAGE_PRO_KERZE.get(str(resolution).upper(), 1.0)
    swap_gesamt   = 0.0
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

        # Ergebnis in R (SL = -1, TP = +R:R, Timeout dazwischen); alte
        # Trade-Listen ohne "r" fallen auf das feste SL/TP-Verhältnis zurück
        t_sl = t.get("sl_pct", sl_pct)
        if "r" in t:
            r = t["r"]
        else:
            r = (tp_pct / sl_pct if sl_pct > 0 else 1.0) if t["ergebnis"] == "gewonnen" else -1.0
        pnl = einsatz * r
        if pnl > 0:
            wins += 1
            brutto_gewinn += pnl
        else:
            losses += 1
            brutto_verlust += -pnl

        # Haltedauer aus den Kerzen-Indizes → Finanzierungskosten
        kerzen = max(0, int(t.get("exit_i", 0)) - int(t.get("entry_i", 0)))
        swap   = swap_kosten(einsatz, t_sl, asset, kerzen * tage_je_kerze)
        pnl   -= swap
        swap_gesamt += swap

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
        "finanzierung":     round(swap_gesamt, 2),
        "roi_vor_kosten":   round((kapital + swap_gesamt - startkapital) / startkapital * 100, 2) if startkapital > 0 else 0,
        "equity_curve":     equity,
    }


# ── 3. Alle Modi über dieselbe Trade-Sequenz vergleichen ─────────────────────
def vergleiche_modi(candles: list, startkapital: float = 1000.0,
                    sl_pct: float = 1.5, tp_pct: float = 3.0,
                    min_confluence: int = 6, warmup: int = 50,
                    asset: str = None, resolution: str = "DAY",
                    atr_modus: bool = False) -> dict:
    if not candles or len(candles) < warmup + 10:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0} (min {warmup + 10})"}

    max_kerzen = max_kerzen_fuer(resolution)
    trade_seq = generiere_trades(candles, sl_pct, tp_pct, min_confluence, warmup,
                                 atr_modus=atr_modus, max_kerzen=max_kerzen)

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
        res = simuliere_mm(trade_seq, modus, startkapital, sl_pct, tp_pct,
                           asset=asset, resolution=resolution)
        res.pop("equity_curve", None)   # aus der Vergleichs-Tabelle raus (zu groß)
        ergebnisse.append(res)

    # nach ROI absteigend sortieren
    ergebnisse.sort(key=lambda r: r["roi_pct"], reverse=True)

    return {
        "kerzen":         len(candles),
        "signal_trades":  len(trade_seq),
        "parameter":      {"sl_pct": sl_pct, "tp_pct": tp_pct, "atr_modus": atr_modus,
                           "sl_tp_text": (f"ATR: SL {ATR_SL_FAKTOR:g}×, TP {ATR_TP_FAKTOR:g}× Tages-ATR"
                                          if atr_modus else f"SL {sl_pct}% / TP {tp_pct}%"),
                           "timeout_kerzen": max_kerzen,
                           "min_confluence": min_confluence, "startkapital": startkapital},
        "timeouts":       sum(1 for t in trade_seq if t.get("timeout")),
        "baseline_hinweis": ("Regelbasierte Baseline (RSI/MACD/EMA/BB, ohne LLM). "
                             "Alle Modi laufen über dieselben Trades - nur die Positionsgröße unterscheidet sich."),
        "haltedauer":     haltedauer,
        "ergebnisse":     ergebnisse,
        "bester_modus":   ergebnisse[0]["mm_modus"] if ergebnisse else None,
    }


# ── 4. Parameter-Optimierung: welche SL/TP/Schwelle funktioniert? ────────────
def optimiere_parameter(candles: list, startkapital: float = 1000.0,
                        mm_modus: str = "fixed_percent", min_trades: int = 25,
                        warmup: int = 50, asset: str = None,
                        resolution: str = "DAY") -> dict:
    """
    Probiert systematisch SL/TP-Verhältnisse und Signal-Schwellen durch.

    WICHTIG - ehrliche Einordnung: Wenn man viele Kombinationen testet, findet
    man fast immer eine, die auf DIESEN Daten gut aussieht. Das ist oft blosse
    Kurvenanpassung und sagt wenig über die Zukunft. Deshalb:
      - Kombinationen mit zu wenigen Trades werden aussortiert
      - "benoetigte_wr" zeigt, welche Win-Rate das SL/TP-Verhaeltnis rechnerisch
        braucht, um bei null zu landen - erst ein Abstand nach oben ist ein Edge
      - Ein Ergebnis zaehlt erst, wenn es auf einem ZWEITEN Asset ebenfalls haelt
      - Die Uebernacht-Finanzierung ist eingerechnet: bei duennen Vorteilen
        (PF um 1.0) entscheidet allein sie ueber Plus oder Minus
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
                seq = trades_aus_signalen(candles, signale, sl, tp, min_conf,
                                          max_kerzen=max_kerzen_fuer(resolution))
                if len(seq) < min_trades:
                    continue
                res = simuliere_mm(seq, mm_modus, startkapital, sl, tp,
                                   asset=asset, resolution=resolution)
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
