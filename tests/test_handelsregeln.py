from datetime import datetime, timedelta

import pytest

import handelsregeln as hr

JETZT = datetime(2026, 10, 7, 12, 0)          # Mittwoch, Wien = UTC+2


def _kerze(lokal, hi, lo):
    utc = lokal - timedelta(hours=2)
    return {"time": utc.strftime("%Y-%m-%dT%H:%M:%S"), "high": hi, "low": lo}


def _bewerte(**kw):
    basis = dict(entry=100.0, action="buy", sl_pct=2.0, tp_pct=4.0, einsatz=20.0,
                 geoeffnet=JETZT - timedelta(days=1), be_seit=None, be_stop=0.0,
                 kerzen=[], kurs=100.5, jetzt_=JETZT)
    basis.update(kw)
    return hr.bewerte_position(**basis)


def test_sl_tp_aus_atr_mit_grenzen():
    assert hr.sl_tp_prozent(1.0, 1.5, 3.0, vola_adaptiv=True) == (3.0, 6.0, True)
    assert hr.sl_tp_prozent(0.1, 1.5, 3.0, vola_adaptiv=True)[0] == hr.ATR_SL_MIN_PCT
    assert hr.sl_tp_prozent(9.0, 1.5, 3.0, vola_adaptiv=True)[0] == hr.ATR_SL_MAX_PCT
    assert hr.sl_tp_prozent(None, 1.5, 3.0, vola_adaptiv=True) == (1.5, 3.0, False)
    assert hr.sl_tp_prozent(1.0, 1.5, 3.0, vola_adaptiv=False) == (1.5, 3.0, False)


def test_kerze_mit_sl_und_tp_zaehlt_als_verlust():
    assert hr.kerze_trifft("buy", 110, 90, 98, 104) == "verloren"
    assert hr.kerze_trifft("sell", 101, 95, 102, 96) == "gewonnen"
    assert hr.kerze_trifft("buy", 101, 99, 98, 104) is None


def test_r_aus_preis_gedeckelt():
    assert hr.r_aus_preis(100, 101, "buy", 2, 4) == pytest.approx(0.5)
    assert hr.r_aus_preis(100, 90, "buy", 2, 4) == -1.0
    assert hr.r_aus_preis(100, 120, "buy", 2, 4) == 2.0
    assert hr.r_aus_preis(100, 99, "sell", 2, 4) == pytest.approx(0.5)


def test_kerzen_vor_eroeffnung_werden_ignoriert():
    geo = JETZT - timedelta(hours=10)
    b = _bewerte(geoeffnet=geo, kerzen=[_kerze(geo - timedelta(hours=3), 110, 90)])
    assert b["ergebnis"] is None


def test_sl_treffer_in_kerze():
    geo = JETZT - timedelta(hours=10)
    b = _bewerte(geoeffnet=geo, kerzen=[_kerze(geo + timedelta(hours=2), 100.5, 97.9)])
    assert (b["ergebnis"], b["grund"], b["pnl"]) == ("verloren", "sl", -20.0)


def test_nachgezogener_stop_greift_vor_dem_urspruenglichen_sl():
    """Vorher: Kurs fällt durch den nachgezogenen Stop und später bis zum SL
    -> es wurde -1 R gebucht statt des gesicherten Gewinns."""
    geo = JETZT - timedelta(days=20)
    be_seit = JETZT - timedelta(hours=10)
    kerzen = [_kerze(be_seit + timedelta(hours=1), 101.0, 100.4),   # durch Stop 100.6
              _kerze(be_seit + timedelta(hours=5), 100.0, 97.5)]    # später bis unter SL 98
    b = _bewerte(geoeffnet=geo, be_seit=be_seit, be_stop=100.6, kerzen=kerzen, kurs=97.6)
    assert b["grund"] == "be" and b["ergebnis"] == "gewonnen" and b["pnl"] == 6.0   # 0.3 R


def test_timeout_schliesst_zum_marktpreis():
    b = _bewerte(geoeffnet=JETZT - timedelta(days=hr.MAX_TRADE_TAGE + 1), kurs=101.0)
    assert b["grund"] == "timeout" and b["pnl"] == 10.0 and b["ergebnis"] == "gewonnen"


def test_stop_wird_nachgezogen():
    b = _bewerte(geoeffnet=JETZT - timedelta(days=hr.BREAKEVEN_NACH_TAGEN + 1), kurs=102.5)
    assert b["ergebnis"] is None
    assert b["be_stop_neu"] == pytest.approx(100.0 * (1 + hr.BREAKEVEN_STOP_R * 2 / 100))


def test_kein_stopnachzug_unter_der_schwelle():
    b = _bewerte(geoeffnet=JETZT - timedelta(days=hr.BREAKEVEN_NACH_TAGEN + 1), kurs=101.0)
    assert b["be_stop_neu"] is None


def test_short_sl_ueber_momentanpreis():
    b = _bewerte(action="sell", kurs=102.5)
    assert b["grund"] == "sl"


def test_ohne_entry_keine_entscheidung():
    assert _bewerte(entry=0.0, kurs=50)["ergebnis"] is None
