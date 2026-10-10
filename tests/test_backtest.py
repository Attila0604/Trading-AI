import math

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


def test_beide_trade_generatoren_liefern_dasselbe():
    """generiere_trades und trades_aus_signalen sind doppelt implementiert -
    sie müssen exakt dieselbe Trade-Folge erzeugen."""
    k = _markt()
    a = generiere_trades(k, 2.0, 4.0, 6)
    b = trades_aus_signalen(k, berechne_signale(k), 2.0, 4.0, 6)
    assert a and [(t["entry_i"], t["exit_i"], t["ergebnis"]) for t in a] == \
                 [(t["entry_i"], t["exit_i"], t["ergebnis"]) for t in b]


def test_keine_ueberlappenden_trades():
    trades = generiere_trades(_markt(), 2.0, 4.0, 6)
    for vor, nach in zip(trades, trades[1:]):
        assert nach["entry_i"] > vor["exit_i"]


def test_simuliere_mm_rechnet_konsistent():
    seq = [{"ergebnis": "gewonnen", "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0},
           {"ergebnis": "verloren", "confidence": 7, "vola": 2, "entry_i": 0, "exit_i": 0}]
    r = simuliere_mm(seq, "fixed_amount", 1000, 2.0, 4.0, params={"amount_eur": 10})
    # +20 (10 * R:R 2) - 10 = +10, keine Haltedauer -> kein Swap
    assert r["endkapital"] == 1010.0 and r["profit_factor"] == 2.0 and r["win_rate"] == 50.0


def test_vergleiche_modi_und_optimierung_laufen():
    k = _markt()
    v = vergleiche_modi(k, sl_pct=2.0, tp_pct=4.0, asset="EUR/USD")
    assert "error" not in v and len(v["ergebnisse"]) == 6
    o = optimiere_parameter(k, min_trades=3, asset="EUR/USD")
    assert "error" not in o and o["getestet"] > 0


def test_zu_wenig_kerzen():
    assert "error" in vergleiche_modi(_markt()[:30])
