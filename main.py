from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from contextlib import asynccontextmanager
import asyncio, os, json, logging
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
SCHEDULE_HOURS  = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "1"))

os.makedirs(DATA_DIR, exist_ok=True)

capital = CapitalClient()
tracker = ExcelTracker(DATA_DIR)

latest_signals   = []
latest_analysis  = {}
pipeline_running = False
schedule_log     = []

scheduler = BackgroundScheduler(timezone="Europe/Vienna")


def scheduled_job():
    log.info(f"GEPLANTER LAUF | {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    schedule_log.append({"time": datetime.now().isoformat(), "trigger": "scheduler"})
    if len(schedule_log) > 50:
        schedule_log.pop(0)
    asyncio.run(run_analysis_pipeline(AnalyzeRequest(
        assets=ASSETS, strategy=STRATEGY,
        risk_pct=MAX_RISK_PCT, sl_pct=STOP_LOSS_PCT,
        tp_pct=TAKE_PROFIT_PCT, position_size=POSITION_SIZE,
        auto_execute=AUTO_TRADE,
    )))


def tages_report_job():
    """Täglicher Report um 20:00 Uhr"""
    log.info("📊 Tages-Report wird generiert...")
    tages_snapshot()
    report = generiere_tages_report()
    send_whatsapp(report)
    log.info("✅ Tages-Report gesendet")


def ergebnis_check_job():
    """Prüft offene Trades nach 48h"""
    offene = get_offene_trades()
    if not offene:
        return
    log.info(f"📋 Ergebnis-Check: {len(offene)} offene Trades")
    for trade in offene:
        geoeffnet = datetime.fromisoformat(trade["geoeffnet_am"])
        alter_stunden = (datetime.now() - geoeffnet).total_seconds() / 3600
        if alter_stunden > 48:
            trade_schliessen(trade["id"], "verloren")
            log.info(f"Trade {trade['id']} nach 48h geschlossen")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        scheduled_job,
        trigger=IntervalTrigger(hours=SCHEDULE_HOURS, timezone="Europe/Vienna"),
        id="trading_pipeline", replace_existing=True, max_instances=1,
    )
    scheduler.add_job(
        tages_report_job,
        trigger=CronTrigger(hour=20, minute=0, timezone="Europe/Vienna"),
        id="tages_report", replace_existing=True,
    )
    scheduler.add_job(
        ergebnis_check_job,
        trigger=IntervalTrigger(hours=4, timezone="Europe/Vienna"),
        id="ergebnis_check", replace_existing=True,
    )
    scheduler.start()

    try:
        connected = await capital.connect()
        if connected:
            log.info("Capital.com verbunden")
        else:
            log.warning("Capital.com Verbindung fehlgeschlagen")
    except Exception as e:
        log.warning(f"Capital.com Fehler: {e}")

    job = scheduler.get_job("trading_pipeline")
    next_run = job.next_run_time.strftime("%d.%m.%Y %H:%M") if job else "?"
    tages_snapshot()

    send_whatsapp(
        f"🤖 *Trading-Agent gestartet*\n"
        f"📊 Analyse alle {SCHEDULE_HOURS}h\n"
        f"💹 Assets: {', '.join(ASSETS)}\n"
        f"🎯 Strategie: {STRATEGY}\n"
        f"⚡ Auto-Trade: {'AN' if AUTO_TRADE else 'AUS'}\n"
        f"🔗 Capital.com: {'✅ Verbunden' if capital.is_connected() else '❌ Getrennt'}\n"
        f"⏰ Nächster Lauf: {next_run}\n"
        f"📅 Tages-Report: täglich 20:00 Uhr"
    )
    yield
    scheduler.shutdown()


app = FastAPI(title="Trading Multi-Agent v3.0", lifespan=lifespan)


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


async def run_analysis_pipeline(req: AnalyzeRequest):
    global latest_signals, latest_analysis, pipeline_running
    if pipeline_running:
        log.warning("Pipeline bereits aktiv")
        return
    pipeline_running = True
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        log.info(f"PIPELINE START | {ts}")

        result = await run_pipeline(
            assets=req.assets, strategy=req.strategy,
            risk_pct=req.risk_pct, sl_pct=req.sl_pct,
            tp_pct=req.tp_pct, position_size=req.position_size,
        )

        latest_analysis = result
        decisions = result.get("decisions", [])
        all_signals = [d for d in decisions if d.get("action") != "hold"]
        latest_signals = all_signals
        tracker.save_analysis(result)

        # Demo Trades öffnen
        for signal in all_signals:
            if signal.get("confidence", 0) >= MIN_CONFIDENCE:
                demo_trade = signal_oeffnen({
                    **signal,
                    "strategyUsed": result.get("strategyUsed", STRATEGY),
                })
                log.info(f"Demo-Trade geöffnet: {demo_trade['id']} | {signal['asset']}")

        score    = result.get("sessionScore", 0)
        overview = result.get("marketOverview", "")
        demo_stats = get_statistik()

        msg = (
            f"📊 *TRADING ANALYSE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {ts}\n"
            f"🎯 Score: *{score}/100*\n"
            f"💡 {overview}\n\n"
        )

        for s in all_signals:
            arrow = "🟢 LONG" if s["action"] == "buy" else "🔴 SHORT"
            star  = "⭐ " if s.get("confidence", 0) >= MIN_CONFIDENCE else ""
            msg  += (
                f"{arrow} *{star}{s['asset']}* | {s['confidence']}%\n"
                f"SL: {s.get('stopLoss', 0):.1f}% | TP: {s.get('takeProfit', 0):.1f}%\n"
                f"_{s.get('summary', '')[:100]}_\n\n"
            )

        if not all_signals:
            msg += "⏸ Keine Signale - Markt beobachten.\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Demo-Kapital: *€{demo_stats['aktuelles_kapital']:.2f}*\n"
            f"📈 ROI: *{'+' if demo_stats['statistik']['roi'] >= 0 else ''}{demo_stats['statistik']['roi']:.1f}%*\n"
            f"🎯 Win Rate: *{demo_stats['statistik']['win_rate']:.1f}%*"
        )

        send_whatsapp(msg.strip())

        if req.auto_execute:
            strong = [s for s in all_signals if s.get("confidence", 0) >= MIN_CONFIDENCE]
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
                stop_loss_pct=sig.get("stopLoss", STOP_LOSS_PCT),
                take_profit_pct=sig.get("takeProfit", TAKE_PROFIT_PCT),
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
    scheduler.pause_job("trading_pipeline")
    return {"status": "pausiert"}

@app.post("/schedule/resume")
async def resume_schedule():
    scheduler.resume_job("trading_pipeline")
    job = scheduler.get_job("trading_pipeline")
    return {"status": "aktiv", "next_run": job.next_run_time.isoformat() if job else None}

@app.get("/status")
async def status():
    job = scheduler.get_job("trading_pipeline")
    demo = get_statistik()
    return {
        "status":            "running",
        "pipeline_running":  pipeline_running,
        "capital_connected": capital.is_connected(),
        "signals_count":     len(latest_signals),
        "assets":            ASSETS,
        "strategy":          STRATEGY,
        "auto_trade":        AUTO_TRADE,
        "min_confidence":    MIN_CONFIDENCE,
        "schedule_hours":    SCHEDULE_HOURS,
        "next_scheduled":    job.next_run_time.isoformat() if job else None,
        "demo_kapital":      demo["aktuelles_kapital"],
        "demo_roi":          demo["statistik"]["roi"],
        "demo_win_rate":     demo["statistik"]["win_rate"],
    }

@app.post("/connect")
async def connect_capital():
    ok = await capital.connect()
    return {"connected": ok}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
