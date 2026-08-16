"""
money_management.py
────────────────────────────────────────────────────────────
Wählbare Money-Management- / Positionsgrößen-Modi.

Jeder Modus bekommt denselben Kontext (Kapital + Signal + Statistik)
und liefert einen Einsatz (Stake) in EUR zurück. So kann pro Analyse
oder in der Config ein anderes Sizing gewählt werden, ohne dass die
Logik in demo_tracker/main.py verstreut ist.

Nutzung:
    from money_management import berechne_einsatz, get_modi
    res = berechne_einsatz("kelly", kapital=1234.0, ctx={...})
    einsatz = res["einsatz"]
"""

import logging

log = logging.getLogger(__name__)

# Harte Obergrenze: NIE mehr als dieser Anteil des Kapitals in EINEN Trade.
# Gilt über allen Modi - schützt vor kaputten Parametern / Kelly-Ausreißern.
ABSOLUTE_MAX_PCT = 25.0

# ── Modus-Katalog (auch fürs Dashboard-Dropdown / API) ───────────────────────
MODI = {
    "fixed_percent": {
        "name":        "Fixer Prozentsatz",
        "beschreibung": "Immer X % des aktuellen Kapitals pro Trade. Der Klassiker.",
        "risiko":      "MITTEL",
        "defaults":    {"risk_pct": 2.0},
    },
    "fixed_amount": {
        "name":        "Fixer Betrag",
        "beschreibung": "Immer derselbe Euro-Betrag, egal wie groß das Kapital ist.",
        "risiko":      "NIEDRIG",
        "defaults":    {"amount_eur": 50.0},
    },
    "confidence_scaled": {
        "name":        "Confidence-skaliert",
        "beschreibung": "Einsatz wächst linear mit der Signal-Konfidenz (min → max %).",
        "risiko":      "MITTEL",
        "defaults":    {"min_pct": 1.0, "max_pct": 5.0, "conf_min": 60, "conf_max": 95},
    },
    "kelly": {
        "name":        "Half-Kelly",
        "beschreibung": "Optimale Größe aus echter Win-Rate & R:R, halbiert (sicherer). Braucht Trade-Historie.",
        "risiko":      "VARIABEL",
        "defaults":    {"fraction": 0.5, "min_trades": 20, "cap_pct": 10.0, "fallback_pct": 2.0},
    },
    "volatility": {
        "name":        "Volatilitäts-invers",
        "beschreibung": "Kleiner Einsatz bei hoher Volatilität, größer bei ruhigem Markt.",
        "risiko":      "MITTEL",
        "defaults":    {"basis_pct": 3.0, "ziel_vola_pct": 2.0, "min_pct": 0.5, "max_pct": 6.0},
    },
    "anti_martingale": {
        "name":        "Anti-Martingale",
        "beschreibung": "Nach Gewinnen erhöhen, nach Verlusten reduzieren (mit Deckel). Reitet Serien.",
        "risiko":      "HOCH",
        "defaults":    {"basis_pct": 2.0, "step_pct": 0.5, "min_pct": 0.5, "max_pct": 8.0},
    },
}

DEFAULT_MODUS = "fixed_percent"


# ── Helfer ───────────────────────────────────────────────────────────────────
def _f(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _clamp_pct(pct: float, low: float, high: float) -> float:
    return max(low, min(pct, high))


def _streak(letzte_trades: list) -> int:
    """
    Aktuelle Serie aus den letzten (geschlossenen) Trades.
    +n = n Gewinne in Folge, -n = n Verluste in Folge, 0 = keine.
    Erwartet die Liste bereits nach Schließzeit absteigend sortiert.
    """
    streak = 0
    for t in letzte_trades:
        status = str(t.get("Status", "")).lower()
        if status == "gewonnen":
            if streak >= 0:
                streak += 1
            else:
                break
        elif status == "verloren":
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break
    return streak


def _params(modus: str, override: dict | None) -> dict:
    """Modus-Defaults mit optionalen Overrides mischen."""
    base = dict(MODI.get(modus, MODI[DEFAULT_MODUS])["defaults"])
    if override:
        for k, v in override.items():
            if v is not None:
                base[k] = v
    return base


# ── Einzelne Sizing-Modi (liefern jeweils einen %-Wert vom Kapital) ──────────
def _mode_fixed_percent(p, ctx):
    return p["risk_pct"], f"Fest {p['risk_pct']:.1f}% vom Kapital"


def _mode_fixed_amount(p, ctx):
    kapital = ctx["kapital"]
    pct = (p["amount_eur"] / kapital * 100) if kapital > 0 else 0
    return pct, f"Fester Betrag €{p['amount_eur']:.0f} (= {pct:.1f}% aktuell)"


def _mode_confidence_scaled(p, ctx):
    conf = _f(ctx.get("confidence"), 0)
    lo, hi = p["conf_min"], p["conf_max"]
    if hi <= lo:
        frac = 0.5
    else:
        frac = _clamp_pct((conf - lo) / (hi - lo), 0.0, 1.0)
    pct = p["min_pct"] + frac * (p["max_pct"] - p["min_pct"])
    return pct, f"Konfidenz {conf:.0f}% → {pct:.1f}%"


def _mode_kelly(p, ctx):
    trades = int(_f(ctx.get("gesamt_abgeschlossen"), 0))
    win_rate = _f(ctx.get("win_rate"), 0) / 100.0            # 0..1
    sl_pct = _f(ctx.get("sl_pct"), 1.5)
    tp_pct = _f(ctx.get("tp_pct"), 3.0)
    payoff = (tp_pct / sl_pct) if sl_pct > 0 else 1.0        # R = Gewinn/Verlust

    if trades < p["min_trades"]:
        return p["fallback_pct"], (
            f"Zu wenig Historie ({trades}/{p['min_trades']}) → Fallback {p['fallback_pct']:.1f}%"
        )

    # Kelly: f* = W - (1-W)/R
    kelly = win_rate - (1 - win_rate) / payoff
    if kelly <= 0:
        return p["fallback_pct"], (
            f"Kelly ≤ 0 (kein Edge, WR {win_rate*100:.0f}%, R {payoff:.2f}) → klein {p['fallback_pct']:.1f}%"
        )
    pct = _clamp_pct(kelly * p["fraction"] * 100, 0.1, p["cap_pct"])
    return pct, f"Half-Kelly (WR {win_rate*100:.0f}%, R {payoff:.2f}) → {pct:.1f}%"


def _mode_volatility(p, ctx):
    vola = _f(ctx.get("volatility_pct"), 0)
    if vola <= 0:
        # Keine Vola-Daten → auf Basis zurückfallen
        return p["basis_pct"], f"Keine Vola-Daten → Basis {p['basis_pct']:.1f}%"
    # Einsatz invers zur Vola: bei Ziel-Vola = basis_pct, bei doppelter Vola halb so groß
    pct = p["basis_pct"] * (p["ziel_vola_pct"] / vola)
    pct = _clamp_pct(pct, p["min_pct"], p["max_pct"])
    return pct, f"Vola {vola:.1f}% (Ziel {p['ziel_vola_pct']:.1f}%) → {pct:.1f}%"


def _mode_anti_martingale(p, ctx):
    streak = int(_f(ctx.get("streak"), 0))
    pct = p["basis_pct"] + streak * p["step_pct"]   # Gewinnserie erhöht, Verlustserie senkt
    pct = _clamp_pct(pct, p["min_pct"], p["max_pct"])
    lage = f"+{streak} Serie" if streak > 0 else (f"{streak} Serie" if streak < 0 else "neutral")
    return pct, f"Anti-Martingale ({lage}) → {pct:.1f}%"


_DISPATCH = {
    "fixed_percent":     _mode_fixed_percent,
    "fixed_amount":      _mode_fixed_amount,
    "confidence_scaled": _mode_confidence_scaled,
    "kelly":             _mode_kelly,
    "volatility":        _mode_volatility,
    "anti_martingale":   _mode_anti_martingale,
}


# ── Öffentliche API ──────────────────────────────────────────────────────────
def get_modi() -> list:
    """Liste aller Modi für Dashboard-Dropdown / API-Endpoint."""
    return [
        {"id": mid, "name": m["name"], "beschreibung": m["beschreibung"], "risiko": m["risiko"]}
        for mid, m in MODI.items()
    ]


def berechne_einsatz(modus: str, kapital: float, ctx: dict | None = None,
                     params: dict | None = None) -> dict:
    """
    Berechnet den Einsatz (Stake) in EUR für einen Trade.

    modus  : Schlüssel aus MODI (z.B. "kelly", "fixed_percent")
    kapital: aktuelles Demo-/Real-Kapital in EUR
    ctx    : {confidence, win_rate, gesamt_abgeschlossen, sl_pct, tp_pct,
              volatility_pct, streak, letzte_trades}
    params : optionale Parameter-Overrides für den Modus

    Returns: {einsatz, risk_pct_effektiv, modus, modus_name, begruendung}
    """
    ctx = dict(ctx or {})
    ctx["kapital"] = kapital

    if modus not in _DISPATCH:
        log.warning(f"[MM] Unbekannter Modus '{modus}' → {DEFAULT_MODUS}")
        modus = DEFAULT_MODUS

    # Streak nachziehen, falls nur letzte_trades geliefert wurde
    if "streak" not in ctx and ctx.get("letzte_trades"):
        ctx["streak"] = _streak(ctx["letzte_trades"])

    p = _params(modus, params)

    try:
        pct, begruendung = _DISPATCH[modus](p, ctx)
    except Exception as e:
        log.error(f"[MM] Fehler in Modus '{modus}': {e} → {DEFAULT_MODUS}")
        pct, begruendung = _mode_fixed_percent(_params(DEFAULT_MODUS, None), ctx)
        modus = DEFAULT_MODUS

    # Globaler Deckel + Untergrenze
    pct = _clamp_pct(pct, 0.0, ABSOLUTE_MAX_PCT)
    einsatz = round(kapital * pct / 100.0, 2)
    einsatz = max(1.0, min(einsatz, kapital))   # nie <1€, nie mehr als Kapital

    log.info(f"[MM] {modus} | Kapital €{kapital:.2f} | {pct:.2f}% → Einsatz €{einsatz:.2f} | {begruendung}")

    return {
        "einsatz":           einsatz,
        "risk_pct_effektiv": round(pct, 2),
        "modus":             modus,
        "modus_name":        MODI[modus]["name"],
        "begruendung":       begruendung,
    }
