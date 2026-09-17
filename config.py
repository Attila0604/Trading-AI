"""
config.py — Zentrale Konfiguration.
────────────────────────────────────────────────────────────
EINE Quelle der Wahrheit für alle Einstellungen. Vorher lasen
main.py, demo_tracker.py und excel_tracker.py dieselben Env-Variablen
mit UNTERSCHIEDLICHEN Defaults (z.B. MAX_RISK_PCT 5 vs 2) — fehlte
eine Variable, rechneten die Module verschieden.

Nutzung:
    from config import MAX_RISK_PCT, STOP_LOSS_PCT, DATA_DIR, jetzt
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo


def _f(name: str, default: float) -> float:
    """Float aus Env, robust gegen leere/kaputte Werte."""
    try:
        v = os.getenv(name, "").strip()
        return float(v) if v else default
    except (ValueError, TypeError):
        return default


def _i(name: str, default: int) -> int:
    try:
        v = os.getenv(name, "").strip()
        return int(float(v)) if v else default
    except (ValueError, TypeError):
        return default


def _b(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in ("true", "1", "yes", "ja")


def _s(name: str, default: str) -> str:
    v = os.getenv(name, "").strip()
    return v if v else default


# ─── Zeit ────────────────────────────────────────────────────
# Railway läuft in UTC. Bisher standen im Excel UTC-Zeiten (05:03),
# während der Zeitplan auf Wien (07:00) lief. Ab jetzt rechnet ALLES
# in Wiener Zeit: jetzt() liefert eine "naive" Wien-Zeit, damit die
# bestehenden ISO-Strings im Excel weiter vergleichbar bleiben.
ZEITZONE = _s("ZEITZONE", "Europe/Vienna")


def jetzt() -> datetime:
    """Aktuelle Zeit in Wiener Zeit (ohne tzinfo, für Excel/Vergleiche)."""
    return datetime.now(ZoneInfo(ZEITZONE)).replace(tzinfo=None)


# ─── Speicherort ─────────────────────────────────────────────
DATA_DIR          = _s("DATA_DIR", "/app/data")

# ─── Risiko & Trade-Parameter (EINHEITLICH für alle Module) ──
MAX_RISK_PCT      = _f("MAX_RISK_PCT", 2.0)
STOP_LOSS_PCT     = _f("STOP_LOSS_PCT", 1.5)
TAKE_PROFIT_PCT   = _f("TAKE_PROFIT_PCT", 3.0)
POSITION_SIZE     = _f("POSITION_SIZE_EUR", 1000.0)
MIN_CONFIDENCE    = _i("MIN_CONFIDENCE", 70)
# Max. Haltedauer, danach wird zum Marktpreis geschlossen
MAX_TRADE_TAGE    = _f("MAX_TRADE_TAGE", 14.0)

# ─── Portfolio-Schutz (NEU, greift im Backend) ───────────────
# Drawdown wird vom letzten Kapital-Hoch gemessen (AKTUELLER DD, nicht
# der historische Max-DD). Über DD_PAUSE_PCT werden KEINE neuen Trades
# geöffnet, zwischen DD_VORSICHT_PCT und DD_PAUSE_PCT wird der Einsatz
# mit DD_VORSICHT_FAKTOR multipliziert (0.5 = halbiert).
DD_PAUSE_PCT        = _f("DD_PAUSE_PCT", 10.0)
DD_VORSICHT_PCT     = _f("DD_VORSICHT_PCT", 5.0)
DD_VORSICHT_FAKTOR  = _f("DD_VORSICHT_FAKTOR", 0.5)
# Summe aller offenen Einsätze darf diesen Anteil des Kapitals nicht
# überschreiten (vorher: unbegrenzt, real waren 33% offen).
MAX_EXPOSURE_PCT    = _f("MAX_EXPOSURE_PCT", 15.0)
# Break-even-Stop: Läuft ein Trade nach X Tagen im Plus (aber ohne TP),
# wird der Stop auf den Entry gezogen. Fällt der Kurs zurück auf den Entry,
# wird mit P&L 0 ("breakeven") geschlossen statt später am SL zu verlieren
# oder 14 Tage Kapital zu binden. 0 = aus.
BREAKEVEN_NACH_TAGEN = _f("BREAKEVEN_NACH_TAGEN", 4.0)
# Harte Obergrenze für gleichzeitig offene Demo-Trades
MAX_OFFENE_TRADES   = _i("MAX_OFFENE_TRADES", 6)
# Nur ein offener Trade pro Asset (verhindert Long UND Short gleichzeitig)
EIN_TRADE_PRO_ASSET = _b("EIN_TRADE_PRO_ASSET", True)

# ─── Demo-Simulation ─────────────────────────────────────────
DEMO_STARTKAPITAL = _f("DEMO_STARTKAPITAL", 1000.0)
MM_MODUS          = _s("MM_MODUS", "fixed_percent")

# ─── Handel & Sicherheit ─────────────────────────────────────
AUTO_TRADE        = _b("AUTO_TRADE", False)
ORDERS_ENABLED    = _b("ORDERS_ENABLED", False)   # harte Order-Sperre
CAPITAL_DEMO      = _b("CAPITAL_DEMO", True)
API_TOKEN         = _s("API_TOKEN", "")

# ─── Sonstiges ───────────────────────────────────────────────
TRADING_ASSETS    = [a.strip() for a in _s("TRADING_ASSETS", "EUR/USD,BTC/USD,XAU/USD,US500").split(",") if a.strip()]
TRADING_STRATEGY  = _s("TRADING_STRATEGY", "adaptive")
DASHBOARD_URL     = _s("DASHBOARD_URL", "https://trading-ai-production-5cca.up.railway.app")
