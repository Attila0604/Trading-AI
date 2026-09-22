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


def utc_nach_lokal(dt_utc: datetime) -> datetime:
    """
    Naive UTC-Zeit -> naive Wiener Zeit.

    Capital.com liefert Kerzen mit `snapshotTimeUTC`, die Trades stehen seit
    v3.1 in Wiener Zeit im Excel. Ohne diese Umrechnung wurden beim SL/TP-Scan
    die ersten zwei Stunden nach Eröffnung übersprungen - und nach der
    Zeitumstellung wäre daraus eine Stunde geworden, also ein wandernder Fehler.
    """
    return dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(ZEITZONE)).replace(tzinfo=None)


# ─── Speicherort ─────────────────────────────────────────────
DATA_DIR          = _s("DATA_DIR", "/app/data")

# ─── Risiko & Trade-Parameter (EINHEITLICH für alle Module) ──
MAX_RISK_PCT      = _f("MAX_RISK_PCT", 2.0)
STOP_LOSS_PCT     = _f("STOP_LOSS_PCT", 1.5)
TAKE_PROFIT_PCT   = _f("TAKE_PROFIT_PCT", 3.0)
POSITION_SIZE     = _f("POSITION_SIZE_EUR", 1000.0)
MIN_CONFIDENCE    = _i("MIN_CONFIDENCE", 70)
# Max. Haltedauer, danach wird zum Marktpreis geschlossen.
# 45 Tage statt 14: bei Wochen-Haltedauer war der alte Timeout ein Abwürgen -
# er hat systematisch die Gewinner gekappt (Verlierer treffen ihren SL nach
# Stunden, Gewinner brauchen Wochen). Dadurch lag das effektive R:R bei 0.96
# statt der geplanten 1.5.
MAX_TRADE_TAGE    = _f("MAX_TRADE_TAGE", 45.0)

# ─── Volatilitäts-adaptive SL/TP (ATR) ───────────────────────
# Ein fester Stop bedeutet bei jedem Asset etwas anderes: 2% sind bei
# EUR/USD (~0.5% Tagesbewegung) rund 4 Tagesbewegungen weit weg, bei
# BTC (~3%) dagegen WENIGER als eine - der Trade wird vom normalen
# Rauschen ausgestoppt, bevor die These überhaupt eine Chance hat.
# Deshalb: SL/TP als Vielfaches des Tages-ATR je Asset.
#
# WICHTIG: Ein weiterer Stop kostet KEIN zusätzliches Risiko in Euro.
# Der Einsatz IST der maximale Verlust; sl_pct bestimmt nur die
# Preisdistanz und damit die (kleinere) Positionsgröße.
VOLA_ADAPTIV      = _b("VOLA_ADAPTIV", True)
ATR_SL_FAKTOR     = _f("ATR_SL_FAKTOR", 3.0)    # SL = 3 x Tages-ATR (~1.5 Wochen-ATR)
ATR_TP_FAKTOR     = _f("ATR_TP_FAKTOR", 6.0)    # TP = 6 x Tages-ATR  -> R:R 2.0
ATR_SL_MIN_PCT    = _f("ATR_SL_MIN_PCT", 1.0)   # Untergrenze, gegen Spread-Rauschen
ATR_SL_MAX_PCT    = _f("ATR_SL_MAX_PCT", 10.0)  # Obergrenze, gegen ATR-Ausreißer

# ─── Übernacht-Finanzierung (CFD-Swap) ───────────────────────
# Bei Wochen-Haltedauer kein Rundungsfehler mehr: auf das NOMINALE
# Volumen (= Einsatz / SL%) fallen pro Kalendertag Finanzierungskosten
# an. Demo und Backtest haben die bisher komplett ignoriert und waren
# dadurch systematisch zu optimistisch.
# Werte sind Richtwerte in % pro Tag - bei Bedarf an die echten
# Capital.com-Sätze anpassen.
FINANZIERUNG_AN   = _b("FINANZIERUNG_AN", True)
FINANZIERUNG_SAETZE = {
    "EUR/USD": _f("SWAP_FX_PCT",     0.015),
    "US500":   _f("SWAP_INDEX_PCT",  0.015),
    "XAU/USD": _f("SWAP_GOLD_PCT",   0.020),
    "BTC/USD": _f("SWAP_CRYPTO_PCT", 0.060),
    "ETH/USD": _f("SWAP_CRYPTO_PCT", 0.060),
}
FINANZIERUNG_STANDARD = _f("SWAP_STANDARD_PCT", 0.020)

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
# Break-even-Stop: Läuft ein Trade nach X Tagen WEIT GENUG im Plus, wird
# der Stop nachgezogen. Fällt der Kurs danach zurück, wird mit dem dann
# gesicherten Gewinn geschlossen statt am SL zu verlieren. 0 = aus.
#
# 14 statt 4 Tage und eine echte Mindestschwelle: mit "nach 4 Tagen, wenn
# irgendein Plus da ist" wurde der Stop schon bei +0.63 EUR (vier Pips,
# also Rauschen) scharfgeschaltet und der Trade 3.5 Stunden später von
# einer Zwei-Pip-Bewegung getötet. Vier von vier Auslösungen endeten bei
# exakt 0 - eine Null-Fabrik.
BREAKEVEN_NACH_TAGEN = _f("BREAKEVEN_NACH_TAGEN", 14.0)
# Mindestgewinn in R (Vielfaches des Einsatzes), bevor überhaupt
# nachgezogen wird. 1.0 = der Trade muss die volle SL-Distanz im Plus sein.
BREAKEVEN_AB_R       = _f("BREAKEVEN_AB_R", 1.0)
# Wo der Stop dann liegt, in R vom Entry aus gerechnet. 0.3 = es werden
# 30% des Einsatzes als Gewinn gesichert, statt exakt auf dem Entry zu
# sitzen (wo jedes normale Zurücklaufen ihn sofort auslöst).
BREAKEVEN_STOP_R     = _f("BREAKEVEN_STOP_R", 0.3)
# Harte Obergrenze für gleichzeitig offene Demo-Trades
MAX_OFFENE_TRADES   = _i("MAX_OFFENE_TRADES", 6)
# Nur ein offener Trade pro Asset (verhindert Long UND Short gleichzeitig)
EIN_TRADE_PRO_ASSET = _b("EIN_TRADE_PRO_ASSET", True)
# Korrelations-Deckel: EUR/USD, Gold, US500 und BTC sind in einer
# Risk-on/Risk-off-Welt EINE Wette mit vier Tickets. Im September standen
# vier Shorts gleichzeitig offen - als der Markt drehte, verloren alle
# zusammen. Deshalb: max. N offene Positionen in dieselbe Richtung.
MAX_GLEICHE_RICHTUNG = _i("MAX_GLEICHE_RICHTUNG", 2)

# ─── Demo-Simulation ─────────────────────────────────────────
DEMO_STARTKAPITAL = _f("DEMO_STARTKAPITAL", 1000.0)
# fixed_percent als Default: die LLM-Konfidenz ist in den bisherigen Daten
# ANTI-korreliert (70-79% -> +188 EUR, 80-89% -> -54 EUR, 90%+ -> -81 EUR).
# confidence_scaled setzt damit systematisch am meisten auf die schlechtesten
# Trades - eine Regel mit negativem Erwartungswert.
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
