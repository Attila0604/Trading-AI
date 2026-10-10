import pytest

from money_management import berechne_einsatz, get_modi, MODI, ABSOLUTE_MAX_PCT, _streak


def test_alle_modi_liefern_gueltigen_einsatz():
    ctx = {"confidence": 80, "win_rate": 55, "gesamt_abgeschlossen": 40,
           "sl_pct": 2, "tp_pct": 4, "volatility_pct": 3, "streak": 2}
    for modus in MODI:
        r = berechne_einsatz(modus, 1000.0, ctx)
        assert 1.0 <= r["einsatz"] <= 1000 * ABSOLUTE_MAX_PCT / 100, modus
        assert r["modus"] == modus


def test_fixed_percent_default_zwei_prozent():
    assert berechne_einsatz("fixed_percent", 1000)["einsatz"] == 20.0


def test_unbekannter_modus_faellt_auf_default():
    assert berechne_einsatz("gibtsnicht", 1000)["modus"] == "fixed_percent"


def test_kelly_braucht_historie_und_edge():
    wenig = berechne_einsatz("kelly", 1000, {"gesamt_abgeschlossen": 5})
    assert wenig["risk_pct_effektiv"] == 2.0
    kein_edge = berechne_einsatz("kelly", 1000, {"gesamt_abgeschlossen": 50, "win_rate": 20,
                                                  "sl_pct": 2, "tp_pct": 2})
    assert kein_edge["risk_pct_effektiv"] == 2.0
    edge = berechne_einsatz("kelly", 1000, {"gesamt_abgeschlossen": 50, "win_rate": 60,
                                             "sl_pct": 2, "tp_pct": 4})
    # f* = 0.6 - 0.4/2 = 0.4, halbiert 20%, gedeckelt auf 10%
    assert edge["risk_pct_effektiv"] == 10.0


def test_volatility_invers():
    ruhig = berechne_einsatz("volatility", 1000, {"volatility_pct": 1.0})["einsatz"]
    wild = berechne_einsatz("volatility", 1000, {"volatility_pct": 8.0})["einsatz"]
    assert ruhig > wild


def test_streak():
    assert _streak([{"Status": "gewonnen"}, {"Status": "gewonnen"}, {"Status": "verloren"}]) == 2
    assert _streak([{"Status": "verloren"}, {"Status": "verloren"}]) == -2
    assert _streak([]) == 0


def test_anti_martingale_reagiert_auf_serie():
    plus = berechne_einsatz("anti_martingale", 1000, {"streak": 4})["einsatz"]
    minus = berechne_einsatz("anti_martingale", 1000, {"streak": -4})["einsatz"]
    assert plus > minus


def test_einsatz_nie_ueber_kapital_und_mindestens_ein_euro():
    # 500 € bei 10 € Kapital -> globaler Deckel 25 % greift
    assert berechne_einsatz("fixed_amount", 10, params={"amount_eur": 500})["einsatz"] == 2.5
    assert berechne_einsatz("fixed_percent", 10)["einsatz"] == 1.0


def test_get_modi_fuer_dashboard():
    ids = {m["id"] for m in get_modi()}
    assert ids == set(MODI)
