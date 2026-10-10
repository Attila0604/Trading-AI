import math

import pytest

from indicators import sma, ema, ema_series, rsi, macd, bollinger_bands, atr, atr_pct, calculate_all_indicators
from conftest import kerzen_aus_closes, trend_closes


def test_sma_und_ema_grundwerte():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None
    # EMA einer konstanten Reihe = Konstante
    assert ema([5.0] * 30, 10) == pytest.approx(5.0)
    assert ema([1.0] * 5, 10) is None
    s = ema_series(list(range(1, 21)), 5)
    assert len(s) == 16 and s[0] == pytest.approx(3.0)


def test_rsi_extremwerte():
    steigend = [float(i) for i in range(1, 40)]
    fallend = list(reversed(steigend))
    assert rsi(steigend) == 100.0
    assert rsi(fallend) == pytest.approx(0.0)
    assert rsi([1.0] * 10) is None          # zu kurz


def test_rsi_bekannter_wert():
    # Klassisches Wilder-Beispiel (Cutler/Wilder-Daten), RSI(14) ≈ 70.5 nach 15 Werten
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert rsi(closes) == pytest.approx(70.53, abs=0.1)


def test_macd_vorzeichen_im_trend():
    m = macd(trend_closes(120, welle=0))
    assert m is not None and m["macd"] > 0
    assert macd([1.0] * 20) is None


def test_bollinger_position_und_breite():
    bb = bollinger_bands([10.0] * 19 + [20.0])
    assert bb["position"] == "breakout_up"
    flach = bollinger_bands([10.0] * 25)
    assert flach["width_pct"] == 0 and flach["position"] == "breakout_up"  # std 0 -> current >= upper


def test_atr_konstante_spanne():
    kerzen = [{"high": 101, "low": 99, "close": 100} for _ in range(20)]
    assert atr(kerzen) == pytest.approx(2.0)
    assert atr_pct(kerzen) == pytest.approx(2.0)
    assert atr(kerzen[:5]) is None


def test_atr_beruecksichtigt_luecken():
    # Gap: Vorheriger Close 100, nächste Kerze 110-111 -> TR = 11
    kerzen = [{"high": 101, "low": 99, "close": 100}] * 15 + [{"high": 111, "low": 110, "close": 110.5}]
    trs_letzte = atr(kerzen, period=1)
    assert trs_letzte == pytest.approx(11.0)


def test_calculate_all_indicators_zu_wenig_kerzen():
    assert "error" in calculate_all_indicators([])
    assert "error" in calculate_all_indicators(kerzen_aus_closes([1.0] * 10))


def test_calculate_all_indicators_aufwaertstrend_gibt_buy():
    ind = calculate_all_indicators(kerzen_aus_closes(trend_closes()))
    assert ind["trend"] == "uptrend"
    assert ind["signal"] in ("buy", "strong buy")
    assert 1 <= ind["confluenceScore"] <= 10
    assert ind["atrPct"] and ind["atrPct"] > 0


def test_confluence_bleibt_im_bereich():
    for closes in (trend_closes(), list(reversed(trend_closes())), [100.0] * 60):
        ind = calculate_all_indicators(kerzen_aus_closes(closes))
        assert 1 <= ind["confluenceScore"] <= 10


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Ein überverkaufter RSI (<30, also ein KAUF-Argument) erhöht die "
    "Confluence eines SHORT-Signals. Gegenläufige Indikatoren werden als "
    "Bestätigung gezählt."))
def test_rsi_ueberverkauft_staerkt_kein_short_signal():
    # Steiler Abwärtstrend -> RSI < 30, MACD bearish, Kurs < EMA20 < EMA50
    closes = [200 * (0.985 ** i) for i in range(120)]
    ind = calculate_all_indicators(kerzen_aus_closes(closes))
    assert ind["rsi"] < 30 and ind["trend"] == "downtrend"
    assert ind["signal"] in ("sell", "strong sell")
    # Erwartet: RSI-Gegenargument darf die Short-Confluence nicht anheben
    assert ind["confluenceScore"] <= 7


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Bei völlig flachem Kurs (keine Gewinne UND keine Verluste, z.B. eingefrorener "
    "Datenfeed) liefert rsi() 100 statt 50 -> 'sell'-Signal mit Confluence 7, also über "
    "der Handelsschwelle."))
def test_flacher_kurs_erzeugt_kein_signal():
    ind = calculate_all_indicators(kerzen_aus_closes([100.0] * 250))
    assert ind["signal"] == "neutral"


@pytest.mark.xfail(strict=True, reason=(
    "BEFUND: Spiegelbild zum RSI-Befund - ein ÜBERKAUFTER RSI (>70) erhöht die Confluence "
    "eines LONG-Signals. In einer reinen Seitwärts-Welle feuern so Long-Signale mit "
    "Confluence 8 genau am Hoch der Welle."))
def test_rsi_ueberkauft_staerkt_kein_long_signal():
    closes = [100 + 2 * math.sin(i / 4) for i in range(233)]
    ind = calculate_all_indicators(kerzen_aus_closes(closes))
    assert ind["rsi"] > 70 and ind["signal"] in ("buy", "strong buy")
    assert ind["confluenceScore"] <= 7
