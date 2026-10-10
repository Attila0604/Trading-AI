"""
handelsregeln.py — Trade-Management, das Live-Check UND Backtest benutzen.
────────────────────────────────────────────────────────────────────────
Vorher standen dieselben Regeln (SL/TP aus ATR, Kerzen-Treffer, nachgezogener
Stop, Timeout) mehrfach im Code - in main.py für den Live-Check und gar nicht
im Backtest. Der Backtest hat dadurch eine andere Strategie gemessen als die,
die live lief. Alles, was über das Schicksal eines Trades entscheidet, steht
jetzt hier und wird von beiden Seiten aufgerufen.

Reine Funktionen ohne I/O - leicht zu testen.
"""

from datetime import datetime

from config import (VOLA_ADAPTIV, ATR_SL_FAKTOR, ATR_TP_FAKTOR, ATR_SL_MIN_PCT,
                    ATR_SL_MAX_PCT, BREAKEVEN_NACH_TAGEN, BREAKEVEN_AB_R,
                    BREAKEVEN_STOP_R, MAX_TRADE_TAGE, utc_nach_lokal)


def ist_long(action) -> bool:
    return str(action or "").strip().lower() in ("buy", "long")


# ─── Stop-Distanzen ──────────────────────────────────────────
def sl_tp_prozent(atr_pct, fest_sl: float, fest_tp: float,
                  sl_faktor: float = None, tp_faktor: float = None,
                  vola_adaptiv: bool = None) -> tuple:
    """
    SL/TP in % vom Entry. Mit ATR: SL = sl_faktor x Tages-ATR (begrenzt auf
    ATR_SL_MIN_PCT..ATR_SL_MAX_PCT), TP im selben Verhältnis. Ohne ATR oder bei
    abgeschaltetem VOLA_ADAPTIV: die festen Werte.
    Rückgabe: (sl_pct, tp_pct, atr_genutzt)
    """
    adaptiv = VOLA_ADAPTIV if vola_adaptiv is None else vola_adaptiv
    sl_f = ATR_SL_FAKTOR if sl_faktor is None else sl_faktor
    tp_f = ATR_TP_FAKTOR if tp_faktor is None else tp_faktor
    if adaptiv and atr_pct and sl_f > 0:
        sl = max(ATR_SL_MIN_PCT, min(ATR_SL_MAX_PCT, float(atr_pct) * sl_f))
        tp = sl * (tp_f / sl_f)
        return round(sl, 2), round(tp, 2), True
    return float(fest_sl), float(fest_tp), False


def level(entry: float, action, sl_pct: float, tp_pct: float) -> tuple:
    """Absolute (SL-Preis, TP-Preis)."""
    if ist_long(action):
        return entry * (1 - sl_pct / 100), entry * (1 + tp_pct / 100)
    return entry * (1 + sl_pct / 100), entry * (1 - tp_pct / 100)


def be_stop_preis(entry: float, action, sl_pct: float, stop_r: float = None) -> float:
    """Nachgezogener Stop: stop_r x SL-Distanz IM Gewinn (nicht exakt am Entry)."""
    r = BREAKEVEN_STOP_R if stop_r is None else stop_r
    if ist_long(action):
        return entry * (1 + r * sl_pct / 100)
    return entry * (1 - r * sl_pct / 100)


# ─── Treffer-Prüfung ─────────────────────────────────────────
def kerze_trifft(action, hi: float, lo: float, sl_level: float, tp_level: float):
    """
    "verloren" / "gewonnen" / None für eine Kerze. Liegen SL und TP beide in
    derselben Kerze, ist die Reihenfolge unbekannt -> konservativ SL.
    """
    if hi is None or lo is None:
        return None
    if ist_long(action):
        sl_hit, tp_hit = lo <= sl_level, hi >= tp_level
    else:
        sl_hit, tp_hit = hi >= sl_level, lo <= tp_level
    if sl_hit:
        return "verloren"
    if tp_hit:
        return "gewonnen"
    return None


def kurs_trifft(action, kurs: float, sl_level: float, tp_level: float):
    """Wie kerze_trifft, aber für einen einzelnen Momentanpreis."""
    return kerze_trifft(action, kurs, kurs, sl_level, tp_level)


def be_stop_getroffen(action, hi: float, lo: float, be_stop: float) -> bool:
    if hi is None or lo is None:
        return False
    return lo <= be_stop if ist_long(action) else hi >= be_stop


# ─── Ergebnis in R ───────────────────────────────────────────
def r_aus_preis(entry: float, exit_preis: float, action, sl_pct: float, tp_pct: float) -> float:
    """
    Ergebnis in R (Vielfaches des Einsatzes) beim Schließen zu exit_preis.
    Gedeckelt zwischen -1 R und +R:R - dieselbe Rechnung wie pnl_aus_preis.
    """
    if entry <= 0 or sl_pct <= 0:
        return 0.0
    move = (exit_preis - entry) / entry * 100
    if not ist_long(action):
        move = -move
    return max(-1.0, min(move / sl_pct, tp_pct / sl_pct))


# ─── Zeitregeln ──────────────────────────────────────────────
def be_aktivieren(alter_tage: float, unreal_r: float,
                  nach_tagen: float = None, ab_r: float = None) -> bool:
    """Stop nachziehen? Erst nach X Tagen und ab einer Mindestschwelle in R."""
    tage = BREAKEVEN_NACH_TAGEN if nach_tagen is None else nach_tagen
    schwelle = BREAKEVEN_AB_R if ab_r is None else ab_r
    return tage > 0 and alter_tage > tage and schwelle > 0 and unreal_r >= schwelle


def timeout_erreicht(alter_tage: float, max_tage: float = None) -> bool:
    m = MAX_TRADE_TAGE if max_tage is None else max_tage
    return m > 0 and alter_tage > m


# ─── Bewertung einer offenen Position (Live-Check + Veto-Schatten) ───────────
def kerzen_zeit(c: dict):
    """Kerzenzeit (UTC von Capital.com) als naive Wiener Zeit, None wenn unlesbar."""
    try:
        roh = datetime.fromisoformat(str(c.get("time", "")).replace("Z", "").split(".")[0])
        return utc_nach_lokal(roh)
    except Exception:
        return None


def bewerte_position(entry: float, action, sl_pct: float, tp_pct: float, einsatz: float,
                     geoeffnet, be_seit, be_stop, kerzen: list, kurs, jetzt_: datetime) -> dict:
    """
    Entscheidet für EINE offene Position, was jetzt passiert. Keine Seiteneffekte.

    kerzen : Stundenkerzen (UTC-Zeiten), werden ab Eröffnung in Zeitfolge geprüft
    kurs   : Momentanpreis der Schließ-Seite (Long: Bid, Short: Ask)

    Ist der Stop nachgezogen, gilt ab BE-Seit der nachgezogene Stop als Stop.
    Vorher wurde zuerst gegen den URSPRÜNGLICHEN SL gescannt: fiel der Kurs
    zwischen zwei Checks erst durch den nachgezogenen Stop und dann bis zum SL,
    wurde -1 R gebucht statt des gesicherten Gewinns.

    Rückgabe: {ergebnis, pnl, exit, grund (tp|sl|be|timeout), be_stop_neu,
               alter_tage, unreal_r}
    """
    out = {"ergebnis": None, "pnl": None, "exit": None, "grund": None,
           "be_stop_neu": None, "alter_tage": 0.0, "unreal_r": None}
    alter = (jetzt_ - geoeffnet).total_seconds() / 86400 if geoeffnet else 0.0
    out["alter_tage"] = alter
    if not entry or entry <= 0 or sl_pct <= 0:
        return out

    sl_level, tp_level = level(entry, action, sl_pct, tp_pct)
    be_aktiv = be_seit is not None
    if be_aktiv and not be_stop:
        be_stop = entry                     # Alt-Trades ohne gespeichertes Stop-Niveau

    def schluss(grund, preis):
        if grund == "tp":
            r, erg = tp_pct / sl_pct, "gewonnen"
        elif grund == "sl":
            r, erg = -1.0, "verloren"
        else:
            r = r_aus_preis(entry, preis, action, sl_pct, tp_pct)
            erg = "gewonnen" if r > 0 else ("breakeven" if grund == "be" else "verloren")
        out.update(ergebnis=erg, pnl=round(einsatz * r, 2), exit=preis, grund=grund)
        return out

    # Nur Kerzen AB Eröffnung: die Historie davor enthält fast sicher irgendwo
    # einen SL- oder TP-Durchbruch. Kerzen ohne lesbare Zeit werden mitgeprüft.
    for c in kerzen or []:
        ct = kerzen_zeit(c)
        if ct is not None and geoeffnet is not None and ct < geoeffnet:
            continue
        be_hier = be_aktiv and ct is not None and ct >= be_seit
        stop = be_stop if be_hier else sl_level
        t = kerze_trifft(action, c.get("high"), c.get("low"), stop, tp_level)
        if t == "gewonnen":
            return schluss("tp", tp_level)
        if t == "verloren":
            return schluss("be", stop) if be_hier else schluss("sl", sl_level)

    if kurs and kurs > 0:
        stop = be_stop if be_aktiv else sl_level
        t = kurs_trifft(action, kurs, stop, tp_level)
        if t == "gewonnen":
            return schluss("tp", tp_level)
        if t == "verloren":
            return schluss("be", stop) if be_aktiv else schluss("sl", sl_level)
        out["unreal_r"] = r_aus_preis(entry, kurs, action, sl_pct, tp_pct)
        if timeout_erreicht(alter):
            return schluss("timeout", kurs)
        if not be_aktiv and be_aktivieren(alter, out["unreal_r"]):
            out["be_stop_neu"] = be_stop_preis(entry, action, sl_pct)
    return out
