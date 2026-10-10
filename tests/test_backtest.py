import math

import pytest

import handelsregeln as hr
from backtest import (berechne_signale, trades_aus_signalen, generiere_trades,
                      simuliere_mm, vergleiche_modi, optimiere_parameter)
from conftest import kerzen_aus_closes


def _markt(n=400):
    # Zwei Trendphasen + Seitwärts, damit Long- und Short-Signale entstehen
    closes = []
    p = 100.0
    for i in range(n):
        drift = 0.004 if (i // 100) % 2 == 0 else -0.004
        p *= 1 + drift + 0.012 * math.sin(i / 2.5)
        closes.append(p)
    return kerzen_aus_closes(closes, spanne_pct=1.5)


def _bar(hi, lo, close, spread=0.0):
    return {"high": hi, "low": lo, "close": close, "spread": spread}


LONG_SIGNAL = [{"i": 0, "action": "long", "conf": 7, "vola": 1, "atr_pct": 1.0, "close": 100.0}]


def test_trades_tragen_r_und_ausstiegsgrund():
    trades = generiere_trades(_markt(), 2.0, 4.0, 6, stops="fest")
    assert trades
    for t in trades:
        assert t["grund"] in ("tp", "sl", "be", "timeout")
        assert -1.0 <= t["r"] <= 2.0


def test_keine_ueberlappenden_trades():
    trades = generiere_trades(_markt(), 2.0, 4.0, 6, stops="atr")
    for vor, nach in zip(trades, trades[1:]):
        assert nach["entry_i"] > vor["exit_i"]


def test_atr_stops_je_trade():
    trades = generiere_trades(_markt(), stops="atr")
    assert trades
    for t in trades:
        assert hr.ATR_SL_MIN_PCT <= t["sl_pct"] <= hr.ATR_SL_MAX_PCT
        assert t["tp_pct"] / t["sl_pct"] == pytest.approx(hr.ATR_TP_FAKTOR / hr.ATR_SL_FAKTOR, rel=0.01)


def test_einstieg_mit_spread():
    kerzen = [_bar(100.1, 99.9, 100.0, spread=0.2)] + [_bar(100.2, 99.9, 100.0)] * 5
    long_ = trades_aus_signalen(kerzen + [_bar(120, 100, 110)], LONG_SIGNAL, stops="atr")
    assert long_[0]["entry"] == pytest.approx(100.1)                 # Ask
    short_sig = [dict(LONG_SIGNAL[0], action="short")]
    short = trades_aus_signalen(kerzen + [_bar(110, 80, 90)], short_sig, stops="atr")
    assert short[0]["entry"] == pytest.approx(99.9)                  # Bid


def test_nachgezogener_stop_im_backtest():
    # SL 3 % (97), TP 6 % (106). 15 Tage bei 103.5 (> 1 R) -> Stop auf 100.9,
    # dann Rücksetzer auf 100.5 -> Ausstieg am nachgezogenen Stop mit +0.3 R
    kerzen = [_bar(100, 100, 100)] + [_bar(104, 103, 103.5)] * 15 + [_bar(103, 100.5, 101)]
    t = trades_aus_signalen(kerzen, LONG_SIGNAL, stops="atr")[0]
    assert t["grund"] == "be" and t["r"] == pytest.approx(hr.BREAKEVEN_STOP_R)


def test_ohne_management_kein_stopnachzug():
    kerzen = [_bar(100, 100, 100)] + [_bar(104, 103, 103.5)] * 15 + [_bar(103, 96, 97)]
    t = trades_aus_signalen(kerzen, LONG_SIGNAL, stops="atr", management=False)[0]
    assert t["grund"] == "sl" and t["r"] == -1.0


def test_timeout_im_backtest():
    kerzen = [_bar(100, 100, 100)] + [_bar(101.2, 100.8, 101.0)] * 60
    t = trades_aus_signalen(kerzen, LONG_SIGNAL, stops="atr")[0]
    assert t["grund"] == "timeout" and t["r"] == pytest.approx(1 / 3, abs=1e-3)
    assert t["exit_i"] - t["entry_i"] == int(hr.MAX_TRADE_TAGE) + 1


def test_simuliere_mm_rechnet_konsistent():
    seq = [{"ergebnis": "gewonnen", "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0},
           {"ergebnis": "verloren", "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0}]
    r = simuliere_mm(seq, "fixed_amount", 1000, 2.0, 4.0, params={"amount_eur": 10})
    # +20 (10 * R:R 2) - 10 = +10, keine Haltedauer -> kein Swap
    assert r["endkapital"] == 1010.0 and r["profit_factor"] == 2.0 and r["win_rate"] == 50.0


def test_simuliere_mm_nutzt_r_je_trade():
    seq = [{"ergebnis": "gewonnen", "r": 0.3, "sl_pct": 3.0, "tp_pct": 6.0,
            "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0}]
    r = simuliere_mm(seq, "fixed_amount", 1000, params={"amount_eur": 10})
    assert r["endkapital"] == 1003.0


def test_risk_pct_wirkt_im_backtest():
    seq = [{"ergebnis": "verloren", "r": -1.0, "sl_pct": 2.0, "tp_pct": 4.0,
            "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0}]
    assert simuliere_mm(seq, "fixed_percent", 1000, params={"risk_pct": 1.0})["endkapital"] == 990.0


def test_vergleiche_modi_und_optimierung_laufen():
    k = _markt()
    v = vergleiche_modi(k, asset="EUR/USD", stops="atr")
    assert "error" not in v and len(v["ergebnisse"]) == 6
    assert v["parameter"]["stops_text"].startswith("SL 3×ATR")
    assert sum(v["ausstiege"].values()) == v["signal_trades"]
    for stops in ("atr", "fest"):
        o = optimiere_parameter(k, min_trades=3, asset="EUR/USD", stops=stops)
        assert "error" not in o and o["getestet"] > 0


def test_zu_wenig_kerzen():
    assert "error" in vergleiche_modi(_markt()[:30])
