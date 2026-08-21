# Trading Multi-Agent v3.0 - Update 11.04.2026
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager
import asyncio, os, json, logging
from datetime import datetime

from agents import run_pipeline
from capital_client import CapitalClient
from excel_tracker import ExcelTracker
from whatsapp import send_whatsapp
from demo_tracker import (
    signal_oeffnen, trade_schliessen, tages_snapshot,
    get_offene_trades, get_statistik, generiere_tages_report, pnl_aus_preis
)
from money_management import get_modi, MODI
from backtest import vergleiche_modi, optimiere_parameter
from indicators import calculate_all_indicators

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# --- HIER WURDE DER CODE GEHÄRTET (Assets) ---
RAW_ASSETS      = os.getenv("TRADING_ASSETS", "EUR/USD,BTC/USD,XAU/USD,US500")
ASSETS          = [a.strip() for a in RAW_ASSETS.split(",") if a.strip()]
# ---------------------------------------------

STRATEGY        = os.getenv("TRADING_STRATEGY", "adaptive")
MAX_RISK_PCT    = float(os.getenv("MAX_RISK_PCT", "2"))
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "1.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
POSITION_SIZE   = float(os.getenv("POSITION_SIZE_EUR", "1000"))
AUTO_TRADE      = os.getenv("AUTO_TRADE", "false").lower() == "true"
DATA_DIR        = os.getenv("DATA_DIR", "/app/data")
MIN_CONFIDENCE  = int(os.getenv("MIN_CONFIDENCE", "70"))
MM_MODUS        = os.getenv("MM_MODUS", "fixed_percent")
# Schreibschutz: nur aktiv, wenn API_TOKEN gesetzt ist (sonst offen wie bisher)
API_TOKEN       = os.getenv("API_TOKEN", "").strip()
DASHBOARD_URL   = os.getenv("DASHBOARD_URL", "https://trading-ai-production-5cca.up.railway.app")

# Assets die am Wochenende handelbar sind
WOCHENENDE_ASSETS = {"BTC/USD", "ETH/USD"}

os.makedirs(DATA_DIR, exist_ok=True)

capital = CapitalClient()
tracker = ExcelTracker(DATA_DIR)

latest_signals   = []
latest_analysis  = {}
pipeline_running = False
schedule_log     = []

active_config = {
    "assets":   ASSETS.copy(),
    "strategy": STRATEGY,
    "risk_pct": MAX_RISK_PCT,
    "sl_pct":   STOP_LOSS_PCT,
    "tp_pct":   TAKE_PROFIT_PCT,
    "size":     POSITION_SIZE,
    "conf":     MIN_CONFIDENCE,
    "mode":     "semi",
    "mm_modus": MM_MODUS,
}

# ─── Config-Persistenz ───────────────────────────────────────────────────────
# Ohne das fällt die Config bei JEDEM Neustart auf die Code-Defaults zurück.
# Liegt im DATA_DIR (Railway-Volume), überlebt also Deployments.
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")


def config_laden():
    """Gespeicherte Config beim Start einlesen (nur bekannte Keys übernehmen)."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                gespeichert = json.load(f)
            for k, v in gespeichert.items():
                if k in active_config and v is not None:
                    active_config[k] = v
            log.info(f"⚙️ Config geladen: {CONFIG_FILE}")
        else:
            log.info("⚙️ Keine gespeicherte Config - Defaults aktiv")
    except Exception as e:
        log.error(f"Config laden fehlgeschlagen: {e} - Defaults aktiv")


def config_speichern_datei():
    """Aktuelle Config auf Platte schreiben."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(active_config, f, ensure_ascii=False, indent=2)
        log.info(f"⚙️ Config gespeichert: {CONFIG_FILE}")
        return True
    except Exception as e:
        log.error(f"Config speichern fehlgeschlagen: {e}")
        return False


config_laden()
# ─────────────────────────────────────────────────────────────────────────────


# ─── Schreibschutz für kritische Endpoints ───────────────────────────────────
# Greift NUR, wenn die Env-Variable API_TOKEN gesetzt ist. Ohne sie bleibt
# alles offen wie bisher - so kann man sich nicht versehentlich aussperren.
def pruefe_token(x_api_token: str = Header(default="")):
    if not API_TOKEN:
        return True   # Schutz nicht konfiguriert -> alles erlaubt
    if x_api_token != API_TOKEN:
        log.warning("🔒 Zugriff abgelehnt: falscher/fehlender API-Token")
        raise HTTPException(status_code=401, detail="Nicht autorisiert - API-Token fehlt oder ist falsch")
    return True
# ─────────────────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="Europe/Vienna")


# ── Wochenende Check ──────────────────────────────────────────────────────────
def ist_wochenende() -> bool:
    """Samstag=5, Sonntag=6"""
    return datetime.now().weekday() >= 5

def asset_handelbar(asset: str) -> bool:
    """Prüft ob ein Asset aktuell handelbar ist."""
    if ist_wochenende():
        handelbar = asset.upper() in WOCHENENDE_ASSETS
        if not handelbar:
            log.info(f"⚠️ Wochenende → {asset} nicht handelbar")
        return handelbar
    return True


# ── Jobs ──────────────────────────────────────────────────────────────────────
def morgen_analyse_job():
    log.info(f"🌅 Morgen-Analyse | {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    schedule_log.append({"time": datetime.now().isoformat(), "trigger": "07:00 Morgen-Analyse"})
    asyncio.run(run_analysis_pipeline(AnalyzeRequest(
        assets=active_config["assets"],
        strategy=active_config["strategy"],
        risk_pct=active_config["risk_pct"],
        sl_pct=active_config["sl_pct"],
        tp_pct=active_config["tp_pct"],
        position_size=active_config["size"],
        auto_execute=AUTO_TRADE,
    )))


def tages_report_job():
    log.info("📊 Tages-Report...")
    tages_snapshot()
    report = generiere_tages_report()
    send_whatsapp(report)


def ergebnis_check_job():
    offene = get_offene_trades()
    if not offene:
        return
    log.info(f"🔍 Ergebnis-Check: {len(offene)} offene Trades")
    asyncio.run(_check_trade_results(offene))


async def _check_trade_results(offene: list):
    """Prüft ob SL oder TP getroffen wurde via Capital.com Preise"""
    if not capital.is_connected():
        await capital.connect()

    for trade in offene:
        try:
            # Excel-Spalten-Keys (nicht lowercase!) - das war der Bug
            asset = str(trade.get("Asset", "")).strip()
            if not asset:
                continue
            epic     = asset_to_epic(asset)
            trade_id = trade.get("ID", "Unbekannt")

            entry_price = float(trade.get("Entry-Price", 0) or 0)
            action      = str(trade.get("Action", "buy")).lower()
            sl_pct      = float(trade.get("SL %", STOP_LOSS_PCT) or STOP_LOSS_PCT)
            tp_pct      = float(trade.get("TP %", TAKE_PROFIT_PCT) or TAKE_PROFIT_PCT)
            einsatz     = float(trade.get("Einsatz", 0) or 0)

            price_data    = await capital.get_prices(epic)
            current_price = price_data.get("bid") or price_data.get("ask")
            if not current_price:
                log.warning(f"Kein Preis für {asset}")
                continue
            current_price = float(current_price)

            geoeffnet = None
            try:
                geoeffnet = datetime.fromisoformat(str(trade.get("Geöffnet am", "")))
                alter_std = (datetime.now() - geoeffnet).total_seconds() / 3600
            except Exception:
                alter_std = 0.0

            log.info(f"🔍 {trade_id} | {asset} | Entry: {entry_price} | Aktuell: {current_price} | {alter_std:.1f}h")

            ergebnis     = None
            pnl_override = None

            if entry_price > 0:
                if action in ("buy", "long"):
                    tp_level = entry_price * (1 + tp_pct / 100)
                    sl_level = entry_price * (1 - sl_pct / 100)
                else:
                    tp_level = entry_price * (1 - tp_pct / 100)
                    sl_level = entry_price * (1 + sl_pct / 100)

                # ── Intraday-Erkennung: Kerzen-Highs/Lows seit Eröffnung ──────────
                # Fängt SL/TP-Treffer, die ZWISCHEN zwei 4h-Checks passiert sind.
                try:
                    kerzen = await capital.get_historical_prices(epic, "HOUR", 200)
                except Exception as ce:
                    log.warning(f"Kerzen-Fetch {asset} fehlgeschlagen: {ce}")
                    kerzen = []

                seit_open = []
                for c in kerzen:
                    ct = None
                    try:
                        ct = datetime.fromisoformat(str(c.get("time", "")).replace("Z", "").split(".")[0])
                    except Exception:
                        ct = None
                    if ct is None or geoeffnet is None or ct >= geoeffnet:
                        seit_open.append(c)
                scan = seit_open if seit_open else kerzen

                treffer = None
                for c in scan:
                    hi, lo = c.get("high"), c.get("low")
                    if hi is None or lo is None:
                        continue
                    if action in ("buy", "long"):
                        sl_hit, tp_hit = lo <= sl_level, hi >= tp_level
                    else:
                        sl_hit, tp_hit = hi >= sl_level, lo <= tp_level
                    if sl_hit and tp_hit:
                        treffer = "verloren"   # beide in einer Kerze → konservativ SL
                        break
                    if tp_hit:
                        treffer = "gewonnen"; break
                    if sl_hit:
                        treffer = "verloren"; break

                if treffer:
                    ergebnis = treffer
                    log.info(f"{'✅ TP' if treffer=='gewonnen' else '❌ SL'} (Intraday) {asset} | {len(scan)} Kerzen geprüft | {entry_price:.5f}")
                else:
                    # Fallback: Momentanpreis (falls keine Kerzendaten verfügbar)
                    if action in ("buy", "long"):
                        if current_price >= tp_level:   ergebnis = "gewonnen"
                        elif current_price <= sl_level: ergebnis = "verloren"
                    else:
                        if current_price <= tp_level:   ergebnis = "gewonnen"
                        elif current_price >= sl_level: ergebnis = "verloren"
                    if ergebnis:
                        log.info(f"{'✅ TP' if ergebnis=='gewonnen' else '❌ SL'} (Momentanpreis) {asset} | {current_price:.5f}")

            # Trades ohne Einstiegspreis (z.B. aus einer Verbindungsstörung) sind
            # nicht auswertbar -> als "abgebrochen" schließen, NICHT als Verlust
            # werten, sonst verfälschen sie die Win-Rate.
            if entry_price <= 0 and alter_std > 4:
                trade_schliessen(trade_id, "abgebrochen", 0.0)
                log.info(f"🗑️ {trade_id} ohne Entry-Price → abgebrochen (zählt nicht in der Statistik)")
                continue

            # Timeout: nach 48h zum ECHTEN Marktpreis schließen (kein Pauschal-Verlust)
            if ergebnis is None and alter_std > 48:
                pnl_override = pnl_aus_preis(einsatz, entry_price, current_price, action, sl_pct, tp_pct)
                ergebnis = "gewonnen" if pnl_override > 0 else "verloren"
                log.info(f"⏰ Trade {trade_id} nach 48h zum Marktpreis geschlossen | P&L €{pnl_override:.2f}")

            if ergebnis:
                geschlossen = trade_schliessen(trade_id, ergebnis, pnl_override)
                stats       = get_statistik()
                pnl_wert    = float(geschlossen.get("P&L", 0) or 0)
                emoji = "✅" if ergebnis == "gewonnen" else "❌"
                send_whatsapp(
                    f"{emoji} *Demo-Trade {ergebnis.upper()}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {asset} | {action.upper()}\n"
                    f"📈 Entry: {entry_price:.5f}\n"
                    f"📉 Aktuell: {current_price:.5f}\n"
                    f"💰 P&L: {'+' if pnl_wert >= 0 else ''}€{pnl_wert:.2f}\n"
                    f"💼 Kapital: €{stats['aktuelles_kapital']:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 {DASHBOARD_URL}"
                )

        except Exception as e:
            log.error(f"Ergebnis-Check Fehler [{trade.get('ID', 'Unbekannt')}]: {e}")


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(morgen_analyse_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=7, minute=0, timezone="Europe/Vienna"),
        id="morgen_analyse", replace_existing=True)
    scheduler.add_job(tages_report_job,
        trigger=CronTrigger(hour=20, minute=0, timezone="Europe/Vienna"),
        id="tages_report", replace_existing=True)
    scheduler.add_job(ergebnis_check_job,
        trigger=IntervalTrigger(hours=4, timezone="Europe/Vienna"),
        id="ergebnis_check", replace_existing=True)
    scheduler.start()

    try:
        connected = await capital.connect()
        log.info(f"Capital.com: {'✅ Verbunden' if connected else '❌ Getrennt'}")
    except Exception as e:
        log.warning(f"Capital.com Fehler: {e}")

    tages_snapshot()
    send_whatsapp(
        f"🤖 *Trading-Agent gestartet*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Analyse: täglich 07:00 Uhr\n"
        f"💹 Assets: {', '.join(active_config['assets'])}\n"
        f"🎯 Strategie: {active_config['strategy']}\n"
        f"💵 Money-Mgmt: {MODI.get(active_config['mm_modus'], {}).get('name', active_config['mm_modus'])}\n"
        f"⚡ Auto-Trade: {'AN' if AUTO_TRADE else 'AUS'}\n"
        f"🔗 Capital.com: {'✅ Verbunden' if capital.is_connected() else '❌ Getrennt'}\n"
        f"📅 Tages-Report: 20:00 Uhr\n"
        f"🔍 Ergebnis-Check: alle 4h\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 {DASHBOARD_URL}"
    )
    yield
    scheduler.shutdown()


app = FastAPI(title="Trading Multi-Agent v3.0", lifespan=lifespan)


# ── Models ────────────────────────────────────────────────────────────────────
class TradeRequest(BaseModel):
    asset: str
    direction: str
    size: float = POSITION_SIZE
    stop_loss_pct: float = STOP_LOSS_PCT
    take_profit_pct: float = TAKE_PROFIT_PCT


class AnalyzeRequest(BaseModel):
    assets: list[str] = ASSETS
    strategy: str = STRATEGY
    risk_pct: float = MAX_RISK_PCT
    sl_pct: float = STOP_LOSS_PCT
    tp_pct: float = TAKE_PROFIT_PCT
    position_size: float = POSITION_SIZE
    auto_execute: bool = AUTO_TRADE


class BacktestRequest(BaseModel):
    asset: str = None
    resolution: str = "HOUR_4"
    count: int = 500
    sl_pct: float = None
    tp_pct: float = None
    min_confluence: int = 6
    startkapital: float = 1000.0


class ConfigRequest(BaseModel):
    assets: list[str] = None
    strategy: str = None
    risk_pct: float = None
    sl_pct: float = None
    tp_pct: float = None
    size: float = None
    conf: int = None
    mode: str = None
    mm_modus: str = None


# ── Pipeline ──────────────────────────────────────────────────────────────────
async def run_analysis_pipeline(req: AnalyzeRequest):
    global latest_signals, latest_analysis, pipeline_running
    if pipeline_running:
        log.warning("Pipeline bereits aktiv")
        return
    pipeline_running = True
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        wochenende = ist_wochenende()
        log.info(f"PIPELINE START | {ts} | {req.strategy} | {req.assets} | Wochenende: {wochenende}")

        result = await run_pipeline(
            assets=req.assets, strategy=req.strategy,
            risk_pct=req.risk_pct, sl_pct=req.sl_pct,
            tp_pct=req.tp_pct, position_size=req.position_size,
            capital_client=capital,   # eine Sitzung statt zwei
        )

       # ── FIX: SL/TP IMMER mit aktiven Werten überschreiben ────────────
        # Die KI darf nicht über Risk-Settings entscheiden
        for decision in result.get("decisions", []):
            decision["stopLoss"]   = active_config["sl_pct"]
            decision["takeProfit"] = active_config["tp_pct"]
        # ─────────────────────────────────────────────────────────────────

        latest_analysis = result
        decisions       = result.get("decisions", [])
        all_signals     = [d for d in decisions if d.get("action") != "hold"]
        latest_signals  = all_signals
        tracker.save_analysis(result)

        # Demo-Trades öffnen
        trades_geoeffnet = 0
        trades_übersprungen = 0

        # ── Volatilität pro Asset aus den Tech-Reports (für volatility-Modus) ──
        # Echte Bollinger-Bandbreite aus der Pipeline (Rohindikatoren).
        vola_lookup = result.get("volatility", {}) or {}
        # ───────────────────────────────────────────────────────────────────────

        # ── Risk Guardian: greift jetzt wirklich (vorher nur kosmetisch) ──────
        risk_report = result.get("agentReports", {}).get("risk", {})
        risk_ok     = risk_report.get("approved", True)
        if not risk_ok:
            log.warning(f"🛡️ Risk Guardian NICHT freigegeben ({risk_report.get('message','')}) → keine Trades")
        # ───────────────────────────────────────────────────────────────────────

        # ── Trading-Modus durchsetzen (war bisher wirkungslos!) ──────────
        # "analyse" = nur Signale anzeigen, KEINE Demo-Trades öffnen
        modus = active_config.get("mode", "semi")
        if modus == "analyse" and all_signals:
            log.info(f"📊 Modus 'Nur Analyse': {len(all_signals)} Signal(e) angezeigt, keine Trades geöffnet")
        # ─────────────────────────────────────────────────────────────────

        for signal in all_signals:
            if not risk_ok:
                continue
            if modus == "analyse":
                trades_übersprungen += 1
                continue
            if signal.get("confidence", 0) < active_config["conf"]:
                continue

            asset = signal.get("asset", "")

            # ── Wochenende Check ──────────────────────────────────────────
            if not asset_handelbar(asset):
                trades_übersprungen += 1
                log.info(f"⏭ {asset} übersprungen → Wochenende")
                continue
            # ─────────────────────────────────────────────────────────────

            # ── Entry-Price mit Retry ─────────────────────────────────────
            entry_price = 0
            for versuch in range(3):
                try:
                    epic       = asset_to_epic(asset)
                    if not epic:
                        continue
                    price_data = await capital.get_prices(epic)
                    entry_price = float(price_data.get("ask") or price_data.get("bid") or 0)
                    if entry_price > 0:
                        log.info(f"✅ Entry-Price [{asset}]: {entry_price} (Versuch {versuch+1})")
                        break
                    await asyncio.sleep(2)
                except Exception as pe:
                    log.warning(f"Entry-Price Versuch {versuch+1} [{asset}]: {pe}")
                    await asyncio.sleep(2)

            if entry_price == 0:
                # Ohne Einstiegspreis lässt sich später kein Ergebnis und kein
                # P&L berechnen -> Trade GAR NICHT öffnen statt Karteileiche anlegen.
                log.warning(f"⚠️ Kein Entry-Price für {asset} nach 3 Versuchen - Trade übersprungen")
                trades_übersprungen += 1
                continue
            # ─────────────────────────────────────────────────────────────

            demo_trade = signal_oeffnen({
                **signal,
                "entry_price":    entry_price,
                "strategyUsed":   result.get("strategyUsed", req.strategy),
                "mm_modus":       active_config["mm_modus"],
                "volatility_pct": vola_lookup.get(asset, 0),
                "sessionScore":   result.get("sessionScore", 0),
            })
            trades_geoeffnet += 1
            
            # --- HIER WURDE DER CODE GEHÄRTET (Sichere ID nach Trade-Erstellung) ---
            if not isinstance(demo_trade, dict):
                demo_trade = {}
            
            trade_id = demo_trade.get("ID", "Unbekannt")
            log.info(f"Demo-Trade: {trade_id} | {asset} | Entry: {entry_price} | SL: {signal.get('stopLoss')}% | TP: {signal.get('takeProfit')}%")
            # -----------------------------------------------------------------------

        score      = result.get("sessionScore", 0)
        overview   = result.get("marketOverview", "")
        demo_stats = get_statistik()

        msg = (
            f"📊 *TRADING ANALYSE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {ts}\n"
            f"🎯 Score: *{score}/100*\n"
            f"🔧 Strategie: *{req.strategy}*\n"
        )

        if wochenende:
            msg += f"📅 _Wochenende: nur BTC/ETH handelbar_\n"
        if trades_übersprungen > 0:
            msg += f"⏭ {trades_übersprungen} Asset(s) übersprungen (Wochenende)\n"

        msg += f"💡 {overview}\n\n"

        for s in all_signals:
            arrow = "🟢 LONG" if s["action"] == "buy" else "🔴 SHORT"
            star  = "⭐ " if s.get("confidence", 0) >= active_config["conf"] else ""
            handelbar = asset_handelbar(s.get("asset",""))
            skip = "" if handelbar else " _(Wochenende - übersprungen)_"
            msg  += (
                f"{arrow} *{star}{s['asset']}* | {s['confidence']}%{skip}\n"
                f"SL: {active_config['sl_pct']:.1f}% | TP: {active_config['tp_pct']:.1f}%\n"
                f"_{s.get('summary', '')[:100]}_\n\n"
            )

        if not all_signals:
            msg += "⏸ Keine Signale - Markt beobachten.\n"

        if not risk_ok:
            msg += f"🛡️ _Risk Guardian: Setup nicht freigegeben – keine Trades eröffnet._\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Demo-Kapital: *€{demo_stats['aktuelles_kapital']:.2f}*\n"
            f"📈 ROI: *{'+' if demo_stats['statistik']['roi'] >= 0 else ''}{demo_stats['statistik']['roi']:.1f}%*\n"
            f"🎯 Win Rate: *{demo_stats['statistik']['win_rate']:.1f}%*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 {DASHBOARD_URL}"
        )

        send_whatsapp(msg.strip())

        if req.auto_execute:
            strong = [s for s in all_signals if s.get("confidence", 0) >= active_config["conf"] and asset_handelbar(s.get("asset",""))]
            if strong:
                await auto_execute_signals(strong, req.position_size)

    except Exception as e:
        log.error(f"Pipeline-Fehler: {e}")
        send_whatsapp(f"❌ Pipeline-Fehler: {str(e)[:200]}")
    finally:
        pipeline_running = False


async def auto_execute_signals(signals: list, size: float):
    if not capital.is_connected():
        if not await capital.connect():
            send_whatsapp("❌ Capital.com nicht verbunden!")
            return
    executed = failed = 0
    for sig in signals:
        try:
            result = await capital.create_position(
                epic=asset_to_epic(sig["asset"]),
                direction=sig["direction"].upper(),
                size=size,
                stop_loss_pct=active_config["sl_pct"],
                take_profit_pct=active_config["tp_pct"],
            )
            if result.get("dealId"):
                tracker.save_trade({**sig, "dealId": result["dealId"], "size": size, "status": "auto"})
                executed += 1
            else:
                failed += 1
        except Exception as e:
            log.error(f"Order-Fehler {sig['asset']}: {e}")
            failed += 1
    send_whatsapp(f"⚡ Auto-Trade: {executed} ausgeführt | {failed} fehlgeschlagen")


# --- HIER WURDE DER CODE GEHÄRTET (Epic Formatierung) ---
def asset_to_epic(asset: str) -> str:
    if not asset:
        return ""
    clean_asset = asset.strip().upper()
    return {
        "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
        "AUD/USD": "AUDUSD", "USD/CHF": "USDCHF",
        "BTC/USD": "BTCUSD", "ETH/USD": "ETHUSD",
        "XAU/USD": "GOLD",   "XAG/USD": "SILVER",
        "US500": "US500",    "US100": "USTEC", "DE40": "DE40",
    }.get(clean_asset, clean_asset.replace("/", ""))
# --------------------------------------------------------


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/analyze")
async def analyze(req: AnalyzeRequest = None, background_tasks: BackgroundTasks = None, _auth: bool = Depends(pruefe_token)):
    if req is None:
        req = AnalyzeRequest()
    if pipeline_running:
        raise HTTPException(status_code=429, detail="Pipeline bereits aktiv")
    background_tasks.add_task(asyncio.run, run_analysis_pipeline(req))
    return {"status": "gestartet", "assets": req.assets, "strategy": req.strategy}

@app.post("/config/speichern")
async def config_speichern(req: ConfigRequest, _auth: bool = Depends(pruefe_token)):
    alte_strategie = active_config["strategy"]
    if req.assets   is not None: active_config["assets"]   = req.assets
    if req.strategy is not None: active_config["strategy"] = req.strategy
    if req.risk_pct is not None: active_config["risk_pct"] = req.risk_pct
    if req.sl_pct   is not None: active_config["sl_pct"]   = req.sl_pct
    if req.tp_pct   is not None: active_config["tp_pct"]   = req.tp_pct
    if req.size     is not None: active_config["size"]      = req.size
    if req.conf     is not None: active_config["conf"]      = req.conf
    if req.mode     is not None: active_config["mode"]      = req.mode
    if req.mm_modus is not None and req.mm_modus in MODI:
        active_config["mm_modus"] = req.mm_modus
    log.info(f"Config gespeichert: {active_config}")
    config_speichern_datei()   # persistent -> überlebt Neustarts/Deployments
    if req.strategy and req.strategy != alte_strategie:
        send_whatsapp(
            f"⚙️ *Strategie geändert*\n"
            f"Alt: {alte_strategie}\n"
            f"Neu: *{req.strategy}*\n"
            f"Nächste Analyse: morgen 07:00 Uhr"
        )
    return {"status": "gespeichert", "config": active_config}

@app.get("/config/aktiv")
async def config_aktiv():
    return active_config

@app.get("/mm/modi")
async def mm_modi():
    """Liste aller Money-Management-Modi + aktuell gewählter."""
    return {"modi": get_modi(), "aktiv": active_config["mm_modus"]}

@app.get("/selftest")
async def selftest():
    """
    Systemcheck: prüft nach einem Deploy, ob alles gesund ist.
    Rein lesend, gibt KEINE Secrets aus - nur ob sie gesetzt sind.
    """
    checks = []

    def add(name, ok, info, warn=False):
        checks.append({"name": name, "status": "warn" if (warn and not ok) else ("ok" if ok else "fehler"), "info": info})

    # 1. Zugangsdaten gesetzt?
    add("Anthropic API-Key", bool(os.getenv("ANTHROPIC_API_KEY")),
        "gesetzt" if os.getenv("ANTHROPIC_API_KEY") else "FEHLT - Analysen funktionieren nicht")
    add("Capital.com Zugangsdaten", bool(os.getenv("CAPITAL_API_KEY")),
        "gesetzt" if os.getenv("CAPITAL_API_KEY") else "FEHLT")

    # 2. Demo oder Live?
    ist_demo = "demo" in capital.base
    add("Konto-Modus", ist_demo,
        "DEMO (Spielgeld)" if ist_demo else "⚠️ LIVE - echtes Geld! CAPITAL_DEMO=true setzen",
        warn=True)

    # 3. Verbindung
    orders_frei = os.getenv("ORDERS_ENABLED", "false").strip().lower() == "true"
    add("Order-Sperre", not orders_frei,
        "🔒 Orders gesperrt - es kann KEINE echte Order rausgehen" if not orders_frei
        else "⚠️ OFFEN - echte Orders sind möglich!", warn=True)
    try:
        if not capital.is_connected():
            await capital.connect()
        verbunden = capital.is_connected()
    except Exception as e:
        verbunden = False
        log.warning(f"Selftest Verbindung: {e}")
    add("Capital.com Verbindung", verbunden, "verbunden" if verbunden else "NICHT verbunden")

    # 4. Datenspeicher persistent?
    tracker_datei = os.path.join(DATA_DIR, "Trading_Tracker.xlsx")
    daten_da = os.path.exists(tracker_datei)
    add("Datenspeicher", daten_da,
        f"{DATA_DIR} (Datei vorhanden)" if daten_da else f"{DATA_DIR} - noch keine Datei")

    schreibbar = False
    try:
        testpfad = os.path.join(DATA_DIR, ".schreibtest")
        with open(testpfad, "w") as f:
            f.write("x")
        os.remove(testpfad)
        schreibbar = True
    except Exception as e:
        log.warning(f"Selftest Schreibtest: {e}")
    add("Schreibrechte", schreibbar, "OK" if schreibbar else "KEIN Schreibzugriff - Volume prüfen!")

    # 5. Config-Persistenz aktiv?
    cfg_da = os.path.exists(CONFIG_FILE)
    add("Config-Persistenz", cfg_da,
        "config.json vorhanden - Einstellungen überleben Neustart" if cfg_da
        else "noch nicht gespeichert - einmal Config speichern", warn=True)

    # 6. Schreibschutz
    add("Schreibschutz (API_TOKEN)", bool(API_TOKEN),
        "aktiv" if API_TOKEN else "AUS - Endpoints offen für jeden mit der URL", warn=True)

    # 7. Trade-Auswertung: läuft der Ergebnis-Check?
    try:
        offene = get_offene_trades()
        stats  = get_statistik()
        abgeschlossen = stats["statistik"]["gewonnen"] + stats["statistik"]["verloren"]
        alte = 0
        for t in offene:
            try:
                geo = datetime.fromisoformat(str(t.get("Geöffnet am", "")))
                if (datetime.now() - geo).total_seconds() / 3600 > 52:
                    alte += 1
            except Exception:
                pass
        add("Trade-Auswertung", alte == 0,
            f"{len(offene)} offen, {abgeschlossen} abgeschlossen" +
            (f" - ⚠️ {alte} Trades älter als 52h werden nicht geschlossen!" if alte else ""),
            warn=True)
        add("Demo-Kapital", True, f"€{stats['aktuelles_kapital']:.2f} | Win-Rate {stats['statistik']['win_rate']}%")
    except Exception as e:
        add("Trade-Auswertung", False, f"Fehler: {e}")

    # 8. Indikatoren + Kerzen
    try:
        epic = asset_to_epic(active_config["assets"][0])
        kerzen = await capital.get_historical_prices(epic, "HOUR_4", 60)
        ind = calculate_all_indicators(kerzen) if kerzen else {"error": "keine Kerzen"}
        ok = bool(kerzen) and "error" not in ind
        add("Marktdaten & Indikatoren", ok,
            f"{len(kerzen)} Kerzen, Signal: {ind.get('signal', '-')}" if ok else "keine Daten")
    except Exception as e:
        add("Marktdaten & Indikatoren", False, f"Fehler: {e}")

    # 9. Zeitpläne
    jobs = scheduler.get_jobs()
    add("Zeitpläne", len(jobs) >= 3, f"{len(jobs)} Jobs aktiv")

    fehler   = sum(1 for c in checks if c["status"] == "fehler")
    warnungen = sum(1 for c in checks if c["status"] == "warn")
    return {
        "gesamt": "fehler" if fehler else ("warnung" if warnungen else "ok"),
        "fehler": fehler, "warnungen": warnungen, "geprueft": len(checks),
        "checks": checks,
        "zeit": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }

@app.post("/backtest/optimieren")
async def backtest_optimieren(req: BacktestRequest = None, _auth: bool = Depends(pruefe_token)):
    """
    Probiert systematisch SL/TP-Verhältnisse und Signal-Schwellen durch und
    zeigt, welche Kombination auf diesem Asset einen echten Vorsprung hätte.
    """
    if req is None:
        req = BacktestRequest()
    asset = req.asset or (active_config["assets"][0] if active_config["assets"] else "BTC/USD")
    if not capital.is_connected():
        await capital.connect()
    epic    = asset_to_epic(asset)
    candles = await capital.get_historical_prices(epic, req.resolution, req.count)
    if not candles or len(candles) < 80:
        raise HTTPException(status_code=400, detail=f"Zu wenige Kerzen für {asset}: {len(candles) if candles else 0}")
    res = optimiere_parameter(candles, startkapital=req.startkapital,
                              mm_modus=active_config["mm_modus"])
    res["asset"] = asset
    res["resolution"] = req.resolution
    return res

@app.post("/backtest")
async def backtest_starten(req: BacktestRequest = None, _auth: bool = Depends(pruefe_token)):
    """
    Regelbasierter Backtest über historische Kerzen. Vergleicht alle 6
    Money-Management-Modi über dieselbe Trade-Sequenz (RSI/MACD/EMA/BB, ohne LLM).
    Baseline: schlägt das Live-System diese Regel-Logik nicht, bringt die KI nichts.
    """
    if req is None:
        req = BacktestRequest()

    asset = req.asset or (active_config["assets"][0] if active_config["assets"] else "BTC/USD")
    sl    = req.sl_pct if req.sl_pct is not None else active_config["sl_pct"]
    tp    = req.tp_pct if req.tp_pct is not None else active_config["tp_pct"]

    if not capital.is_connected():
        await capital.connect()

    epic    = asset_to_epic(asset)
    candles = await capital.get_historical_prices(epic, req.resolution, req.count)
    if not candles or len(candles) < 60:
        raise HTTPException(status_code=400, detail=f"Zu wenige Kerzen für {asset}: {len(candles) if candles else 0}")

    res = vergleiche_modi(candles, startkapital=req.startkapital, sl_pct=sl, tp_pct=tp,
                          min_confluence=req.min_confluence)
    res["asset"]      = asset
    res["resolution"] = req.resolution

    if res.get("ergebnisse"):
        top = res["ergebnisse"][:3]
        msg = (
            f"🔬 *BACKTEST {asset}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {res['kerzen']} Kerzen ({req.resolution}) | {res['signal_trades']} Trades\n"
            f"🎯 Win Rate: {top[0]['win_rate']:.1f}% | SL {sl}% / TP {tp}%\n"
            f"━━━ Top Money-Management ━━━\n"
        )
        for i, r in enumerate(top, 1):
            msg += (f"{i}. *{r['mm_name']}*: {'+' if r['roi_pct'] >= 0 else ''}{r['roi_pct']:.1f}% ROI "
                    f"| DD {r['max_drawdown_pct']:.1f}% | PF {r['profit_factor']}\n")
        msg += f"━━━━━━━━━━━━━━━━━━━━\n🌐 {DASHBOARD_URL}"
        send_whatsapp(msg)

    return res

@app.post("/trade")
async def place_trade(req: TradeRequest, _auth: bool = Depends(pruefe_token)):
    if not capital.is_connected():
        if not await capital.connect():
            raise HTTPException(status_code=503, detail="Capital.com nicht verbunden")
    result = await capital.create_position(
        epic=asset_to_epic(req.asset),
        direction=req.direction.upper(),
        size=req.size,
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
    )
    if result.get("dealId"):
        tracker.save_trade({"asset": req.asset, "direction": req.direction,
                            "size": req.size, "dealId": result["dealId"],
                            "status": "manual", "action": "buy" if req.direction == "long" else "sell"})
    return result

@app.get("/positions")
async def get_positions():
    if not capital.is_connected():
        await capital.connect()
    return await capital.get_positions()

@app.post("/close/{deal_id}")
async def close_position(deal_id: str, _auth: bool = Depends(pruefe_token)):
    if not capital.is_connected():
        await capital.connect()
    result = await capital.close_position(deal_id)
    send_whatsapp(f"Position geschlossen: {deal_id}")
    return result

@app.get("/balance")
async def get_balance():
    if not capital.is_connected():
        await capital.connect()
    return await capital.get_account_info()

@app.get("/signals")
async def get_signals():
    return {
        "signals":        latest_signals,
        "count":          len(latest_signals),
        "sessionScore":   latest_analysis.get("sessionScore", 0),
        "marketOverview": latest_analysis.get("marketOverview", ""),
    }

@app.get("/history")
async def get_history():
    return tracker.get_trade_history()

@app.get("/excel-download")
async def excel_download():
    excel_path = os.path.join(DATA_DIR, "Trading_Tracker.xlsx")
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="Excel nicht gefunden")
    return FileResponse(
        excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Trading_Tracker_{datetime.now().strftime('%Y%m%d')}.xlsx"
    )

@app.get("/demo/statistik")
async def demo_statistik():
    return get_statistik()

@app.get("/demo/report")
async def demo_report():
    return {"report": generiere_tages_report()}

@app.post("/demo/report/senden")
async def demo_report_senden(_auth: bool = Depends(pruefe_token)):
    tages_snapshot()
    report = generiere_tages_report()
    send_whatsapp(report)
    return {"status": "gesendet", "report": report}

@app.post("/demo/trade/{trade_id}/schliessen")
async def demo_trade_schliessen(trade_id: str, ergebnis: str = "verloren", _auth: bool = Depends(pruefe_token)):
    trade = trade_schliessen(trade_id, ergebnis)
    return {"status": "geschlossen", "trade": trade}

@app.get("/demo/trades/offen")
async def demo_trades_offen():
    offene = get_offene_trades()
    return {"trades": offene, "anzahl": len(offene)}

@app.post("/schedule/pause")
async def pause_schedule(_auth: bool = Depends(pruefe_token)):
    try:
        scheduler.pause_job("morgen_analyse")
        return {"status": "pausiert"}
    except Exception as e:
        log.error(f"Pause fehlgeschlagen: {e}")
        raise HTTPException(status_code=400, detail=f"Zeitplan konnte nicht pausiert werden: {e}")

@app.post("/schedule/resume")
async def resume_schedule(_auth: bool = Depends(pruefe_token)):
    try:
        scheduler.resume_job("morgen_analyse")
        return {"status": "aktiv"}
    except Exception as e:
        log.error(f"Fortsetzen fehlgeschlagen: {e}")
        raise HTTPException(status_code=400, detail=f"Zeitplan konnte nicht fortgesetzt werden: {e}")

@app.get("/status")
async def status():
    job  = scheduler.get_job("morgen_analyse")
    demo = get_statistik()
    return {
        "status":            "running",
        "pipeline_running":  pipeline_running,
        "capital_connected": capital.is_connected(),
        "signals_count":     len(latest_signals),
        "assets":            active_config["assets"],
        "strategy":          active_config["strategy"],
        "auto_trade":        AUTO_TRADE,
        "mm_modus":          active_config["mm_modus"],
        "trading_modus":     active_config["mode"],
        "min_confidence":    active_config["conf"],
        "active_config":     active_config,
        "next_scheduled":    job.next_run_time.isoformat() if job else None,
        "demo_kapital":      demo["aktuelles_kapital"],
        "demo_roi":          demo["statistik"]["roi"],
        "demo_win_rate":     demo["statistik"]["win_rate"],
        "wochenende":        ist_wochenende(),
    }

@app.post("/connect")
async def connect_capital(_auth: bool = Depends(pruefe_token)):
    ok = await capital.connect()
    return {"connected": ok}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
