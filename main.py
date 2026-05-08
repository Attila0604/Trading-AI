# Trading Multi-Agent v3.0 - Update 11.04.2026
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager
import asyncio, os, logging
from datetime import datetime

from agents import run_pipeline
from capital_client import CapitalClient
from excel_tracker import ExcelTracker
from whatsapp import send_whatsapp
from demo_tracker import (
    signal_oeffnen, trade_schliessen, tages_snapshot,
    get_offene_trades, get_statistik, generiere_tages_report
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ASSETS          = os.getenv("TRADING_ASSETS", "EUR/USD,BTC/USD,XAU/USD,US500").split(",")
STRATEGY        = os.getenv("TRADING_STRATEGY", "adaptive")
MAX_RISK_PCT    = float(os.getenv("MAX_RISK_PCT", "2"))
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "1.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
POSITION_SIZE   = float(os.getenv("POSITION_SIZE_EUR", "1000"))
AUTO_TRADE      = os.getenv("AUTO_TRADE", "false").lower() == "true"
DATA_DIR        = os.getenv("DATA_DIR", "/app/data")
MIN_CONFIDENCE  = int(os.getenv("MIN_CONFIDENCE", "70"))
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
}

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
            asset       = trade.get("asset", "")
            epic        = asset_to_epic(asset)
            entry_price = float(trade.get("entry_price", 0))
            action      = trade.get("action", "buy")
            sl_pct      = float(trade.get("sl_pct", STOP_LOSS_PCT))
            tp_pct      = float(trade.get("tp_pct", TAKE_PROFIT_PCT))

            price_data    = await capital.get_prices(epic)
            current_price = price_data.get("bid") or price_data.get("ask")

            if not current_price:
                log.warning(f"Kein Preis für {asset}")
                continue

            current_price = float(current_price)
            geoeffnet     = datetime.fromisoformat(trade["geoeffnet_am"])
            alter_std     = (datetime.now() - geoeffnet).total_seconds() / 3600

            log.info(f"🔍 {trade['id']} | {asset} | Entry: {entry_price} | Aktuell: {current_price} | {alter_std:.1f}h")

            ergebnis = None

            if entry_price > 0:
                if action == "buy":
                    tp_level = entry_price * (1 + tp_pct / 100)
                    sl_level = entry_price * (1 - sl_pct / 100)
                    if current_price >= tp_level:
                        ergebnis = "gewonnen"
                        log.info(f"✅ TP! {asset} | {entry_price:.5f} → {current_price:.5f}")
                    elif current_price <= sl_level:
                        ergebnis = "verloren"
                        log.info(f"❌ SL! {asset} | {entry_price:.5f} → {current_price:.5f}")
                else:
                    tp_level = entry_price * (1 - tp_pct / 100)
                    sl_level = entry_price * (1 + sl_pct / 100)
                    if current_price <= tp_level:
                        ergebnis = "gewonnen"
                        log.info(f"✅ TP SELL! {asset} | {entry_price:.5f} → {current_price:.5f}")
                    elif current_price >= sl_level:
                        ergebnis = "verloren"
                        log.info(f"❌ SL SELL! {asset} | {entry_price:.5f} → {current_price:.5f}")

            if ergebnis is None and alter_std > 48:
                ergebnis = "verloren"
                log.info(f"⏰ Trade {trade['id']} nach 48h geschlossen")

            if ergebnis:
                geschlossen = trade_schliessen(trade["id"], ergebnis)
                stats       = get_statistik()
                tracker.save_trade({
                    **trade,
                    "ergebnis":       ergebnis,
                    "kapital_danach": stats["aktuelles_kapital"],
                })
                emoji = "✅" if ergebnis == "gewonnen" else "❌"
                send_whatsapp(
                    f"{emoji} *Demo-Trade {ergebnis.upper()}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 {asset} | {action.upper()}\n"
                    f"📈 Entry: {entry_price:.5f}\n"
                    f"📉 Aktuell: {current_price:.5f}\n"
                    f"💰 P&L: {'+' if geschlossen.get('pnl',0)>=0 else ''}€{geschlossen.get('pnl',0):.2f}\n"
                    f"💼 Kapital: €{stats['aktuelles_kapital']:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 {DASHBOARD_URL}"
                )

        except Exception as e:
            log.error(f"Ergebnis-Check Fehler [{trade.get('id')}]: {e}")


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


class ConfigRequest(BaseModel):
    assets: list[str] = None
    strategy: str = None
    risk_pct: float = None
    sl_pct: float = None
    tp_pct: float = None
    size: float = None
    conf: int = None
    mode: str = None


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

        for signal in all_signals:
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
                log.warning(f"⚠️ Kein Entry-Price für {asset} nach 3 Versuchen")
            # ─────────────────────────────────────────────────────────────

            demo_trade = signal_oeffnen({
                **signal,
                "entry_price":  entry_price,
                "strategyUsed": result.get("strategyUsed", req.strategy),
            })
            trades_geoeffnet += 1
            log.info(f"Demo-Trade: {demo_trade['id']} | {asset} | Entry: {entry_price} | SL: {signal.get('stopLoss')}% | TP: {signal.get('takeProfit')}%")

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


def asset_to_epic(asset: str) -> str:
    return {
        "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
        "AUD/USD": "AUDUSD", "USD/CHF": "USDCHF",
        "BTC/USD": "BTCUSD", "ETH/USD": "ETHUSD",
        "XAU/USD": "GOLD",   "XAG/USD": "SILVER",
        "US500": "US500",    "US100": "USTEC", "DE40": "DE40",
    }.get(asset.upper(), asset.replace("/", ""))


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/analyze")
async def analyze(req: AnalyzeRequest = None, background_tasks: BackgroundTasks = None):
    if req is None:
        req = AnalyzeRequest()
    if pipeline_running:
        raise HTTPException(status_code=429, detail="Pipeline bereits aktiv")
    background_tasks.add_task(asyncio.run, run_analysis_pipeline(req))
    return {"status": "gestartet", "assets": req.assets, "strategy": req.strategy}

@app.post("/config/speichern")
async def config_speichern(req: ConfigRequest):
    alte_strategie = active_config["strategy"]
    if req.assets   is not None: active_config["assets"]   = req.assets
    if req.strategy is not None: active_config["strategy"] = req.strategy
    if req.risk_pct is not None: active_config["risk_pct"] = req.risk_pct
    if req.sl_pct   is not None: active_config["sl_pct"]   = req.sl_pct
    if req.tp_pct   is not None: active_config["tp_pct"]   = req.tp_pct
    if req.size     is not None: active_config["size"]      = req.size
    if req.conf     is not None: active_config["conf"]      = req.conf
    if req.mode     is not None: active_config["mode"]      = req.mode
    log.info(f"Config gespeichert: {active_config}")
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

@app.post("/trade")
async def place_trade(req: TradeRequest):
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
async def close_position(deal_id: str):
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
async def demo_report_senden():
    tages_snapshot()
    report = generiere_tages_report()
    send_whatsapp(report)
    return {"status": "gesendet", "report": report}

@app.post("/demo/trade/{trade_id}/schliessen")
async def demo_trade_schliessen(trade_id: str, ergebnis: str = "verloren"):
    trade = trade_schliessen(trade_id, ergebnis)
    return {"status": "geschlossen", "trade": trade}

@app.get("/demo/trades/offen")
async def demo_trades_offen():
    offene = get_offene_trades()
    return {"trades": offene, "anzahl": len(offene)}

@app.post("/schedule/pause")
async def pause_schedule():
    scheduler.pause_job("morgen_analyse")
    return {"status": "pausiert"}

@app.post("/schedule/resume")
async def resume_schedule():
    scheduler.resume_job("morgen_analyse")
    return {"status": "aktiv"}

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
        "min_confidence":    active_config["conf"],
        "active_config":     active_config,
        "next_scheduled":    job.next_run_time.isoformat() if job else None,
        "demo_kapital":      demo["aktuelles_kapital"],
        "demo_roi":          demo["statistik"]["roi"],
        "demo_win_rate":     demo["statistik"]["win_rate"],
        "wochenende":        ist_wochenende(),
    }

@app.post("/connect")
async def connect_capital():
    ok = await capital.connect()
    return {"connected": ok}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
