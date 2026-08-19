"""
config.py — Zentrale Konfiguration.
────────────────────────────────────────────────────────────
EINE Quelle der Wahrheit für alle Einstellungen. Vorher lasen
main.py, demo_tracker.py und excel_tracker.py dieselben Env-Variablen
mit UNTERSCHIEDLICHEN Defaults (z.B. MAX_RISK_PCT 5 vs 2) — fehlte
eine Variable, rechneten die Module verschieden.

Nutzung:
    from config import MAX_RISK_PCT, STOP_LOSS_PCT, DATA_DIR
"""

import os


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


# ─── Speicherort ─────────────────────────────────────────────
DATA_DIR          = _s("DATA_DIR", "/app/data")

# ─── Risiko & Trade-Parameter (EINHEITLICH für alle Module) ──
MAX_RISK_PCT      = _f("MAX_RISK_PCT", 2.0)
STOP_LOSS_PCT     = _f("STOP_LOSS_PCT", 1.5)
TAKE_PROFIT_PCT   = _f("TAKE_PROFIT_PCT", 3.0)
POSITION_SIZE     = _f("POSITION_SIZE_EUR", 1000.0)
MIN_CONFIDENCE    = _i("MIN_CONFIDENCE", 70)

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
