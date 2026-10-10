from datetime import timedelta

import pytest

from config import jetzt


def _signal(asset="EUR/USD", action="buy", entry=1.1, conf=70, sl=2.0, tp=4.0, **extra):
    return {"asset": asset, "action": action, "direction": "long" if action == "buy" else "short",
            "confidence": conf, "entry_price": entry, "stopLoss": sl, "takeProfit": tp,
            "signalQuelle": "regeln", **extra}


# ─── Reine Rechenfunktionen ──────────────────────────────────
def test_pnl_aus_preis_long_short_und_deckel(frischer_tracker):
    p = frischer_tracker.pnl_aus_preis
    assert p(20, 100, 102, "buy", 2, 4) == 20.0           # +1R
    assert p(20, 100, 98, "buy", 2, 4) == -20.0           # -1R
    assert p(20, 100, 90, "buy", 2, 4) == -20.0           # gedeckelt bei -Einsatz
    assert p(20, 100, 120, "buy", 2, 4) == 40.0           # gedeckelt bei +R:R
    assert p(20, 100, 98, "sell", 2, 4) == 20.0           # Short profitiert
    assert p(20, 0, 98, "sell", 2, 4) == 0.0


def test_swap_kosten(frischer_tracker):
    # 20 € Einsatz / 2 % SL = 1000 € Nominal, 0.015 %/Tag, 10 Tage = 1.50 €
    assert frischer_tracker.swap_kosten(20, 2, "EUR/USD", 10) == 1.5
    assert frischer_tracker.swap_kosten(20, 2, "EUR/USD", 0) == 0.0
    assert frischer_tracker.swap_kosten(20, 0, "EUR/USD", 5) == 0.0


def test_drawdown_berechnung(frischer_tracker):
    kurve = [{"kapital": k} for k in (1000, 1100, 990, 1050)]
    max_dd, akt, peak = frischer_tracker._drawdowns(kurve)
    assert peak == 1100 and max_dd == 10.0 and akt == pytest.approx(4.55, abs=0.01)


# ─── Trade-Lebenszyklus ──────────────────────────────────────
def test_leerer_tracker(frischer_tracker):
    s = frischer_tracker.get_statistik()
    assert s["aktuelles_kapital"] == frischer_tracker.STARTKAPITAL
    assert s["risiko"]["neue_trades_erlaubt"] is True


def test_trade_oeffnen_und_gewinnen(frischer_tracker):
    t = frischer_tracker.signal_oeffnen(_signal())
    assert t["ID"] == "T0001" and t["Status"] == "offen"
    assert t["Einsatz"] == 20.0 and t["R:R"] == 2.0
    zu = frischer_tracker.trade_schliessen("T0001", "gewonnen")
    assert zu["Status"] == "gewonnen" and zu["P&L"] == 40.0     # sofort geschlossen -> kaum Swap
    s = frischer_tracker.get_statistik()
    assert s["aktuelles_kapital"] == 1040.0 and s["statistik"]["win_rate"] == 100.0


def test_ein_trade_pro_asset(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal())
    r = frischer_tracker.signal_oeffnen(_signal(action="sell"))
    assert r.get("stufe") == "ASSET_OFFEN"


def test_korrelationsdeckel_gleiche_richtung(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal("EUR/USD"))
    frischer_tracker.signal_oeffnen(_signal("XAU/USD", entry=2000))
    r = frischer_tracker.signal_oeffnen(_signal("US500", entry=5000))
    assert r.get("stufe") == "RICHTUNG_VOLL"
    # Gegenrichtung ist erlaubt
    assert frischer_tracker.signal_oeffnen(_signal("US500", "sell", 5000)).get("ID") == "T0003"


def test_finanzierung_macht_aus_knappem_gewinn_einen_verlust(frischer_tracker, monkeypatch):
    frischer_tracker.signal_oeffnen(_signal(asset="BTC/USD", entry=60000, sl=1.0, tp=1.0))
    alle = frischer_tracker._lade_trades()
    alle[0]["Geöffnet am"] = (jetzt() - timedelta(days=60)).isoformat()
    frischer_tracker._speichere_trades(alle)
    zu = frischer_tracker.trade_schliessen("T0001", "gewonnen")
    # 20 € / 1 % = 2000 € Nominal * 0.06 %/Tag * 60 Tage = 72 € > 20 € Gewinn
    assert zu["Status"] == "verloren" and zu["P&L"] < 0


def test_veto_zeilen_zaehlen_nicht_in_statistik(frischer_tracker):
    v = frischer_tracker.veto_protokollieren(_signal(vetoGrund="FOMC", vetoEreignis="Fed 29.10."))
    assert v["ID"] == "V0001" and v["Status"] == "veto_offen"
    t = frischer_tracker.signal_oeffnen(_signal())
    assert t["ID"] == "T0001"                      # V-Zeilen verbrauchen keine T-Nummern
    s = frischer_tracker.get_statistik()
    assert s["statistik"]["offen"] == 1 and s["signalvergleich"]["vetos_offen"] == 1
    frischer_tracker.veto_schliessen("V0001", "verloren", -20.0, 1.08)
    assert frischer_tracker.get_statistik()["signalvergleich"]["vetos_waeren_verloren"] == 1


def test_breakeven_wird_nur_einmal_gesetzt(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal())
    a = frischer_tracker.breakeven_aktivieren("T0001", 1.12)
    b = frischer_tracker.breakeven_aktivieren("T0001", 1.15)
    assert a["BE-Stop"] == b["BE-Stop"] == 1.12


def test_reset_archiviert(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal())
    frischer_tracker.trade_schliessen("T0001", "verloren")
    info = frischer_tracker.tracker_zuruecksetzen()
    assert info["trades"] == 1 and info["archiviert"].startswith("Trading_Tracker_archiv_")
    assert (frischer_tracker.EXCEL_FILE.parent / info["archiviert"]).exists()
    assert frischer_tracker.get_statistik()["statistik"]["gesamt_trades"] == 0


def _verluste_buchen(tracker, n, asset_prefix="A"):
    for i in range(n):
        tracker.signal_oeffnen(_signal(f"{asset_prefix}{i}", "buy" if i % 2 else "sell"))
        tracker.trade_schliessen(f"T{i + 1:04d}", "verloren")


def test_drawdown_pause_greift(frischer_tracker):
    _verluste_buchen(frischer_tracker, 12)         # ab -5 % halbierter Einsatz, dann > -10 %
    r = frischer_tracker.get_risiko_status()
    assert r["stufe"] == "PAUSE" and not r["neue_trades_erlaubt"]
    assert frischer_tracker.signal_oeffnen(_signal("NEU")).get("abgelehnt")


def test_drawdown_pause_hat_ausweg(frischer_tracker, monkeypatch):
    _verluste_buchen(frischer_tracker, 12)
    assert not frischer_tracker.get_risiko_status()["neue_trades_erlaubt"]
    # Nach DD_PAUSE_TAGE ohne neuen Verlust: vorsichtiger Neustart
    import demo_tracker
    spaeter = jetzt() + timedelta(days=demo_tracker.DD_PAUSE_TAGE + 1)
    monkeypatch.setattr(demo_tracker, "jetzt", lambda: spaeter)
    r = frischer_tracker.get_risiko_status()
    assert r["stufe"] == "NEUSTART" and r["einsatz_faktor"] == demo_tracker.DD_VORSICHT_FAKTOR
    t = frischer_tracker.signal_oeffnen(_signal("NEU"))
    assert t.get("ID") and "Schutz ×0.5" in t["MM-Begründung"]
    # Ein weiterer Verlust startet die Wartezeit neu
    frischer_tracker.trade_schliessen(t["ID"], "verloren")
    assert frischer_tracker.get_risiko_status()["stufe"] == "PAUSE"


def test_pause_ohne_ausweg_wenn_abgeschaltet(frischer_tracker, monkeypatch):
    import demo_tracker
    monkeypatch.setattr(demo_tracker, "DD_PAUSE_TAGE", 0.0)
    _verluste_buchen(frischer_tracker, 12)
    spaeter = jetzt() + timedelta(days=365)
    monkeypatch.setattr(demo_tracker, "jetzt", lambda: spaeter)
    assert frischer_tracker.get_risiko_status()["stufe"] == "PAUSE"


def test_max_risk_pct_wirkt_auf_einsatz(frischer_tracker, monkeypatch):
    monkeypatch.setattr(frischer_tracker, "RISIKO_PROZENT", 1.0)
    t = frischer_tracker.signal_oeffnen(_signal())
    assert t["Einsatz"] == 10.0


def test_risk_pct_aus_signal_hat_vorrang(frischer_tracker):
    t = frischer_tracker.signal_oeffnen(_signal(mm_params={"risk_pct": 3.0}))
    assert t["Einsatz"] == 30.0


# ─── Datensicherheit ─────────────────────────────────────────
def test_kaputte_datei_wird_nicht_ueberschrieben(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal())
    frischer_tracker.EXCEL_FILE.write_bytes(b"kein zip")
    with pytest.raises(Exception):
        frischer_tracker.signal_oeffnen(_signal("XAU/USD"))
    assert frischer_tracker.EXCEL_FILE.read_bytes() == b"kein zip"


def test_speichern_legt_tagesbackup_an_und_hinterlaesst_keine_tempdateien(frischer_tracker):
    frischer_tracker.signal_oeffnen(_signal())
    frischer_tracker.trade_schliessen("T0001", "gewonnen")
    ordner = frischer_tracker.EXCEL_FILE.parent
    assert len(list((ordner / "backup").glob("Trading_Tracker_*.xlsx"))) == 1
    assert not [p for p in ordner.iterdir() if p.name.startswith(".")]


def test_veto_breakeven_zaehlt_im_vergleich(frischer_tracker):
    frischer_tracker.veto_protokollieren(_signal(vetoGrund="CPI"))
    frischer_tracker.veto_schliessen("V0001", "breakeven", 0.0, 1.1)
    assert frischer_tracker.get_statistik()["signalvergleich"]["vetos_abgeschlossen"] == 1
