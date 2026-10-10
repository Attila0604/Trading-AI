"""
Gemeinsame Test-Fixtures.

Die Module lesen ihre Konfiguration beim Import aus der Umgebung
(DATA_DIR, API-Keys ...). Deshalb wird die Umgebung HIER gesetzt, bevor
irgendein Projektmodul importiert wird - es geht nichts ins echte
/app/data, es wird keine WhatsApp verschickt und keine API angerufen.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="trading_ai_tests_")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["CALLMEBOT_PHONE"] = ""
os.environ["CALLMEBOT_APIKEY"] = ""
os.environ["ORDERS_ENABLED"] = "false"
os.environ["CAPITAL_DEMO"] = "true"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─── Kerzen-Generatoren ──────────────────────────────────────
def kerzen_aus_closes(closes, spanne_pct=0.5):
    """OHLC-Kerzen aus einer Close-Reihe (High/Low symmetrisch um den Close)."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi = max(prev, c) * (1 + spanne_pct / 200)
        lo = min(prev, c) * (1 - spanne_pct / 200)
        out.append({"time": f"2026-01-01T00:00:{i % 60:02d}", "open": prev,
                    "high": hi, "low": lo, "close": c, "volume": 0})
        prev = c
    return out


def trend_closes(n=250, start=100.0, schritt=0.004, welle=0.01):
    """Aufwärtstrend mit leichter Welle (damit RSI nicht auf 100 klebt)."""
    import math
    return [start * (1 + schritt) ** i * (1 + welle * math.sin(i / 3)) for i in range(n)]


@pytest.fixture
def frischer_tracker(tmp_path, monkeypatch):
    """demo_tracker auf eine leere, eigene Excel-Datei umbiegen."""
    import demo_tracker
    monkeypatch.setattr(demo_tracker, "EXCEL_FILE", tmp_path / "Trading_Tracker.xlsx")
    return demo_tracker
