"""
Tests für die FastAPI-App und die Pipeline-Verdrahtung in main.py.
Capital.com, Claude und WhatsApp sind durch Attrappen ersetzt.
"""
import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import main
from config import jetzt


class FakeCapital:
    def __init__(self, bid=1.0999, ask=1.1001, kerzen=None):
        self.bid, self.ask, self.kerzen = bid, ask, kerzen or []
        self.orders = []
        self.base = "https://demo-api"

    def is_connected(self):
        return True

    async def connect(self):
        return True

    async def get_prices(self, epic):
        return {"epic": epic, "bid": self.bid, "ask": self.ask}

    async def get_historical_prices(self, epic, resolution="HOUR", count=200):
        return self.kerzen

    async def create_position(self, **kw):
        self.orders.append(kw)
        return {"dealId": "REF1", "status": "success"}


@pytest.fixture
def app_env(frischer_tracker, monkeypatch, tmp_path):
    fake = FakeCapital()
    nachrichten = []
    monkeypatch.setattr(main, "capital", fake)
    monkeypatch.setattr(main, "send_whatsapp", lambda m: nachrichten.append(m) or True)
    monkeypatch.setattr(main, "API_TOKEN", "")
    monkeypatch.setitem(main.active_config, "conf", 60)
    monkeypatch.setitem(main.active_config, "mode", "semi")
    monkeypatch.setattr(main, "pipeline_running", False)
    monkeypatch.setattr(main, "ist_wochenende", lambda: False)   # Tests unabhängig vom Wochentag
    from excel_tracker import ExcelTracker
    monkeypatch.setattr(main, "tracker", ExcelTracker(str(tmp_path)))
    return fake, nachrichten, frischer_tracker


def _decision(asset="EUR/USD", richtung="long", conf=70, veto=False):
    return {"asset": asset, "action": "buy" if richtung == "long" else "sell",
            "direction": richtung, "confidence": conf, "veto": veto,
            "status": "rejected" if veto else "pending", "signalQuelle": "regeln",
            "vetoGrund": "EZB" if veto else "", "summary": "test"}


def _pipeline_mit(monkeypatch, decisions, atr=None):
    async def fake_run_pipeline(**kw):
        return {"decisions": decisions, "sessionScore": 70, "marketOverview": "",
                "signalQuelle": "regeln", "strategyUsed": "Regeln + KI-Veto",
                "agentReports": {"risk": {"approved": True}},
                "volatility": {}, "atr_pct": atr or {d["asset"]: 0.6 for d in decisions}}
    monkeypatch.setattr(main, "run_pipeline", fake_run_pipeline)


def _analyse(auto=False):
    asyncio.run(main.run_analysis_pipeline(main.AnalyzeRequest(assets=["EUR/USD"], auto_execute=auto)))


# ─── Endpoints ───────────────────────────────────────────────
def test_status_und_statistik(app_env):
    c = TestClient(main.app)
    assert c.get("/status").json()["status"] == "running"
    assert c.get("/demo/statistik").status_code == 200
    assert c.get("/demo/risiko").json()["stufe"] == "NORMAL"
    assert c.get("/mm/modi").json()["aktiv"] in main.MODI


def test_token_schuetzt_schreibende_endpoints(app_env, monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "geheim")
    c = TestClient(main.app)
    assert c.post("/demo/reset?bestaetigung=RESET").status_code == 401
    assert c.post("/demo/reset?bestaetigung=RESET", headers={"X-API-Token": "geheim"}).status_code == 200


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Auch mit gesetztem API_TOKEN sind Kontostand, Positionen und der "
    "komplette Excel-Download ohne Token abrufbar."))
def test_token_schuetzt_auch_lesende_endpoints(app_env, monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "geheim")
    app_env[2].signal_oeffnen({"asset": "EUR/USD", "action": "buy", "entry_price": 1.1})
    assert TestClient(main.app).get("/excel-download").status_code == 401


def test_reset_verweigert_bei_offenen_trades(app_env):
    app_env[2].signal_oeffnen({"asset": "EUR/USD", "action": "buy", "entry_price": 1.1})
    assert TestClient(main.app).post("/demo/reset?bestaetigung=RESET").status_code == 409


def test_markt_schliessen(app_env):
    fake, _, tracker = app_env
    tracker.signal_oeffnen({"asset": "EUR/USD", "action": "buy", "entry_price": 1.1000,
                            "stopLoss": 2.0, "takeProfit": 4.0})
    fake.bid = 1.1220                                       # +2 % = +1R
    r = TestClient(main.app).post("/demo/trade/T0001/schliessen?ergebnis=markt").json()
    assert r["ergebnis"] == "gewonnen" and r["bewertung"]["pnl"] == 20.0


# ─── Pipeline ────────────────────────────────────────────────
def test_pipeline_oeffnet_trade_mit_atr_stops(app_env, monkeypatch):
    _pipeline_mit(monkeypatch, [_decision()])
    _analyse()
    offen = app_env[2].get_offene_trades()
    assert len(offen) == 1
    assert offen[0]["SL %"] == pytest.approx(1.8) and offen[0]["TP %"] == pytest.approx(3.6)


def test_pipeline_protokolliert_veto_statt_trade(app_env, monkeypatch):
    _pipeline_mit(monkeypatch, [_decision(veto=True)])
    _analyse()
    assert app_env[2].get_offene_trades() == []
    assert len(app_env[2].get_offene_vetos()) == 1


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Default MIN_CONFIDENCE=70, Regel-Signale mit Confluence 6 haben aber "
    "Konfidenz 60. Ohne gespeicherte config.json werden sie weder gehandelt noch als "
    "Veto protokolliert - live wird also eine andere Strategie gefahren als im "
    "Backtest (min_confluence 6)."))
def test_default_konfidenz_passt_zu_regel_confluence():
    from config import MIN_CONFIDENCE
    from agents import REGEL_MIN_CONFLUENCE
    assert MIN_CONFIDENCE <= REGEL_MIN_CONFLUENCE * 10


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Short-Einstieg wird zum ASK gebucht (ask or bid für beide Richtungen). "
    "Ein Short wird aber zum Bid eröffnet - das schenkt jedem Short einen Spread."))
def test_short_einstieg_zum_bid(app_env, monkeypatch):
    _pipeline_mit(monkeypatch, [_decision(richtung="short")])
    _analyse()
    assert app_env[2].get_offene_trades()[0]["Entry-Price"] == app_env[0].bid


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: auto_execute_signals reicht 'LONG'/'SHORT' an create_position durch - "
    "Capital.com erwartet 'BUY'/'SELL'. Außerdem wird POSITION_SIZE_EUR (1000) als "
    "Kontraktgröße verschickt."))
def test_auto_trade_richtung_capital_format(app_env, monkeypatch):
    _pipeline_mit(monkeypatch, [_decision()])
    _analyse(auto=True)
    assert app_env[0].orders and app_env[0].orders[0]["direction"] in ("BUY", "SELL")


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: auto_execute_signals filtert KI-Vetos und Portfolio-Schutz nicht - "
    "ein blockiertes Signal würde (bei ORDERS_ENABLED=true) echt gehandelt."))
def test_auto_trade_respektiert_veto(app_env, monkeypatch):
    _pipeline_mit(monkeypatch, [_decision(veto=True)])
    _analyse(auto=True)
    assert app_env[0].orders == []


# ─── Ergebnis-Check (SL/TP-Erkennung) ────────────────────────
def _stundenkerze(dt_lokal, hi, lo):
    # Kerzen kommen in UTC; Wien ist im Oktober UTC+2
    utc = dt_lokal - timedelta(hours=2)
    return {"time": utc.strftime("%Y-%m-%dT%H:%M:%S"), "high": hi, "low": lo,
            "open": lo, "close": hi}


def test_ergebnis_check_erkennt_sl_in_kerze(app_env, monkeypatch):
    fake, _, tracker = app_env
    tracker.signal_oeffnen({"asset": "EUR/USD", "action": "buy", "entry_price": 1.1000,
                            "stopLoss": 2.0, "takeProfit": 4.0})
    alle = tracker._lade_trades()
    geoeffnet = jetzt() - timedelta(hours=10)
    alle[0]["Geöffnet am"] = geoeffnet.isoformat()
    tracker._speichere_trades(alle)
    fake.kerzen = [_stundenkerze(geoeffnet - timedelta(hours=5), 1.20, 1.00),   # VOR Eröffnung: ignorieren
                   _stundenkerze(geoeffnet + timedelta(hours=3), 1.101, 1.075)]  # SL 1.078 berührt
    fake.bid = 1.09
    asyncio.run(main._check_trade_results(tracker.get_offene_trades()))
    s = tracker.get_statistik()["statistik"]
    assert s["verloren"] == 1 and s["offen"] == 0


def test_ergebnis_check_laesst_laufenden_trade_offen(app_env):
    fake, _, tracker = app_env
    tracker.signal_oeffnen({"asset": "EUR/USD", "action": "buy", "entry_price": 1.1000,
                            "stopLoss": 2.0, "takeProfit": 4.0})
    fake.kerzen, fake.bid = [], 1.105
    asyncio.run(main._check_trade_results(tracker.get_offene_trades()))
    assert tracker.get_statistik()["statistik"]["offen"] == 1
