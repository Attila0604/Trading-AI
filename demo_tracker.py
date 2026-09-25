"""
demo_tracker.py (v4 - openpyxl only, NO pandas)
────────────────────────────────────────────────
Liest/schreibt Demo-Kapital direkt aus Excel mit openpyxl.

NEU in v4:
- Drawdown wird nach SCHLIESS-Reihenfolge berechnet (vorher: Zeilen-/
  Eröffnungsreihenfolge -> falscher Wert) und es gibt zusätzlich den
  AKTUELLEN Drawdown vom letzten Kapital-Hoch.
- Kapitalverlauf (tages_snapshots) wird aus den geschlossenen Trades
  erzeugt -> Chart im Dashboard ist nicht mehr leer.
- get_risiko_status(): echter Portfolio-Schutz (Drawdown-Pause, Einsatz-
  Reduktion, Exposure-Deckel, max. offene Trades, ein Trade pro Asset).
- signal_oeffnen() lehnt Trades ab, die gegen diese Regeln verstoßen,
  und gibt {"abgelehnt": grund} zurück.
- Alle Zeitstempel in Wiener Zeit (config.jetzt()).
- Break-even-Stop (Spalte "BE-Seit") + Status "breakeven" (P&L 0).
- Trades können zum Marktpreis geschlossen werden (main.py /demo/trade/{id}/schliessen?ergebnis=markt).
"""

import os
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook, Workbook

from money_management import berechne_einsatz
from config import (DATA_DIR, DEMO_STARTKAPITAL as STARTKAPITAL,
                    MAX_RISK_PCT as RISIKO_PROZENT, MM_MODUS,
                    STOP_LOSS_PCT as SL_PROZENT, TAKE_PROFIT_PCT as TP_PROZENT,
                    DD_PAUSE_PCT, DD_VORSICHT_PCT, DD_VORSICHT_FAKTOR,
                    MAX_EXPOSURE_PCT, MAX_OFFENE_TRADES, EIN_TRADE_PRO_ASSET,
                    MAX_GLEICHE_RICHTUNG, FINANZIERUNG_AN, FINANZIERUNG_SAETZE,
                    FINANZIERUNG_STANDARD, jetzt)

log = logging.getLogger(__name__)

# ─── Datei-Sperre: verhindert parallele Schreib-/Lesezugriffe ───────────────
# RLock = reentrant, damit z.B. signal_oeffnen intern get_statistik aufrufen darf.
_excel_lock = threading.RLock()


def _synchronized(fn):
    """Serialisiert alle Excel-Zugriffe - kein gleichzeitiges Lesen/Schreiben."""
    def wrapper(*args, **kwargs):
        with _excel_lock:
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ─── Konfiguration ──────────────────────────────────────────
EXCEL_FILE     = Path(DATA_DIR) / "Trading_Tracker.xlsx"
SHEET_NAME     = "Demo-Kapital"

# Spalten-Reihenfolge (KONSISTENT halten!)
COLUMNS = [
    "Datum", "Uhrzeit", "ID", "Asset", "Action", "Richtung",
    "Konfidenz", "Einsatz", "SL %", "TP %", "SL Absolut", "TP Absolut",
    "R:R", "Entry-Price", "Aktuell", "P&L", "Status",
    "Geöffnet am", "Geschlossen am", "Zusammenfassung", "Score", "Strategie",
    "MM-Modus", "MM-Begründung",
    "BE-Seit",       # Zeitpunkt, ab dem der nachgezogene Stop aktiv ist ("" = nicht aktiv)
    "BE-Stop",       # Preisniveau des nachgezogenen Stops
    "Finanzierung",  # Übernacht-Kosten in EUR, beim Schließen abgezogen
    "Signalquelle",  # "regeln" (Regeln + KI-Veto) oder "ki" (alter Ablauf)
    "Veto-Grund",    # nur bei V-Zeilen: warum die KI blockiert hat
    "Veto-P&L",      # nur bei V-Zeilen: was der blockierte Trade gebracht HÄTTE
]

os.makedirs(DATA_DIR, exist_ok=True)


# ─── Helper-Funktionen ──────────────────────────────────────

def _ensure_workbook():
    """Stellt sicher dass Workbook und Sheet existieren."""
    if not EXCEL_FILE.exists():
        wb = Workbook()
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        ws = wb.create_sheet(SHEET_NAME)
        ws.append(COLUMNS)
        wb.save(EXCEL_FILE)
        log.info(f"✅ Excel erstellt: {EXCEL_FILE}")
        return

    wb = load_workbook(EXCEL_FILE)
    if SHEET_NAME not in wb.sheetnames:
        ws = wb.create_sheet(SHEET_NAME)
        ws.append(COLUMNS)
        wb.save(EXCEL_FILE)
        log.info(f"✅ Sheet '{SHEET_NAME}' erstellt")


def _lade_trades() -> list:
    """Lädt alle Trades als Liste von Dicts."""
    _ensure_workbook()

    try:
        wb = load_workbook(EXCEL_FILE, data_only=True)
        if SHEET_NAME not in wb.sheetnames:
            return []

        ws = wb[SHEET_NAME]
        rows = list(ws.iter_rows(values_only=True))

        if len(rows) < 2:
            return []

        headers = list(rows[0])
        trades = []
        for row in rows[1:]:
            if all(cell is None for cell in row):
                continue
            trade = {h: v for h, v in zip(headers, row)}
            trades.append(trade)

        return trades
    except Exception as e:
        log.error(f"Excel lesen Fehler: {e}")
        return []


def _speichere_trades(trades: list):
    """Speichert alle Trades zurück ins Excel."""
    if not trades:
        log.warning("Leere Trade-Liste - überspringe Speichern")
        return

    try:
        if EXCEL_FILE.exists():
            wb = load_workbook(EXCEL_FILE)
        else:
            wb = Workbook()
            if "Sheet" in wb.sheetnames:
                del wb["Sheet"]

        # Sheet komplett neu schreiben (cleaner als rows einzeln updaten)
        if SHEET_NAME in wb.sheetnames:
            del wb[SHEET_NAME]
        ws = wb.create_sheet(SHEET_NAME)

        ws.append(COLUMNS)
        for trade in trades:
            row = [trade.get(col, "") for col in COLUMNS]
            ws.append(row)

        wb.save(EXCEL_FILE)
        log.info(f"✅ Excel gespeichert: {len(trades)} Trades")
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


def _safe_float(value, default: float = 0.0) -> float:
    """Konvertiert sicher zu float."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_zeit(wert) -> Optional[datetime]:
    try:
        s = str(wert or "").strip()
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None


def _norm_asset(a) -> str:
    return str(a or "").strip().upper().replace(" ", "")


def _ist_veto(t: dict) -> bool:
    """V-Zeilen = von der KI blockierte Signale. Reines Schattenprotokoll:
    kein Kapital, kein Risiko, zählen in KEINER Trade-Statistik."""
    return str(t.get("ID", "")).startswith("V") or str(t.get("Status", "")).startswith("veto")


def _naechste_id(trades: list, praefix: str) -> str:
    nr = 0
    for t in trades:
        tid = str(t.get("ID", ""))
        if tid.startswith(praefix) and tid[1:].isdigit():
            nr = max(nr, int(tid[1:]))
    return f"{praefix}{nr + 1:04d}"


def _r_wert(pnl, einsatz) -> Optional[float]:
    """Ergebnis in R = Vielfaches des riskierten Einsatzes (größenunabhängig)."""
    e = _safe_float(einsatz)
    return (_safe_float(pnl) / e) if e > 0 else None


def _signalvergleich(echte: list, vetos: list) -> dict:
    """
    Die eigentliche Frage: Bringt das KI-Veto etwas?
    Verglichen wird das Ø-Ergebnis in R der AUSGEFÜHRTEN Regel-Trades mit dem
    der BLOCKIERTEN. Schneiden die blockierten schlechter ab, filtert die KI
    wirklich schlechte Trades heraus. Schneiden sie gleich oder besser ab,
    kostet das Veto nur Gelegenheiten.
    """
    regel_zu = [t for t in echte if t.get("Signalquelle") == "regeln"
                and t.get("Status") in ("gewonnen", "verloren", "breakeven")]
    veto_zu  = [v for v in vetos if v.get("Status") in ("veto_gewonnen", "veto_verloren")]

    def schnitt(zeilen, key):
        rs = [r for r in (_r_wert(z.get(key), z.get("Einsatz")) for z in zeilen) if r is not None]
        return round(sum(rs) / len(rs), 2) if rs else None

    regel_r = schnitt(regel_zu, "P&L")
    veto_r  = schnitt(veto_zu, "Veto-P&L")
    MIN = 10
    if len(regel_zu) < MIN or len(veto_zu) < MIN:
        urteil = (f"Noch zu wenig Daten: {len(veto_zu)}/{MIN} abgeschlossene Vetos, "
                  f"{len(regel_zu)}/{MIN} abgeschlossene Regel-Trades")
    elif veto_r < regel_r - 0.1:
        urteil = "Blockierte Signale laufen schlechter als ausgeführte → das KI-Veto hilft"
    elif veto_r > regel_r + 0.1:
        urteil = "Blockierte Signale laufen BESSER als ausgeführte → das KI-Veto schadet"
    else:
        urteil = "Kein erkennbarer Unterschied → das KI-Veto bringt bisher nichts"

    return {
        "regel_trades_abgeschlossen": len(regel_zu),
        "regel_schnitt_r":            regel_r,
        "regel_pnl":                  round(sum(_safe_float(t.get("P&L")) for t in regel_zu), 2),
        "vetos_gesamt":               len(vetos),
        "vetos_offen":                sum(1 for v in vetos if v.get("Status") == "veto_offen"),
        "vetos_abgeschlossen":        len(veto_zu),
        "vetos_waeren_gewonnen":      sum(1 for v in veto_zu if _safe_float(v.get("Veto-P&L")) > 0),
        "vetos_waeren_verloren":      sum(1 for v in veto_zu if _safe_float(v.get("Veto-P&L")) < 0),
        "veto_schnitt_r":             veto_r,
        # negativ = diese Summe hätten die blockierten Trades VERLOREN -> KI hat sie gespart
        "veto_bilanz":                round(sum(_safe_float(v.get("Veto-P&L")) for v in veto_zu), 2),
        "urteil":                     urteil,
    }


def _ist_long(action) -> bool:
    return str(action or "").strip().lower() in ("buy", "long")


def swap_kosten(einsatz: float, sl_pct: float, asset: str, tage: float) -> float:
    """
    Reine Rechnung: Finanzierungskosten in EUR für eine gegebene Haltedauer.
    Getrennt von finanzierungskosten(), damit der Backtest sie ebenfalls
    nutzen kann - dort gibt es keine Zeitstempel, nur Kerzen-Abstände.
    """
    if not FINANZIERUNG_AN:
        return 0.0
    try:
        einsatz = float(einsatz or 0)
        sl_pct  = float(sl_pct or 0)
        tage    = float(tage or 0)
        if einsatz <= 0 or sl_pct <= 0 or tage <= 0:
            return 0.0
        tage    = min(tage, 365.0)          # gegen kaputte Zeitstempel
        nominal = einsatz / (sl_pct / 100.0)
        satz    = FINANZIERUNG_SAETZE.get(str(asset or "").strip(), FINANZIERUNG_STANDARD)
        return round(nominal * satz / 100.0 * tage, 2)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0


def finanzierungskosten(einsatz: float, sl_pct: float, asset: str,
                        geoeffnet, geschlossen=None) -> float:
    """
    Übernacht-Finanzierung (CFD-Swap) in EUR für die gehaltene Zeit.

    Die Kosten fallen auf das NOMINALE Volumen an, nicht auf den Einsatz:
    der Einsatz ist nur das Risiko (= Verlust bei SL), das tatsächlich
    bewegte Volumen ist Einsatz / SL%. Bei 20 EUR Einsatz und 2% Stop
    sind das 1000 EUR Nominal - über zwei Wochen läppert sich das.

    Nebeneffekt der weiteren ATR-Stops: größerer SL% -> kleineres Nominal
    -> WENIGER Finanzierung. Weite Stops sind hier also doppelt sinnvoll.
    """
    start = _parse_zeit(geoeffnet)
    ende  = _parse_zeit(geschlossen) or jetzt()
    if start is None:
        return 0.0
    return swap_kosten(einsatz, sl_pct, asset, (ende - start).total_seconds() / 86400)


def pnl_aus_preis(einsatz: float, entry: float, exit_price: float,
                  action: str, sl_pct: float, tp_pct: float) -> float:
    """
    Realer P&L beim Schließen zu einem beliebigen Preis (risikobasiert).
    Einsatz = Verlust bei SL. Die Preisbewegung wird in R-Vielfachen gemessen:
    volle SL-Distanz gegen dich = -Einsatz, volle TP-Distanz = +Einsatz × R:R.
    Gedeckelt zwischen -Einsatz und +Einsatz × R:R.
    """
    if entry <= 0 or sl_pct <= 0:
        return 0.0
    move_pct = (exit_price - entry) / entry * 100
    if str(action).lower() in ("sell", "short"):
        move_pct = -move_pct
    r = move_pct / sl_pct
    pnl = einsatz * r
    max_gewinn = einsatz * (tp_pct / sl_pct) if sl_pct > 0 else einsatz
    return round(max(-einsatz, min(pnl, max_gewinn)), 2)


# ─── Kapitalkurve & Drawdown (nach Schließ-Reihenfolge!) ─────────────────────

def _kapitalkurve(trades: list) -> list:
    """
    Kapitalverlauf in der Reihenfolge, in der die Trades GESCHLOSSEN wurden.
    Vorher wurde in Zeilenreihenfolge (= Eröffnung) gerechnet -> der Drawdown
    war falsch, weil ein Trade schon "verbucht" war, bevor er zu war.
    Liefert Liste von {datum, kapital, trade_id, pnl}.
    """
    geschlossene = [t for t in trades if t.get("Status") in ("gewonnen", "verloren", "breakeven")]
    geschlossene.sort(key=lambda t: str(t.get("Geschlossen am", "")))

    kurve = [{"datum": "Start", "kapital": round(STARTKAPITAL, 2), "trade_id": "", "pnl": 0.0}]
    kapital = STARTKAPITAL
    for t in geschlossene:
        pnl = _safe_float(t.get("P&L", 0))
        kapital += pnl
        zeit = _parse_zeit(t.get("Geschlossen am"))
        kurve.append({
            "datum":    zeit.strftime("%d.%m.") if zeit else str(t.get("Datum", "")),
            "kapital":  round(kapital, 2),
            "trade_id": t.get("ID", ""),
            "pnl":      round(pnl, 2),
        })
    return kurve


def _drawdowns(kurve: list) -> tuple:
    """(max_drawdown_pct, aktueller_drawdown_pct, peak_kapital)"""
    max_dd = 0.0
    peak = kurve[0]["kapital"] if kurve else STARTKAPITAL
    for p in kurve:
        k = p["kapital"]
        if k > peak:
            peak = k
        dd = (peak - k) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    letzte = kurve[-1]["kapital"] if kurve else STARTKAPITAL
    aktuell = (peak - letzte) / peak * 100 if peak > 0 else 0.0
    return round(max_dd, 2), round(aktuell, 2), round(peak, 2)


# ─── Public API ─────────────────────────────────────────────

@_synchronized
def get_statistik() -> dict:
    """Liest Statistik direkt aus Excel."""
    alle   = _lade_trades()
    vetos  = [t for t in alle if _ist_veto(t)]
    trades = [t for t in alle if not _ist_veto(t)]   # nur echte Trades
    vergleich = _signalvergleich(trades, vetos)

    if not trades:
        leer = {
            "startkapital": STARTKAPITAL,
            "aktuelles_kapital": STARTKAPITAL,
            "pnl_gesamt": 0.0,
            "erstellt_am": jetzt().isoformat(),
            "statistik": {
                "gesamt_trades": 0, "gewonnen": 0, "verloren": 0, "breakeven": 0, "offen": 0,
                "gesamt_pnl": 0.0, "beste_trade": 0.0, "schlechtester_trade": 0.0,
                "win_rate": 0.0, "roi": 0.0,
                "max_drawdown": 0.0, "aktueller_drawdown": 0.0, "peak_kapital": STARTKAPITAL,
                "offene_exposure": 0.0, "offene_exposure_pct": 0.0,
            },
            "tages_snapshots": [],
            "offene_trades": [],
            "letzte_trades": [],
            "signalvergleich": vergleich,
        }
        leer["risiko"] = _risiko_aus_stats(leer)
        return leer

    offene = [t for t in trades if t.get("Status") == "offen"]
    # Trades OHNE Einstiegspreis sind nicht auswertbar (entstanden z.B. bei einer
    # Verbindungsstörung): sie haben P&L 0 und würden die Win-Rate verfälschen.
    auswertbar = [t for t in trades if _safe_float(t.get("Entry-Price", 0)) > 0]

    geschlossene = [t for t in auswertbar if t.get("Status") in ("gewonnen", "verloren", "breakeven")]
    gewonnen = sum(1 for t in auswertbar if t.get("Status") == "gewonnen")
    verloren = sum(1 for t in auswertbar if t.get("Status") == "verloren")
    breakeven = sum(1 for t in auswertbar if t.get("Status") == "breakeven")

    gesamt_pnl = round(sum(_safe_float(t.get("P&L", 0)) for t in trades), 2)
    aktuelles_kapital = round(STARTKAPITAL + gesamt_pnl, 2)

    abgeschlossen = gewonnen + verloren
    win_rate = round(gewonnen / abgeschlossen * 100, 1) if abgeschlossen > 0 else 0.0
    roi = round((aktuelles_kapital - STARTKAPITAL) / STARTKAPITAL * 100, 2) if STARTKAPITAL > 0 else 0.0

    beste_trade = 0.0
    schlechtester_trade = 0.0
    if geschlossene:
        pnls = [_safe_float(t.get("P&L", 0)) for t in geschlossene]
        beste_trade = round(max(pnls), 2)
        schlechtester_trade = round(min(pnls), 2)

    # Kapitalkurve + Drawdown nach Schließ-Reihenfolge
    kurve = _kapitalkurve(trades)
    max_drawdown, aktueller_drawdown, peak_kapital = _drawdowns(kurve)

    # Offenes Risiko (Einsatz = max. Verlust pro Trade)
    offene_exposure = round(sum(_safe_float(t.get("Einsatz", 0)) for t in offene), 2)
    offene_exposure_pct = round(offene_exposure / aktuelles_kapital * 100, 2) if aktuelles_kapital > 0 else 0.0
    offene_pro_asset = {}
    for t in offene:
        a = _norm_asset(t.get("Asset"))
        offene_pro_asset[a] = offene_pro_asset.get(a, 0) + 1

    letzte = sorted(
        [t for t in trades if t.get("Status") != "offen"],
        key=lambda t: str(t.get("Geschlossen am", "")),
        reverse=True
    )[:20]

    stats = {
        "startkapital": STARTKAPITAL,
        "aktuelles_kapital": aktuelles_kapital,
        "pnl_gesamt": gesamt_pnl,
        "erstellt_am": jetzt().isoformat(),
        "statistik": {
            "gesamt_trades": len(auswertbar),
            "gewonnen": gewonnen,
            "verloren": verloren,
            "breakeven": breakeven,
            "offen": len(offene),
            "gesamt_pnl": gesamt_pnl,
            "beste_trade": beste_trade,
            "schlechtester_trade": schlechtester_trade,
            "win_rate": win_rate,
            "roi": roi,
            "max_drawdown": max_drawdown,
            "aktueller_drawdown": aktueller_drawdown,
            "peak_kapital": peak_kapital,
            "offene_exposure": offene_exposure,
            "offene_exposure_pct": offene_exposure_pct,
            "offene_pro_asset": offene_pro_asset,
        },
        # Chart-Daten: ein Punkt pro geschlossenem Trade (Start + jeder Abschluss)
        "tages_snapshots": [{"datum": p["datum"], "kapital": p["kapital"]} for p in kurve],
        "kapitalverlauf": kurve,
        "offene_trades": offene,
        "letzte_trades": letzte,
        "signalvergleich": vergleich,
    }
    stats["risiko"] = _risiko_aus_stats(stats)
    return stats


def _risiko_aus_stats(stats: dict) -> dict:
    """
    Portfolio-Schutz. Entscheidet, ob neue Trades erlaubt sind und mit welchem
    Einsatz-Faktor. Das Dashboard zeigt NUR noch an, was hier entschieden wird.
    """
    s = stats["statistik"]
    dd      = _safe_float(s.get("aktueller_drawdown", 0))
    expo    = _safe_float(s.get("offene_exposure_pct", 0))
    offen   = int(s.get("offen", 0))
    gruende = []
    faktor  = 1.0
    stufe   = "NORMAL"

    if dd >= DD_PAUSE_PCT:
        stufe = "PAUSE"
        faktor = 0.0
        gruende.append(f"Drawdown {dd:.1f}% ≥ {DD_PAUSE_PCT:.0f}% → keine neuen Trades bis Erholung")
    elif dd >= DD_VORSICHT_PCT:
        stufe = "VORSICHT"
        faktor = DD_VORSICHT_FAKTOR
        gruende.append(f"Drawdown {dd:.1f}% ≥ {DD_VORSICHT_PCT:.0f}% → Einsatz × {DD_VORSICHT_FAKTOR:g}")

    if expo >= MAX_EXPOSURE_PCT:
        if stufe != "PAUSE":
            stufe = "EXPOSURE_VOLL"
        faktor = 0.0
        gruende.append(f"Offenes Risiko {expo:.1f}% ≥ {MAX_EXPOSURE_PCT:.0f}% des Kapitals")
    if offen >= MAX_OFFENE_TRADES:
        if stufe not in ("PAUSE", "EXPOSURE_VOLL"):
            stufe = "EXPOSURE_VOLL"
        faktor = 0.0
        gruende.append(f"{offen} offene Trades ≥ Limit {MAX_OFFENE_TRADES}")

    return {
        "stufe":               stufe,
        "neue_trades_erlaubt": faktor > 0,
        "einsatz_faktor":      faktor,
        "aktueller_drawdown":  dd,
        "max_drawdown":        _safe_float(s.get("max_drawdown", 0)),
        "peak_kapital":        _safe_float(s.get("peak_kapital", STARTKAPITAL)),
        "offene_exposure_pct": expo,
        "offene_exposure":     _safe_float(s.get("offene_exposure", 0)),
        "offen":               offen,
        "gruende":             gruende,
        "limits": {
            "dd_pause_pct":        DD_PAUSE_PCT,
            "dd_vorsicht_pct":     DD_VORSICHT_PCT,
            "dd_vorsicht_faktor":  DD_VORSICHT_FAKTOR,
            "max_exposure_pct":    MAX_EXPOSURE_PCT,
            "max_offene_trades":   MAX_OFFENE_TRADES,
            "ein_trade_pro_asset": EIN_TRADE_PRO_ASSET,
            "max_gleiche_richtung": MAX_GLEICHE_RICHTUNG,
        },
    }


@_synchronized
def get_risiko_status() -> dict:
    """Aktueller Portfolio-Schutz-Status (für Pipeline, /status, Dashboard)."""
    return get_statistik()["risiko"]


@_synchronized
def signal_oeffnen(signal: dict, einsatz_faktor: float = 1.0) -> dict:
    """
    Öffnet einen neuen Demo-Trade und speichert in Excel.
    Gibt bei Ablehnung {"abgelehnt": "<Grund>", ...} zurück (KEIN Trade).
    """
    trades = _lade_trades()
    stats  = get_statistik()
    kapital = stats["aktuelles_kapital"]
    risiko  = stats["risiko"]
    asset   = str(signal.get("asset", "")).strip()

    # ── Portfolio-Schutz (Backend, nicht nur Anzeige) ─────────────────
    if not risiko["neue_trades_erlaubt"]:
        grund = "; ".join(risiko["gruende"]) or risiko["stufe"]
        log.warning(f"🛡️ Trade {asset} abgelehnt: {grund}")
        return {"abgelehnt": grund, "asset": asset, "stufe": risiko["stufe"]}

    offene_alle = [t for t in trades if t.get("Status") == "offen"]

    if EIN_TRADE_PRO_ASSET:
        offene_gleich = [t for t in offene_alle
                         if _norm_asset(t.get("Asset")) == _norm_asset(asset)]
        if offene_gleich:
            ids = ", ".join(str(t.get("ID")) for t in offene_gleich)
            richtung = "/".join(sorted({str(t.get("Richtung", "")) for t in offene_gleich}))
            grund = f"{asset} bereits offen ({ids}, {richtung})"
            log.warning(f"🛡️ Trade {asset} abgelehnt: {grund}")
            return {"abgelehnt": grund, "asset": asset, "stufe": "ASSET_OFFEN"}

    # ── Korrelations-Deckel ───────────────────────────────────────────
    # Vier Assets in dieselbe Richtung sind EINE Wette mit vier Tickets.
    if MAX_GLEICHE_RICHTUNG > 0:
        neu_long = _ist_long(signal.get("action") or signal.get("direction"))
        gleiche  = sum(1 for t in offene_alle if _ist_long(t.get("Action")) == neu_long)
        if gleiche >= MAX_GLEICHE_RICHTUNG:
            wort  = "LONG" if neu_long else "SHORT"
            grund = f"schon {gleiche} offene {wort}-Positionen (Limit {MAX_GLEICHE_RICHTUNG}) - Klumpenrisiko"
            log.warning(f"🛡️ Trade {asset} abgelehnt: {grund}")
            return {"abgelehnt": grund, "asset": asset, "stufe": "RICHTUNG_VOLL"}
    # ──────────────────────────────────────────────────────────────────

    faktor = max(0.0, min(1.0, float(einsatz_faktor))) * float(risiko["einsatz_faktor"])
    if faktor <= 0:
        return {"abgelehnt": "Einsatz-Faktor 0", "asset": asset, "stufe": risiko["stufe"]}
    # ─────────────────────────────────────────────────────────────────

    sl_pct = _validiere_prozent(signal.get("stopLoss"), SL_PROZENT)
    tp_pct = _validiere_prozent(signal.get("takeProfit"), TP_PROZENT)

    # ── Money-Management: Einsatz aus gewähltem Modus ────────────────
    modus = signal.get("mm_modus", MM_MODUS)
    mm = berechne_einsatz(
        modus=modus,
        kapital=kapital,
        ctx={
            "confidence":           signal.get("confidence", 0),
            "win_rate":             stats["statistik"]["win_rate"],
            "gesamt_abgeschlossen": stats["statistik"]["gewonnen"] + stats["statistik"]["verloren"],
            "sl_pct":               sl_pct,
            "tp_pct":               tp_pct,
            "volatility_pct":       signal.get("volatility_pct", 0),
            "letzte_trades":        stats.get("letzte_trades", []),
        },
        params=signal.get("mm_params"),
    )
    einsatz          = round(float(mm["einsatz"]) * faktor, 2)
    mm_modus_genutzt = mm["modus"]
    mm_begruendung   = mm["begruendung"]
    if faktor < 1.0:
        mm_begruendung += f" | Schutz ×{faktor:g}"

    # Exposure-Deckel: passt der neue Einsatz noch unter das Limit?
    frei = kapital * MAX_EXPOSURE_PCT / 100 - risiko["offene_exposure"]
    if einsatz > frei:
        if frei < 1.0:
            grund = f"Exposure-Limit erreicht ({risiko['offene_exposure_pct']:.1f}% offen)"
            log.warning(f"🛡️ Trade {asset} abgelehnt: {grund}")
            return {"abgelehnt": grund, "asset": asset, "stufe": "EXPOSURE_VOLL"}
        log.info(f"🛡️ Einsatz {asset} von €{einsatz:.2f} auf €{frei:.2f} gekürzt (Exposure-Limit)")
        einsatz = round(frei, 2)
        mm_begruendung += f" | auf €{einsatz:.2f} gekürzt (Exposure)"
    if einsatz < 1.0:
        return {"abgelehnt": "Einsatz unter €1", "asset": asset, "stufe": risiko["stufe"]}
    # ─────────────────────────────────────────────────────────────────

    rr         = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0
    sl_absolut = round(einsatz, 2)
    tp_absolut = round(einsatz * rr, 2)

    # Nur T-Zeilen zählen - V-Zeilen (Vetos) dürfen keine Nummern verbrauchen
    trade_id = _naechste_id(trades, "T")
    now = jetzt()

    neue_zeile = {
        "Datum": now.strftime("%d.%m.%Y"),
        "Uhrzeit": now.strftime("%H:%M:%S"),
        "ID": trade_id,
        "Asset": asset,
        "Action": str(signal.get("action", "")).upper(),
        "Richtung": str(signal.get("direction", "")).upper(),
        "Konfidenz": int(_safe_float(signal.get("confidence", 0))),
        "Einsatz": einsatz,
        "SL %": sl_pct,
        "TP %": tp_pct,
        "SL Absolut": sl_absolut,
        "TP Absolut": tp_absolut,
        "R:R": rr,
        "Entry-Price": _safe_float(signal.get("entry_price", 0)),
        "Aktuell": 0.0,
        "P&L": 0.0,
        "Status": "offen",
        "Geöffnet am": now.isoformat(),
        "Geschlossen am": "",
        "Zusammenfassung": signal.get("summary", ""),
        "Score": signal.get("sessionScore", 0),
        "Strategie": signal.get("strategyUsed", ""),
        "MM-Modus": mm_modus_genutzt,
        "MM-Begründung": mm_begruendung,
        "Signalquelle": signal.get("signalQuelle") or "ki",
    }

    trades.append(neue_zeile)
    _speichere_trades(trades)

    log.info(
        f"✅ Demo-Trade: {trade_id} | {neue_zeile['Asset']} {neue_zeile['Action']} | "
        f"€{einsatz} ({mm_modus_genutzt}) | SL:{sl_pct}% TP:{tp_pct}% | Entry:{neue_zeile['Entry-Price']}"
    )

    return neue_zeile


@_synchronized
def trade_schliessen(trade_id: str, ergebnis: str, pnl_override: Optional[float] = None) -> dict:
    """Schließt einen offenen Demo-Trade."""
    trades = _lade_trades()

    if not trades:
        log.warning(f"Trade {trade_id} nicht gefunden (Excel leer)")
        return {}

    idx = None
    for i, t in enumerate(trades):
        if t.get("ID") == trade_id:
            idx = i
            break

    if idx is None:
        log.warning(f"Trade {trade_id} nicht gefunden")
        return {}

    trade = trades[idx]

    if trade.get("Status") != "offen":
        log.warning(f"Trade {trade_id} ist nicht offen")
        return trade

    if pnl_override is not None:
        pnl = pnl_override
    elif ergebnis == "gewonnen":
        pnl = _safe_float(trade.get("TP Absolut", 0))
    elif ergebnis == "verloren":
        pnl = -_safe_float(trade.get("SL Absolut", 0))
    else:                       # "breakeven", "abgebrochen"
        pnl = 0.0

    zu = jetzt()

    # ── Übernacht-Finanzierung abziehen ──────────────────────────────
    # Abgebrochene Trades (nie wirklich am Markt) bleiben kostenfrei.
    kosten = 0.0
    if ergebnis != "abgebrochen":
        kosten = finanzierungskosten(
            _safe_float(trade.get("Einsatz")), _safe_float(trade.get("SL %")),
            trade.get("Asset"), trade.get("Geöffnet am"), zu.isoformat(),
        )
        pnl -= kosten
        # Ehrliche Statistik: frisst die Finanzierung den Treffer auf, war es
        # wirtschaftlich KEIN Gewinn - sonst zeigt die Win-Rate mehr, als die
        # Kapitalkurve hergibt.
        if ergebnis == "gewonnen" and pnl < 0:
            log.info(f"↩️ {trade_id}: Ziel erreicht, aber €{kosten:.2f} Finanzierung → zählt als Verlust")
            ergebnis = "verloren"
    # ─────────────────────────────────────────────────────────────────

    trades[idx]["Status"] = ergebnis
    trades[idx]["P&L"] = round(pnl, 2)
    trades[idx]["Finanzierung"] = kosten
    trades[idx]["Geschlossen am"] = zu.isoformat()

    _speichere_trades(trades)

    emoji = {"gewonnen": "✅", "verloren": "❌", "breakeven": "⚖️"}.get(ergebnis, "🗑️")
    zusatz = f" (inkl. €{kosten:.2f} Finanzierung)" if kosten else ""
    log.info(f"{emoji} Trade {trade_id} | {ergebnis.upper()} | P&L: {'+' if pnl >= 0 else ''}€{pnl:.2f}{zusatz}")

    return trades[idx]


@_synchronized
def breakeven_aktivieren(trade_id: str, stop_preis: float) -> dict:
    """
    Zieht den Stop eines offenen Trades nach und merkt sich das Preisniveau.

    Der Stop liegt bewusst NICHT exakt auf dem Entry, sondern ein Stück im
    Gewinn (BREAKEVEN_STOP_R): genau auf dem Entry wird er von jedem normalen
    Zurücklaufen sofort ausgelöst - vier von vier Auslösungen endeten so bei 0.
    """
    trades = _lade_trades()
    for t in trades:
        if t.get("ID") == trade_id and t.get("Status") == "offen":
            if str(t.get("BE-Seit") or "").strip():
                return t
            t["BE-Seit"] = jetzt().isoformat()
            t["BE-Stop"] = round(float(stop_preis), 5)
            _speichere_trades(trades)
            log.info(f"⚖️ Stop nachgezogen: {trade_id} | {t.get('Asset')} | "
                     f"Entry {t.get('Entry-Price')} → Stop {t['BE-Stop']}")
            return t
    log.warning(f"Break-even: Trade {trade_id} nicht gefunden oder nicht offen")
    return {}


@_synchronized
def tracker_zuruecksetzen() -> dict:
    """
    Archiviert die laufende Excel und startet einen leeren Tracker bei 0.

    Es wird NICHTS gelöscht: die alte Datei bleibt mit Zeitstempel im
    DATA_DIR liegen. Gedacht für einen sauberen Schnitt zwischen zwei
    Parameter-Generationen - sonst stehen Trades mit alten und neuen
    SL/TP-Regeln in derselben Statistik und die Auswertung ist wertlos.
    """
    vorher = _lade_trades()
    stats  = get_statistik()
    if not EXCEL_FILE.exists():
        _ensure_workbook()
        return {"archiviert": None, "trades": 0, "kapital_vorher": stats["aktuelles_kapital"]}

    stempel = jetzt().strftime("%Y%m%d_%H%M%S")
    ziel    = EXCEL_FILE.parent / f"Trading_Tracker_archiv_{stempel}.xlsx"
    EXCEL_FILE.rename(ziel)
    _ensure_workbook()

    log.info(f"🗃️ Tracker zurückgesetzt | {len(vorher)} Trades nach {ziel.name} archiviert "
             f"| Kapital {stats['aktuelles_kapital']:.2f} € → {STARTKAPITAL:.2f} €")
    return {
        "archiviert":      ziel.name,
        "trades":          len(vorher),
        "kapital_vorher":  stats["aktuelles_kapital"],
        "kapital_nachher": STARTKAPITAL,
    }


@_synchronized
def veto_protokollieren(signal: dict) -> dict:
    """
    Schreibt ein von der KI blockiertes Signal als V-Zeile ins Excel.
    Kein Kapital, kein Risiko, keine Wirkung auf Schutz oder Statistik -
    der 4h-Check verfolgt nur, was der Trade gebracht HÄTTE.
    Einsatz wird genauso berechnet wie bei einem echten Trade, damit das
    Ergebnis in R direkt vergleichbar ist.
    """
    alle  = _lade_trades()
    stats = get_statistik()
    sl_pct = _validiere_prozent(signal.get("stopLoss"), SL_PROZENT)
    tp_pct = _validiere_prozent(signal.get("takeProfit"), TP_PROZENT)
    mm = berechne_einsatz(
        modus=signal.get("mm_modus", MM_MODUS), kapital=stats["aktuelles_kapital"],
        ctx={"confidence": signal.get("confidence", 0),
             "win_rate": stats["statistik"]["win_rate"],
             "gesamt_abgeschlossen": stats["statistik"]["gewonnen"] + stats["statistik"]["verloren"],
             "sl_pct": sl_pct, "tp_pct": tp_pct,
             "volatility_pct": signal.get("volatility_pct", 0),
             "letzte_trades": stats.get("letzte_trades", [])},
    )
    einsatz = round(float(mm["einsatz"]), 2)
    rr  = round(tp_pct / sl_pct, 2) if sl_pct > 0 else 0
    now = jetzt()
    grund = str(signal.get("vetoGrund") or "").strip()
    if signal.get("vetoEreignis"):
        grund += f" ({signal['vetoEreignis']})"
    zeile = {
        "Datum": now.strftime("%d.%m.%Y"), "Uhrzeit": now.strftime("%H:%M:%S"),
        "ID": _naechste_id(alle, "V"),
        "Asset": str(signal.get("asset", "")).strip(),
        "Action": str(signal.get("action", "")).upper(),
        "Richtung": str(signal.get("direction", "")).upper(),
        "Konfidenz": int(_safe_float(signal.get("confidence", 0))),
        "Einsatz": einsatz, "SL %": sl_pct, "TP %": tp_pct,
        "SL Absolut": einsatz, "TP Absolut": round(einsatz * rr, 2), "R:R": rr,
        "Entry-Price": _safe_float(signal.get("entry_price", 0)),
        "Aktuell": 0.0, "P&L": 0.0, "Status": "veto_offen",
        "Geöffnet am": now.isoformat(), "Geschlossen am": "",
        "Zusammenfassung": signal.get("summary", ""),
        "Score": signal.get("sessionScore", 0),
        "Strategie": signal.get("strategyUsed", ""),
        "MM-Modus": mm["modus"], "MM-Begründung": "hypothetisch (Veto)",
        "Signalquelle": signal.get("signalQuelle") or "regeln",
        "Veto-Grund": grund[:250], "Veto-P&L": 0.0,
    }
    alle.append(zeile)
    _speichere_trades(alle)
    log.info(f"⛔ Veto protokolliert: {zeile['ID']} | {zeile['Asset']} {zeile['Action']} | "
             f"Entry {zeile['Entry-Price']} | Grund: {grund[:80]}")
    return zeile


@_synchronized
def veto_schliessen(veto_id: str, ergebnis: str, pnl: float, kurs: float = 0.0) -> dict:
    """Schließt eine V-Zeile rein rechnerisch (Finanzierung wie bei echten Trades)."""
    alle = _lade_trades()
    for t in alle:
        if t.get("ID") == veto_id and t.get("Status") == "veto_offen":
            zu = jetzt()
            kosten = finanzierungskosten(_safe_float(t.get("Einsatz")), _safe_float(t.get("SL %")),
                                         t.get("Asset"), t.get("Geöffnet am"), zu.isoformat())
            netto = round(float(pnl) - kosten, 2)
            if ergebnis == "gewonnen" and netto < 0:
                ergebnis = "verloren"
            t["Status"] = f"veto_{ergebnis}"
            t["Veto-P&L"] = netto
            t["Finanzierung"] = kosten
            t["Aktuell"] = round(float(kurs or 0), 5)
            t["Geschlossen am"] = zu.isoformat()
            _speichere_trades(alle)
            log.info(f"⛔ Veto {veto_id} ausgewertet: hätte {'+' if netto >= 0 else ''}€{netto:.2f} gebracht")
            return t
    return {}


@_synchronized
def get_offene_vetos() -> list:
    return [t for t in _lade_trades() if t.get("Status") == "veto_offen"]


@_synchronized
def get_offene_trades() -> list:
    """Gibt alle offenen Trades zurück."""
    trades = _lade_trades()
    return [t for t in trades if t.get("Status") == "offen"]


@_synchronized
def tages_snapshot():
    """Loggt täglichen Kapital-Snapshot (Kurve kommt aus den Trades selbst)."""
    stats = get_statistik()
    r = stats["risiko"]
    log.info(f"📊 Snapshot: {jetzt().strftime('%d.%m.%Y')} | €{stats['aktuelles_kapital']:.2f} "
             f"| DD {r['aktueller_drawdown']:.1f}% | offen {r['offene_exposure_pct']:.1f}% | {r['stufe']}")


@_synchronized
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
    r = stats["risiko"]
    schutz = "🟢 normal" if r["stufe"] == "NORMAL" else f"🛡️ {r['stufe']}"
    sv = stats.get("signalvergleich", {})
    veto_zeile = ""
    if sv.get("vetos_gesamt"):
        veto_zeile = (f"⛔ KI-Vetos: {sv['vetos_gesamt']} ({sv['vetos_offen']} laufen noch, "
                      f"{sv['vetos_waeren_gewonnen']} wären gewonnen, {sv['vetos_waeren_verloren']} verloren)\n")

    return (
        f"📊 *TRADING DEMO - TAGESREPORT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Kapital: *€{kapital:.2f}*\n"
        f"{'📈' if pnl >= 0 else '📉'} Gesamt P&L: *{'+' if pnl >= 0 else ''}€{pnl:.2f}* "
        f"({'+' if roi >= 0 else ''}{roi:.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Win Rate: *{wr:.1f}%*\n"
        f"✅ Gewonnen: *{gewon}* | ❌ Verloren: *{verl}* | ⚖️ BE: {stats['statistik'].get('breakeven', 0)}\n"
        f"🔄 Offen: *{offen}* (€{r['offene_exposure']:.0f} = {r['offene_exposure_pct']:.1f}%)\n"
        f"📉 Drawdown: aktuell {r['aktueller_drawdown']:.1f}% | max {r['max_drawdown']:.1f}%\n"
        f"🛡️ Schutz: {schutz}\n"
        f"{veto_zeile}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Startkapital: €{start:.2f}\n"
        f"🤖 _Trading Multi-Agent v3.3 · Regeln + KI-Veto_"
    )
