import asyncio
import math

import pytest

import agents
from conftest import kerzen_aus_closes, trend_closes
from utils.json_parser import parse_agent_json


def test_parse_json_varianten():
    assert agents.parse_json('{"a": 1}', None) == {"a": 1}
    assert agents.parse_json('Hier ist das Ergebnis: {"a": {"b": 2}} Danke', None) == {"a": {"b": 2}}
    assert agents.parse_json('Text [1, 2] Text', None) == [1, 2]
    assert agents.parse_json("kein json", "fb") == "fb"
    assert agents.parse_json("", "fb") == "fb"


def test_parse_agent_json_codeblock():
    assert parse_agent_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert parse_agent_json("nix") is None


def test_regel_signal_aufwaertstrend():
    r = agents.regel_signal("EUR/USD", kerzen_aus_closes(trend_closes()))
    assert r["signal"] == "long" and r["confluence"] >= agents.REGEL_MIN_CONFLUENCE


def test_regel_pipeline_ohne_signal_ruft_keine_ki(monkeypatch):
    aufgerufen = []
    monkeypatch.setattr(agents, "veto_agent", lambda s: aufgerufen.append(s))
    # Seitwärts-Welle an einer Stelle ohne Trend (Kurs zwischen EMA20 und EMA50)
    flach = kerzen_aus_closes([100 + 2 * math.sin(i / 4) for i in range(240)])
    res = asyncio.run(agents._regel_pipeline(["EUR/USD"], {"EUR/USD": flach}))
    assert not aufgerufen
    assert res["decisions"][0]["action"] == "hold"


def test_regel_pipeline_veto_wird_angewendet(monkeypatch):
    monkeypatch.setattr(agents, "veto_agent", lambda s: {
        "pruefungen": {"EUR/USD": {"veto": True, "grund": "EZB", "ereignis": "EZB 30.10."}},
        "marktlage": "", "fehler": False})
    res = asyncio.run(agents._regel_pipeline(["EUR/USD"], {"EUR/USD": kerzen_aus_closes(trend_closes())}))
    d = res["decisions"][0]
    assert d["veto"] is True and d["status"] == "rejected" and d["direction"] == "long"


def test_veto_agent_kaputte_antwort_heisst_kein_veto(monkeypatch):
    monkeypatch.setattr(agents, "call_claude", lambda *a, **k: "Entschuldigung, keine Daten.")
    r = agents.veto_agent([{"asset": "EUR/USD", "direction": "long", "indikatoren": {}}])
    assert r["fehler"] is True and r["pruefungen"] == {}


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Schreibt das Modell das Asset anders als angefragt (z.B. 'EURUSD' statt "
    "'EUR/USD'), wird das Veto stillschweigend ignoriert und der Trade trotzdem eröffnet."))
def test_veto_robust_gegen_asset_schreibweise(monkeypatch):
    monkeypatch.setattr(agents, "call_claude", lambda *a, **k:
                        '{"pruefungen":[{"asset":"EURUSD","veto":true,"grund":"EZB","ereignis":"EZB"}],'
                        '"marktlage":""}')
    monkeypatch.setattr(agents, "veto_agent", agents.veto_agent)
    res = asyncio.run(agents._regel_pipeline(["EUR/USD"], {"EUR/USD": kerzen_aus_closes(trend_closes())}))
    assert res["decisions"][0]["veto"] is True


def test_pipeline_ueberspringt_offene_assets():
    class KeinClient:
        def is_connected(self):
            raise AssertionError("darf nicht aufgerufen werden")
    res = asyncio.run(agents.run_pipeline(["EUR/USD"], capital_client=KeinClient(),
                                          skip_assets=["EUR/USD"]))
    assert res["decisions"] == [] and res["uebersprungen"] == ["EUR/USD"]
