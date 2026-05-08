"""
demo_tracker.py (v2 - Excel-basiert)
────────────────────────────────────
Liest/schreibt Demo-Kapital direkt aus Excel statt JSON.
"""

import os
import logging
import pandas as pd
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR       = os.getenv("DATA_DIR", "/app/data")
STARTKAPITAL   = float(os.getenv("DEMO_STARTKAPITAL", "1000"))
RISIKO_PROZENT = float(os.getenv("MAX_RISK_PCT", "5"))
SL_PROZENT     = float(os.getenv("STOP_LOSS_PCT", "1.0"))
TP_PROZENT     = float(os.getenv("TAKE_PROFIT_PCT", "2.0"))
EXCEL_FILE     = Path(DATA_DIR) / "Trading_Tracker.xlsx"
SHEET_NAME     = "Demo-Kapital"

os.makedirs(DATA_DIR, exist_ok=True)


def _lade_excel() -> pd.DataFrame:
    """Lädt Demo-Kapital Sheet aus Excel."""
    if not EXCEL_FILE.exists():
        log.warning(f"Excel nicht gefunden: {EXCEL_FILE}")
        return pd.DataFrame()
    
    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME)
        log.info(f"✅ Excel geladen: {len(df)} Trades")
        return df
    except Exception as e:
        log.error(f"Excel lesen Fehler: {e}")
        return pd.DataFrame()


def _speichere_excel(df: pd.DataFrame):
    """Speichert Demo-Kapital Sheet in Excel."""
    if df.empty:
        log.warning("Leerer DataFrame - nicht speichern")
        return
    
    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=SHEET_NAME, index=False)
        log.info(f"✅ Excel gespeichert: {len(df)} Trades")
    except Exception as e:
        log.error(f"Excel speichern Fehler: {e}")


def _validiere_prozent(wert, default: float, max_wert: float = 20.0) -> float:
    """Stellt sicher dass ein Wert ein gültiger Prozentsatz ist."""
    try:
        wert = float(wert)
        if wert <= 0 or wert > max_wert:
            return default
        return wert
    except Exception:
        return default


def signal_oeffnen(signal: dict) -> dict:
    """Öffnet einen neuen Demo-Trade und speichert in Excel."""
    df = _lade_excel()
    
    # Berechne Einsatz basierend auf aktuellem Kapital
    stats = get_statistik()
    kapital = stats["aktuelles_kapital"]
    einsatz = round(kapital * RISIKO_PROZENT / 100, 2)
    einsatz = max(1.0, min(einsatz, kapital))
    
    sl_pct = _validiere_prozent(signal.get("stopLoss"), SL_PROZENT)
    tp_pct = _validiere_prozent(signal.get("takeProfit"), TP_PROZENT)
    
    sl_absolut = round(einsatz * sl_pct / 100, 2)
    tp_absolut = round(einsatz * tp_pct / 100, 2)
    
    # Trade ID
    trade_id = f"T{len(df) + 1:04d}"
    
    # Neue Zeile
    neue_zeile = {
        "Datum": datetime.now().strftime("%d.%m.%Y"),
        "Uhrzeit": datetime.now().strftime("%H:%M:%S"),
        "ID": trade_id,
        "Asset": signal.get("asset", ""),
        "Action": signal.get("action", "").upper(),
        "Richtung": signal.get("direction", "").upper(),
        "Konfidenz": int(signal.get("confidence", 0)),
        "Einsatz": einsatz,
        "SL %": sl_pct,
        "TP %": tp_pct,
        "SL Absolut": sl_absolut,
        "TP Absolut": tp_absolut,
        "R:R": round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0,
        "Entry-Price": float(signal.get("entry_price", 0)),
        "Aktuell": 0.0,
        "P&L": 0.0,
        "Status": "offen",
        "Geöffnet am": datetime.now().isoformat(),
        "Geschlossen am": "",
        "Zusammenfassung": signal.get("summary", ""),
        "Score": signal.get("sessionScore", 0),
        "Strategie": signal.get("strategyUsed", ""),
    }
    
    # Füge neue Zeile hinzu
    df = pd.concat([df, pd.DataFrame([neue_zeile])], ignore_index=True)
    
    # Speichere Excel
    _speichere_excel(df)
    
    log.info(f"✅ Demo-Trade: {trade_id} | {neue_zeile['Asset']} {neue_zeile['Action']} | €{einsatz} | SL:{sl_pct}% TP:{tp_pct}% | Entry:{neue_zeile['Entry-Price']}")
    
    return neue_zeile


def trade_schliessen(trade_id: str, ergebnis: str, pnl_override: float = None) -> dict:
    """Schließt einen offenen Demo-Trade."""
    df = _lade_excel()
    
    if df.empty or "ID" not in df.columns:
        log.warning(f"Trade {trade_id} nicht gefunden (Excel leer)")
        return {}
    
    # Finde Trade
    mask = df["ID"] == trade_id
    if not mask.any():
        log.warning(f"Trade {trade_id} nicht gefunden")
        return {}
    
    idx = df[mask].index[0]
    trade = df.loc[idx].to_dict()
    
    if trade.get("Status") != "offen":
        log.warning(f"Trade {trade_id} ist nicht offen")
        return trade
    
    # Berechne P&L
    if pnl_override is not None:
        pnl = pnl_override
    elif ergebnis == "gewonnen":
        pnl = trade.get("TP Absolut", 0)
    elif ergebnis == "verloren":
        pnl = -trade.get("SL Absolut", 0)
    else:
        pnl = 0.0
    
    # Update Trade
    df.loc[idx, "Status"] = ergebnis
    df.loc[idx, "P&L"] = round(pnl, 2)
    df.loc[idx, "Geschlossen am"] = datetime.now().isoformat()
    
    # Speichere Excel
    _speichere_excel(df)
    
    log.info(f"{'✅' if ergebnis == 'gewonnen' else '❌'} Trade {trade_id} | {ergebnis.upper()} | P&L: {'+' if pnl >= 0 else ''}€{pnl:.2f}")
    
    return df.loc[idx].to_dict()


def get_offene_trades() -> list:
    """Gibt alle offenen Trades zurück."""
    df = _lade_excel()
    if df.empty or "Status" not in df.columns:
        return []
    
    offene = df[df["Status"] == "offen"]
    return offene.to_dict('records')


def get_statistik() -> dict:
    """Liest Statistik direkt aus Excel."""
    df = _lade_excel()
    
    if df.empty:
        # Fallback wenn Excel leer
        return {
            "startkapital": STARTKAPITAL,
            "aktuelles_kapital": STARTKAPITAL,
            "pnl_gesamt": 0.0,
            "erstellt_am": datetime.now().isoformat(),
            "statistik": {
                "gesamt_trades": 0,
                "gewonnen": 0,
                "verloren": 0,
                "offen": 0,
                "gesamt_pnl": 0.0,
                "beste_trade": 0.0,
                "schlechtester_trade": 0.0,
                "win_rate": 0.0,
                "roi": 0.0,
                "max_drawdown": 0.0,
            },
            "tages_snapshots": [],
            "offene_trades": [],
            "letzte_trades": [],
        }
    
    # Berechne aus Excel
    offene = df[df["Status"] == "offen"]
    geschlossene = df[df["Status"].isin(["gewonnen", "verloren", "breakeven"])]
    
    gewonnen = len(df[df["Status"] == "gewonnen"])
    verloren = len(df[df["Status"] == "verloren"])
    
    # P&L berechnen
    gesamt_pnl = 0.0
    if "P&L" in df.columns:
        gesamt_pnl = round(df["P&L"].sum(), 2)
    
    aktuelles_kapital = round(STARTKAPITAL + gesamt_pnl, 2)
    
    # Win Rate
    abgeschlossen = gewonnen + verloren
    win_rate = round(gewonnen / abgeschlossen * 100, 1) if abgeschlossen > 0 else 0.0
    
    # ROI
    roi = round((aktuelles_kapital - STARTKAPITAL) / STARTKAPITAL * 100, 2)
    
    # Beste/Schlechteste Trade
    beste_trade = 0.0
    schlechtester_trade = 0.0
    if "P&L" in df.columns and not geschlossene.empty:
        beste_trade = round(geschlossene["P&L"].max(), 2)
        schlechtester_trade = round(geschlossene["P&L"].min(), 2)
    
    # Max Drawdown
    max_drawdown = 0.0
    if not df.empty and "P&L" in df.columns:
        kapital_progression = [STARTKAPITAL]
        for pnl in df["P&L"]:
            kapital_progression.append(kapital_progression[-1] + pnl)
        peak = max(kapital_progression)
        for k in kapital_progression:
            drawdown = (peak - k) / peak * 100 if peak > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)
        max_drawdown = round(max_drawdown, 2)
    
    return {
        "startkapital": STARTKAPITAL,
        "aktuelles_kapital": aktuelles_kapital,
        "pnl_gesamt": gesamt_pnl,
        "erstellt_am": datetime.now().isoformat(),
        "statistik": {
            "gesamt_trades": len(df),
            "gewonnen": gewonnen,
            "verloren": verloren,
            "offen": len(offene),
            "gesamt_pnl": gesamt_pnl,
            "beste_trade": beste_trade,
            "schlechtester_trade": schlechtester_trade,
            "win_rate": win_rate,
            "roi": roi,
            "max_drawdown": max_drawdown,
        },
        "tages_snapshots": [],
        "offene_trades": offene.to_dict('records') if not offene.empty else [],
        "letzte_trades": df[df["Status"] != "offen"].sort_values(
            "Geschlossen am", ascending=False, na_position='last'
        ).head(20).to_dict('records') if not df.empty else [],
    }


def tages_snapshot():
    """Speichert täglichen Kapital-Snapshot."""
    stats = get_statistik()
    heute = datetime.now().strftime("%d.%m.%Y")
    
    log.info(f"📊 Snapshot: {heute} | €{stats['aktuelles_kapital']:.2f}")


def generiere_tages_report() -> str:
    """Generiert Tagesreport aus Excel-Daten."""
    stats = get_statistik()
    kapital = stats["aktuelles_kapital"]
    start = stats["startkapital"]
    pnl = stats["pnl_gesamt"]
    roi = stats["statistik"]["roi"]
    wr = stats["statistik"]["win_rate"]
    offen = stats["statistik"]["offen"]
    gewon = stats["statistik"]["gewonnen"]
    verl = stats["statistik"]["verloren"]
    
    return (
        f"📊 *TRADING DEMO - TAGESREPORT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kapital: *€{kapital:.2f}*\n"
        f"{'📈' if pnl >= 0 else '📉'} Gesamt P&L: *{'+' if pnl >= 0 else ''}€{pnl:.2f}* ({'+' if roi >= 0 else ''}{roi:.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Win Rate: *{wr:.1f}%*\n"
        f"✅ Gewonnen: *{gewon}* | ❌ Verloren: *{verl}*\n"
        f"🔄 Offen: *{offen}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Startkapital: €{start:.2f}\n"
        f"🤖 _Trading Multi-Agent v3.0_"
    )
