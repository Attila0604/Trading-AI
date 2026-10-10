"""
backtest.py — Regelbasierter Backtest über historische Kerzen.
────────────────────────────────────────────────────────────────
Signalquelle = indicators.py (RSI/MACD/EMA/BB), KEINE LLM-Calls.
Damit ist der Backtest deterministisch UND dient als Baseline:
Schlägt das Live-System diese reine Regel-Logik nicht, bringt die
LLM-Schicht keinen echten Mehrwert.

Ablauf:
  1) generiere_trades()  – erzeugt die Trade-Sequenz EINMAL aus den Kerzen
     (kapitalunabhängig: Entry/Exit/Ergebnis in R hängen nur am Preis).
  2) simuliere_mm()      – legt einen Money-Management-Modus über genau
     diese Sequenz und rechnet Kapitalkurve, ROI, Drawdown, Profit-Faktor.
  3) vergleiche_modi()   – läuft alle 6 Modi über dieselben Trades → Tabelle.

Seit 10.10.2026 dasselbe Trade-Management wie live (handelsregeln.py):
ATR-Stops, nachgezogener Stop, Timeout nach MAX_TRADE_TAGE, Einstieg Long
zum Ask / Short zum Bid (sofern die Kerzen einen Spread liefern). Vorher
lief der Backtest mit festem SL/TP bis zum Treffer und hat damit eine
andere Strategie gemessen als die, die live gehandelt wurde.

Nicht abgebildet: Portfolio-Limits über mehrere Assets (Exposure,
gleiche Richtung), KI-Veto und der Einstieg um 07:00 in die noch offene
Tageskerze (der Backtest steigt zum Tagesschluss ein).
"""

import logging
from indicators import calculate_all_indicators
from money_management import berechne_einsatz, MODI
from demo_tracker import swap_kosten
from config import VOLA_ADAPTIV
import handelsregeln as hr

log = logging.getLogger(__name__)

# Kerzen-Auflösung → Tage pro Kerze (Finanzierung, Stop-Nachzug, Timeout)
TAGE_PRO_KERZE = {
    "MINUTE": 1/1440, "MINUTE_5": 5/1440, "MINUTE_15": 15/1440, "MINUTE_30": 30/1440,
    "HOUR": 1/24, "HOUR_4": 4/24, "DAY": 1.0, "WEEK": 7.0,
}


def _stops_default(stops):
    return stops or ("atr" if VOLA_ADAPTIV else "fest")


# ── 1. Signal-/Trade-Sequenz aus Kerzen (einmalig, kapitalunabhängig) ────────
def berechne_signale(candles: list, warmup: int = 50) -> list:
    """
    Berechnet die Indikator-Signale EINMAL pro Kerze.
    Teuerster Teil des Backtests - wird für alle SL/TP-Kombinationen
    wiederverwendet, sonst dauert eine Optimierung ewig.
    Fenster = letzte 200 Kerzen, wie live (agents.regel_signal).
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
            "i":       i,
            "action":  action,
            "conf":    ind.get("confluenceScore", 5),
            "vola":    (ind.get("bollinger") or {}).get("width_pct", 0) or 0,
            "atr_pct": ind.get("atrPct"),
            "close":   candles[i].get("close"),
        })
    return signale


def trades_aus_signalen(candles: list, signale: list, sl_pct: float = 1.5, tp_pct: float = 3.0,
                        min_confluence: int = 6, stops: str = None,
                        sl_faktor: float = None, tp_faktor: float = None,
                        resolution: str = "DAY", management: bool = True) -> list:
    """
    Erzeugt Trades aus vorberechneten Signalen.

    stops      : "atr"  = SL/TP je Trade aus dem Tages-ATR (wie live),
                 "fest" = sl_pct/tp_pct für alle Trades
    management : nachgezogener Stop + Timeout wie live (False = nur SL/TP)

    Jeder Trade trägt sein Ergebnis in R ("r") und den Ausstiegsgrund
    ("grund": tp | sl | be | timeout).
    """
    stops = _stops_default(stops)
    tage_je_kerze = TAGE_PRO_KERZE.get(str(resolution).upper(), 1.0)
    trades = []
    pos = None
    sig_by_i = {s["i"]: s for s in signale}

    for i in range(len(candles)):
        bar = candles[i]
        hi, lo, close = bar.get("high"), bar.get("low"), bar.get("close")
        if hi is None or lo is None:
            continue

        if pos:
            a = pos["action"]
            alter = (i - pos["entry_i"]) * tage_je_kerze
            stop = pos["be_stop"] if pos["be_stop"] is not None else pos["sl"]
            treffer = hr.kerze_trifft(a, hi, lo, stop, pos["tp"])
            ausgang = None
            if treffer == "gewonnen":
                ausgang = ("gewonnen", pos["tp_pct"] / pos["sl_pct"], pos["tp"], "tp")
            elif treffer == "verloren" and pos["be_stop"] is not None:
                r = hr.r_aus_preis(pos["entry"], stop, a, pos["sl_pct"], pos["tp_pct"])
                ausgang = ("gewonnen" if r > 0 else "breakeven", r, stop, "be")
            elif treffer == "verloren":
                ausgang = ("verloren", -1.0, stop, "sl")
            elif management and close and hr.timeout_erreicht(alter):
                r = hr.r_aus_preis(pos["entry"], close, a, pos["sl_pct"], pos["tp_pct"])
                ausgang = ("gewonnen" if r > 0 else "verloren", r, close, "timeout")
            elif management and close and pos["be_stop"] is None:
                unreal = hr.r_aus_preis(pos["entry"], close, a, pos["sl_pct"], pos["tp_pct"])
                if hr.be_aktivieren(alter, unreal):
                    # wirkt wie live erst ab der nächsten Kerze
                    pos["be_stop"] = hr.be_stop_preis(pos["entry"], a, pos["sl_pct"])

            if ausgang:
                ergebnis, r, exit_preis, grund = ausgang
                trades.append({"action": a, "entry": pos["entry"], "exit": exit_preis,
                               "ergebnis": ergebnis, "r": round(r, 4), "grund": grund,
                               "sl_pct": pos["sl_pct"], "tp_pct": pos["tp_pct"],
                               "confidence": pos["confidence"], "vola": pos["vola"],
                               "entry_i": pos["entry_i"], "exit_i": i})
                pos = None
                continue   # keine Neueröffnung in derselben Kerze

        if pos is None and i in sig_by_i:
            s = sig_by_i[i]
            if s["conf"] < min_confluence or not s["close"]:
                continue
            if stops == "atr":
                if not s.get("atr_pct"):
                    continue
                t_sl, t_tp, _ = hr.sl_tp_prozent(s["atr_pct"], sl_pct, tp_pct,
                                                 sl_faktor, tp_faktor, vola_adaptiv=True)
            else:
                t_sl, t_tp = float(sl_pct), float(tp_pct)
            # Einstieg wie live: Long zum Ask, Short zum Bid (Spread aus der Kerze)
            halb = float(bar.get("spread") or 0) / 2
            entry = s["close"] + halb if s["action"] == "long" else s["close"] - halb
            sl, tp = hr.level(entry, s["action"], t_sl, t_tp)
            pos = {"action": s["action"], "entry": entry, "sl": sl, "tp": tp, "be_stop": None,
                   "sl_pct": t_sl, "tp_pct": t_tp,
                   "confidence": s["conf"], "vola": s["vola"], "entry_i": i}
    return trades


def generiere_trades(candles: list, sl_pct: float = 1.5, tp_pct: float = 3.0,
                     min_confluence: int = 6, warmup: int = 50, **kwargs) -> list:
    """Signale berechnen und daraus die Trade-Folge erzeugen (siehe trades_aus_signalen)."""
    return trades_aus_signalen(candles, berechne_signale(candles, warmup),
                               sl_pct, tp_pct, min_confluence, **kwargs)


# ── 2. Ein MM-Modus über die feste Trade-Sequenz simulieren ──────────────────
def simuliere_mm(trade_seq: list, mm_modus: str = "fixed_percent",
                 startkapital: float = 1000.0, sl_pct: float = 1.5,
                 tp_pct: float = 3.0, params: dict = None,
                 asset: str = None, resolution: str = "DAY") -> dict:
    """
    sl_pct/tp_pct gelten nur für Trades ohne eigene Werte (alte Sequenzen).
    Ergebnis pro Trade = Einsatz x R, abzüglich Übernacht-Finanzierung.
    """
    kapital  = startkapital
    equity   = [round(kapital, 2)]
    peak     = kapital
    max_dd   = 0.0
    wins = losses = be = 0
    brutto_gewinn = brutto_verlust = 0.0
    # Übernacht-Finanzierung: ohne sie zeigt der Backtest Ergebnisse, die es
    # so nie geben wird. Bei dünnen Vorteilen (PF um 1.0) entscheidet allein
    # diese Position über Plus oder Minus.
    tage_je_kerze = TAGE_PRO_KERZE.get(str(resolution).upper(), 1.0)
    swap_gesamt   = 0.0
    verlauf  = []   # letzte Ergebnisse für Kelly/Anti-Martingale

    for t in trade_seq:
        t_sl = float(t.get("sl_pct") or sl_pct)
        t_tp = float(t.get("tp_pct") or tp_pct)
        r = t.get("r")
        if r is None:
            r = (t_tp / t_sl if t_sl > 0 else 1.0) if t["ergebnis"] == "gewonnen" else -1.0

        abgeschlossen = wins + losses
        wr = (wins / abgeschlossen * 100) if abgeschlossen else 0.0
        mm = berechne_einsatz(
            mm_modus, kapital,
            ctx={
                "confidence":           t["confidence"] * 10,   # 1-10 → ~10-100
                "win_rate":             wr,
                "gesamt_abgeschlossen": abgeschlossen,
                "sl_pct":               t_sl,
                "tp_pct":               t_tp,
                "volatility_pct":       t["vola"],
                "letzte_trades":        [{"Status": x} for x in reversed(verlauf[-10:])],
            },
            params=params,
        )
        einsatz = mm["einsatz"]
        pnl = einsatz * r

        # Haltedauer aus den Kerzen-Indizes → Finanzierungskosten
        kerzen = max(0, int(t.get("exit_i", 0)) - int(t.get("entry_i", 0)))
        swap   = swap_kosten(einsatz, t_sl, asset, kerzen * tage_je_kerze)
        pnl   -= swap
        swap_gesamt += swap

        # Wie live: frisst die Finanzierung den Gewinn auf, zählt es als Verlust
        if pnl > 0:
            wins += 1; brutto_gewinn += pnl; verlauf.append("gewonnen")
        elif pnl < 0:
            losses += 1; brutto_verlust += -pnl; verlauf.append("verloren")
        else:
            be += 1; verlauf.append("breakeven")

        kapital += pnl
        equity.append(round(kapital, 2))
        peak = max(peak, kapital)
        dd = (peak - kapital) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

        if kapital <= 0:            # Pleite → Abbruch
            log.warning(f"[Backtest] {mm_modus}: Kapital ≤ 0 nach {wins+losses+be} Trades")
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
        "trades":           wins + losses + be,
        "gewonnen":         wins,
        "verloren":         losses,
        "breakeven":        be,
        "win_rate":         round(wins / abgeschlossen * 100, 1) if abgeschlossen else 0.0,
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor":    profit_factor if profit_factor != float("inf") else "∞",
        "finanzierung":     round(swap_gesamt, 2),
        "roi_vor_kosten":   round((kapital + swap_gesamt - startkapital) / startkapital * 100, 2) if startkapital > 0 else 0,
        "equity_curve":     equity,
    }


def _ausstiege(trade_seq: list) -> dict:
    """Wie enden die Trades? tp / sl / be / timeout - zeigt, ob z.B. der Timeout Gewinner kappt."""
    zaehler = {}
    for t in trade_seq:
        g = t.get("grund", "?")
        zaehler[g] = zaehler.get(g, 0) + 1
    return zaehler


def _schnitt_r(trade_seq: list):
    rs = [t["r"] for t in trade_seq if t.get("r") is not None]
    return round(sum(rs) / len(rs), 3) if rs else None


def _stops_text(stops, sl_pct, tp_pct, sl_faktor=None, tp_faktor=None) -> str:
    if stops == "atr":
        sl_f = hr.ATR_SL_FAKTOR if sl_faktor is None else sl_faktor
        tp_f = hr.ATR_TP_FAKTOR if tp_faktor is None else tp_faktor
        return f"SL {sl_f:g}×ATR / TP {tp_f:g}×ATR"
    return f"SL {sl_pct:g}% / TP {tp_pct:g}%"


# ── 3. Alle Modi über dieselbe Trade-Sequenz vergleichen ─────────────────────
def vergleiche_modi(candles: list, startkapital: float = 1000.0,
                    sl_pct: float = 1.5, tp_pct: float = 3.0,
                    min_confluence: int = 6, warmup: int = 50,
                    asset: str = None, resolution: str = "DAY",
                    stops: str = None, management: bool = True,
                    params: dict = None) -> dict:
    if not candles or len(candles) < warmup + 10:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0} (min {warmup + 10})"}

    stops = _stops_default(stops)
    trade_seq = generiere_trades(candles, sl_pct, tp_pct, min_confluence, warmup,
                                 stops=stops, resolution=resolution, management=management)

    # Haltedauer messen: zeigt, ob der Live-Timeout (MAX_TRADE_TAGE) lang genug ist.
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
        res = simuliere_mm(trade_seq, modus, startkapital, sl_pct, tp_pct, params=params,
                           asset=asset, resolution=resolution)
        res.pop("equity_curve", None)   # aus der Vergleichs-Tabelle raus (zu groß)
        ergebnisse.append(res)

    # nach ROI absteigend sortieren
    ergebnisse.sort(key=lambda r: r["roi_pct"], reverse=True)

    return {
        "kerzen":         len(candles),
        "signal_trades":  len(trade_seq),
        "parameter":      {"sl_pct": sl_pct, "tp_pct": tp_pct, "stops": stops,
                           "stops_text": _stops_text(stops, sl_pct, tp_pct),
                           "management": management,
                           "min_confluence": min_confluence, "startkapital": startkapital},
        "baseline_hinweis": ("Regelbasierte Baseline (RSI/MACD/EMA/BB, ohne LLM) mit demselben "
                             "Trade-Management wie live. Alle Modi laufen über dieselben Trades - "
                             "nur die Positionsgröße unterscheidet sich."),
        "haltedauer":     haltedauer,
        "ausstiege":      _ausstiege(trade_seq),
        "schnitt_r":      _schnitt_r(trade_seq),
        "ergebnisse":     ergebnisse,
        "bester_modus":   ergebnisse[0]["mm_modus"] if ergebnisse else None,
    }


# ── 4. Parameter-Optimierung: welche SL/TP/Schwelle funktioniert? ────────────
def optimiere_parameter(candles: list, startkapital: float = 1000.0,
                        mm_modus: str = "fixed_percent", min_trades: int = 25,
                        warmup: int = 50, asset: str = None,
                        resolution: str = "DAY", stops: str = None,
                        params: dict = None) -> dict:
    """
    Probiert systematisch Stop-Weiten, R:R und Signal-Schwellen durch.
    Bei ATR-Stops werden ATR-Faktoren variiert, sonst feste Prozentsätze.

    WICHTIG - ehrliche Einordnung: Wenn man viele Kombinationen testet, findet
    man fast immer eine, die auf DIESEN Daten gut aussieht. Das ist oft blosse
    Kurvenanpassung und sagt wenig über die Zukunft. Deshalb:
      - Kombinationen mit zu wenigen Trades werden aussortiert
      - Sortiert wird nach dem Ø-Ergebnis in R pro Trade (Erwartungswert),
        nicht nach ROI - ROI belohnt Zufallstreffer
      - "benoetigte_wr" zeigt die Break-even-Win-Rate bei reinen SL/TP-Ausgängen
        (durch Stop-Nachzug und Timeout nur noch ein Richtwert)
      - Ein Ergebnis zaehlt erst, wenn es auf einem ZWEITEN Asset ebenfalls haelt
      - Die Uebernacht-Finanzierung ist im ROI eingerechnet
    """
    if not candles or len(candles) < warmup + 30:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0}"}

    stops = _stops_default(stops)
    signale = berechne_signale(candles, warmup)   # nur EINMAL berechnen
    if len(signale) < 5:
        return {"error": f"Zu wenige Signale in den Daten ({len(signale)})"}

    if stops == "atr":
        stop_varianten = [(None, f) for f in (2.0, 3.0, 4.0)]          # (sl_pct, sl_faktor)
    else:
        stop_varianten = [(sl, None) for sl in (0.5, 1.0, 1.5, 2.0)]

    ergebnisse = []
    for min_conf in (6, 7, 8):
        for sl, sl_f in stop_varianten:
            for rr in (1.0, 1.5, 2.0, 3.0):      # TP als Vielfaches des SL
                if stops == "atr":
                    seq = trades_aus_signalen(candles, signale, 1.5, 3.0, min_conf, stops="atr",
                                              sl_faktor=sl_f, tp_faktor=sl_f * rr,
                                              resolution=resolution)
                else:
                    seq = trades_aus_signalen(candles, signale, sl, round(sl * rr, 2), min_conf,
                                              stops="fest", resolution=resolution)
                if len(seq) < min_trades:
                    continue
                res = simuliere_mm(seq, mm_modus, startkapital, params=params,
                                   asset=asset, resolution=resolution)
                res.pop("equity_curve", None)
                benoetigt = round(100.0 / (1.0 + rr), 1)     # Break-even-Win-Rate (reine SL/TP)
                res.update({
                    "sl_pct": sl, "tp_pct": round(sl * rr, 2) if sl else None,
                    "atr_sl_faktor": sl_f, "atr_tp_faktor": round(sl_f * rr, 2) if sl_f else None,
                    "stops_text": _stops_text(stops, sl, round(sl * rr, 2) if sl else None,
                                              sl_f, sl_f * rr if sl_f else None),
                    "rr": rr, "min_confluence": min_conf,
                    "benoetigte_wr": benoetigt,
                    "vorsprung": round(res["win_rate"] - benoetigt, 1),
                    "schnitt_r": _schnitt_r(seq),
                    "ausstiege": _ausstiege(seq),
                })
                ergebnisse.append(res)

    if not ergebnisse:
        return {"error": f"Keine Kombination erreicht {min_trades} Trades - mehr Kerzen waehlen",
                "signale": len(signale)}

    ergebnisse.sort(key=lambda r: (r["schnitt_r"] if r["schnitt_r"] is not None else -9, r["roi_pct"]),
                    reverse=True)

    profitabel = [r for r in ergebnisse if (r["schnitt_r"] or 0) > 0 and r["roi_pct"] > 0]
    return {
        "kerzen":        len(candles),
        "signale":       len(signale),
        "getestet":      len(ergebnisse),
        "profitabel":    len(profitabel),
        "mm_modus":      mm_modus,
        "stops":         stops,
        "beste":         ergebnisse[:8],
        "hinweis": ("Kein Parametersatz hat nach Kosten einen positiven Erwartungswert - die "
                    "Signal-Logik traegt auf diesem Asset nicht." if not profitabel else
                    "Vor dem Uebernehmen auf einem ZWEITEN Asset gegenpruefen - sonst ist es "
                    "nur Kurvenanpassung an diese Daten."),
    }
