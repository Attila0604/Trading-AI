"""
demo_tracker.py
───────────────
Simuliertes Demo-Kapital Tracking System.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR       = os.getenv("DATA_DIR", "/app/data")
STARTKAPITAL   = float(os.getenv("DEMO_STARTKAPITAL", "1000"))
RISIKO_PROZENT = float(os.getenv("MAX_RISK_PCT", "2"))
SL_PROZENT     = float(os.getenv("STOP_LOSS_PCT", "1.5"))
TP_PROZENT     = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
DEMO_FILE      = Path(DATA_DIR) / "demo_kapital.json"


def _lade_daten() -> dict:
    if DEMO_FILE.exists():
        try:
            with open(DEMO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Demo-Daten laden Fehler: {e}")
    return _init_daten()


def _speichere_daten(daten: dict):
    try:
        DEMO_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DEMO_FILE, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Demo-Daten speichern Fehler: {e}")


def _init_daten() -> dict:
    daten = {
        "startkapital":      STARTKAPITAL,
        "aktuelles_kapital": STARTKAPITAL,
        "erstellt_am":       datetime.now().isoformat(),
        "trades":            [],
        "tages_snapshots":   [],
        "statistik": {
            "gesamt_trades":       0,
            "gewonnen":            0,
            "verloren":            0,
            "offen":               0,
            "gesamt_pnl":          0.0,
            "beste_trade":         0.0,
            "schlechtester_trade": 0.0,
            "win_rate":            0.0,
            "roi":                 0.0,
            "max_drawdown":        0.0,
        }
    }
    _speichere_daten(daten)
    return daten


def _validiere_prozent(wert, default: float, max_wert: float = 20.0) -> float:
    """
    Stellt sicher dass ein Wert ein gültiger Prozentsatz ist.
    Falls der Wert zu groß ist (z.B. ein Preis statt Prozent) → default verwenden.
    """
    try:
        wert = float(wert)
        if wert <= 0 or wert > max_wert:
            return default
        return wert
    except Exception:
        return default


def signal_oeffnen(signal: dict) -> dict:
    """Öffnet einen neuen Demo-Trade basierend auf einem KI-Signal."""
    daten   = _lade_daten()
    kapital = daten["aktuelles_kapital"]

    # Einsatz = Risiko % des Kapitals
    einsatz = round(kapital * RISIKO_PROZENT / 100, 2)
    einsatz = max(1.0, min(einsatz, kapital))

    # ── FIX: SL/TP als Prozentsatz validieren ────────────────────────────
    # stopLoss/takeProfit aus Signal können Preise sein (z.B. 43200 für BTC)
    # Wir nehmen nur Werte die sinnvolle Prozentsätze sind (0 < x <= 20)
    sl_pct = _validiere_prozent(signal.get("stopLoss"),  SL_PROZENT)
    tp_pct = _validiere_prozent(signal.get("takeProfit"), TP_PROZENT)
    # ─────────────────────────────────────────────────────────────────────

    sl_absolut = round(einsatz * sl_pct / 100, 2)
    tp_absolut = round(einsatz * tp_pct / 100, 2)

    trade = {
        "id":             f"T{len(daten['trades']) + 1:04d}",
        "asset":          signal.get("asset", ""),
        "action":         signal.get("action", ""),
        "direction":      signal.get("direction", ""),
        "konfidenz":      signal.get("confidence", 0),
        "einsatz":        einsatz,
        "sl_pct":         sl_pct,
        "tp_pct":         tp_pct,
        "sl_absolut":     sl_absolut,
        "tp_absolut":     tp_absolut,
        "potenz_gewinn":  tp_absolut,
        "potenz_verlust": sl_absolut,
        "rr":             round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0,
        "status":         "offen",
        "geoeffnet_am":   datetime.now().isoformat(),
        "geschlossen_am": None,
        "pnl":            0.0,
        "zusammenfassung": signal.get("summary", ""),
        "strategie":      signal.get("strategyUsed", ""),
    }

    daten["trades"].append(trade)
    daten["statistik"]["gesamt_trades"] += 1
    daten["statistik"]["offen"]         += 1

    _speichere_daten(daten)
    log.info(f"✅ Demo-Trade: {trade['id']} | {trade['asset']} {trade['action'].upper()} | €{einsatz} | SL:{sl_pct}% TP:{tp_pct}%")
    return trade


def trade_schliessen(trade_id: str, ergebnis: str, pnl_override: float = None) -> dict:
    """Schließt einen offenen Demo-Trade."""
    daten = _lade_daten()
    trade = next((t for t in daten["trades"] if t["id"] == trade_id), None)

    if not trade:
        log.warning(f"Trade {trade_id} nicht gefunden")
        return {}
    if trade["status"] != "offen":
        log.warning(f"Trade {trade_id} ist nicht offen")
        return trade

    if pnl_override is not None:
        pnl = pnl_override
    elif ergebnis == "gewonnen":
        pnl = trade["tp_absolut"]
    elif ergebnis == "verloren":
        pnl = -trade["sl_absolut"]
    else:
        pnl = 0.0

    trade["status"]         = ergebnis
    trade["pnl"]            = round(pnl, 2)
    trade["geschlossen_am"] = datetime.now().isoformat()

    daten["aktuelles_kapital"] = round(daten["aktuelles_kapital"] + pnl, 2)

    stats = daten["statistik"]
    stats["offen"]      = max(0, stats["offen"] - 1)
    stats["gesamt_pnl"] = round(stats["gesamt_pnl"] + pnl, 2)

    if ergebnis == "gewonnen":
        stats["gewonnen"]    += 1
        stats["beste_trade"]  = max(stats["beste_trade"], pnl)
    elif ergebnis == "verloren":
        stats["verloren"]            += 1
        stats["schlechtester_trade"]  = min(stats["schlechtester_trade"], pnl)

    abgeschlossen   = stats["gewonnen"] + stats["verloren"]
    stats["win_rate"] = round(stats["gewonnen"] / abgeschlossen * 100, 1) if abgeschlossen > 0 else 0
    stats["roi"]      = round((daten["aktuelles_kapital"] - daten["startkapital"]) / daten["startkapital"] * 100, 2)

    # Drawdown
    peak = daten["startkapital"]
    laufend = daten["startkapital"]
    for t in daten["trades"]:
        if t["status"] in ("gewonnen", "verloren", "breakeven"):
            laufend = round(laufend + t["pnl"], 2)
            peak    = max(peak, laufend)
    drawdown = round((peak - daten["aktuelles_kapital"]) / peak * 100, 2) if peak > 0 else 0
    stats["max_drawdown"] = max(stats["max_drawdown"], drawdown)

    _speichere_daten(daten)
    log.info(f"{'✅' if ergebnis=='gewonnen' else '❌'} Trade {trade_id} | {ergebnis.upper()} | P&L: {'+' if pnl>=0 else ''}€{pnl:.2f} | Kapital: €{daten['aktuelles_kapital']:.2f}")
    return trade


def tages_snapshot():
    """Speichert täglichen Kapital-Snapshot."""
    daten = _lade_daten()
    heute = datetime.now().strftime("%d.%m.%Y")

    if daten["tages_snapshots"] and daten["tages_snapshots"][-1]["datum"] == heute:
        daten["tages_snapshots"][-1]["kapital"] = daten["aktuelles_kapital"]
    else:
        prev = daten["tages_snapshots"][-1]["kapital"] if daten["tages_snapshots"] else daten["startkapital"]
        daten["tages_snapshots"].append({
            "datum":   heute,
            "kapital": daten["aktuelles_kapital"],
            "pnl":     round(daten["aktuelles_kapital"] - prev, 2),
        })

    if len(daten["tages_snapshots"]) > 365:
        daten["tages_snapshots"] = daten["tages_snapshots"][-365:]

    _speichere_daten(daten)
    log.info(f"📊 Snapshot: {heute} | €{daten['aktuelles_kapital']:.2f}")


def get_offene_trades() -> list:
    daten = _lade_daten()
    return [t for t in daten["trades"] if t["status"] == "offen"]


def get_statistik() -> dict:
    daten = _lade_daten()
    return {
        "startkapital":      daten["startkapital"],
        "aktuelles_kapital": daten["aktuelles_kapital"],
        "pnl_gesamt":        round(daten["aktuelles_kapital"] - daten["startkapital"], 2),
        "erstellt_am":       daten["erstellt_am"],
        "statistik":         daten["statistik"],
        "tages_snapshots":   daten["tages_snapshots"][-30:],
        "offene_trades":     get_offene_trades(),
        "letzte_trades":     sorted(
            [t for t in daten["trades"] if t["status"] != "offen"],
            key=lambda x: x["geschlossen_am"] or "",
            reverse=True
        )[:20],
    }


def generiere_tages_report() -> str:
    stats   = get_statistik()
    kapital = stats["aktuelles_kapital"]
    start   = stats["startkapital"]
    pnl     = stats["pnl_gesamt"]
    roi     = stats["statistik"]["roi"]
    wr      = stats["statistik"]["win_rate"]
    offen   = len(stats["offene_trades"])
    gewon   = stats["statistik"]["gewonnen"]
    verl    = stats["statistik"]["verloren"]
    snapshots = stats["tages_snapshots"]
    tages_pnl = snapshots[-1]["pnl"] if snapshots else 0

    return (
        f"📊 *TRADING DEMO - TAGESREPORT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kapital: *€{kapital:.2f}*\n"
        f"{'📈' if pnl>=0 else '📉'} Gesamt P&L: *{'+' if pnl>=0 else ''}€{pnl:.2f}* ({'+' if roi>=0 else ''}{roi:.1f}%)\n"
        f"{'✅' if tages_pnl>=0 else '❌'} Heute: *{'+' if tages_pnl>=0 else ''}€{tages_pnl:.2f}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Win Rate: *{wr:.1f}%*\n"
        f"✅ Gewonnen: *{gewon}* | ❌ Verloren: *{verl}*\n"
        f"🔄 Offen: *{offen}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Startkapital: €{start:.2f}\n"
        f"🤖 _Trading Multi-Agent v3.0_"
    )
