# Trading Multi-Agent v3.1
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from contextlib import asynccontextmanager
import asyncio, os, json, logging
from datetime import datetime

from agents import run_pipeline
from capital_client import CapitalClient
from excel_tracker import ExcelTracker
from whatsapp import send_whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Config from ENV ──
ASSETS          = os.getenv("TRADING_ASSETS", "EUR/USD,BTC/USD,XAU/USD,US500").split(",")
STRATEGY        = os.getenv("TRADING_STRATEGY", "adaptive")
MAX_RISK_PCT    = float(os.getenv("MAX_RISK_PCT", "2"))
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "1.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
POSITION_SIZE   = float(os.getenv("POSITION_SIZE_EUR", "1000"))
AUTO_TRADE      = os.getenv("AUTO_TRADE", "false").lower() == "true"
DATA_DIR        = os.getenv("DATA_DIR", "/app/data")
MIN_CONFIDENCE  = int(os.getenv("MIN_CONFIDENCE", "70"))          # Mindest-Konfidenz für Auto-Trade
SCHEDULE_HOURS  = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "1")) # Stündlich

os.makedirs(DATA_DIR, exist_ok=True)

capital = CapitalClient()
tracker = ExcelTracker(DATA_DIR)

# ── State ──
latest_signals   = []
latest_analysis  = {}
pipeline_running = False
schedule_log     = []

# ── Scheduler ──
scheduler = BackgroundScheduler(timezone="Europe/Vienna")

def scheduled_job():
    """Wird automatisch alle X Stunden ausgeführt."""
    log.info(f"⏰ GEPLANTER LAUF | {datetime.now().strftime('%d.%m.%Y %H:%M')} | Intervall: {SCHEDULE_HOURS}h")
    schedule_log.append({"time": datetime.now().isoformat(), "trigger": "scheduler"})
    if len(schedule_log) > 50:
        schedule_log.pop(0)
    asyncio.run(run_analysis_pipeline(AnalyzeRequest(
        assets=ASSETS, strategy=STRATEGY,
        risk_pct=MAX_RISK_PCT, sl_pct=STOP_LOSS_PCT,
        tp_pct=TAKE_PROFIT_PCT, position_size=POSITION_SIZE,
        auto_execute=AUTO_TRADE,
    )))

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        scheduled_job,
        trigger=IntervalTrigger(hours=SCHEDULE_HOURS, timezone="Europe/Vienna"),
        id="trading_pipeline",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    job = scheduler.get_job("trading_pipeline")
    next_run = job.next_run_time.strftime("%d.%m.%Y %H:%M") if job else "—"
    log.info(f"✅ Trading Multi-Agent gestartet | Alle {SCHEDULE_HOURS}h | Nächster Lauf: {next_run}")
    send_whatsapp(
        f"🤖 *Trading-Agent gestartet*\n"
        f"⏰ Automatische Analyse alle {SCHEDULE_HOURS}h\n"
        f"📊 Assets: {', '.join(ASSETS)}\n"
        f"🎯 Strategie: {STRATEGY}\n"
        f"⚡ Auto-Trade: {'AN ✅' if AUTO_TRADE else 'AUS ⏸'}\n"
        f"🎚 Min. Konfidenz: {MIN_CONFIDENCE}%\n"
        f"⏰ Nächster Lauf: {next_run}"
    )
    yield
    scheduler.shutdown()

app = FastAPI(title="Trading Multi-Agent v3.0", lifespan=lifespan)

# ── Models ──
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

# ── Pipeline ──
async def run_analysis_pipeline(req: AnalyzeRequest):
    global latest_signals, latest_analysis, pipeline_running
    if pipeline_running:
        log.warning("Pipeline bereits aktiv – überspringe")
        return
    pipeline_running = True
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        log.info(f"PIPELINE START | {ts} | {req.strategy} | {req.assets}")
        send_whatsapp(
            f"🚀 *Trading-Pipeline gestartet*\n"
            f"⏰ {ts}\nStrategie: {req.strategy}\n"
            f"Assets: {', '.join(req.assets)}\n"
            f"⚡ Auto-Trade: {'AN' if req.auto_execute else 'AUS'}"
        )

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

        # ── WhatsApp Summary ──
        score = result.get("sessionScore", 0)
        overview = result.get("marketOverview", "")
        msg = f"✅ *Analyse abgeschlossen*\n⏰ {ts}\nScore: {score}/100\n{overview}\n\n"

        strong_signals = [s for s in all_signals if s.get("confidence", 0) >= MIN_CONFIDENCE]
        weak_signals   = [s for s in all_signals if s.get("confidence", 0) < MIN_CONFIDENCE]

        for s in all_signals:
            emoji = "📈" if s["action"] == "buy" else "📉"
            star  = "⭐ " if s.get("confidence", 0) >= MIN_CONFIDENCE else ""
            msg  += f"{emoji} {star}*{s['action'].upper()} {s['asset']}* | {s['confidence']}% Konfidenz\n"
            msg  += f"SL: {s.get('stopLoss',0):.1f}% | TP: {s.get('takeProfit',0):.1f}% | R:R {s.get('riskReward',0):.1f}:1\n\n"

        if not all_signals:
            msg += "📊 Keine Trade-Signale – Markt abwarten.\n"

        send_whatsapp(msg.strip())

        # ── AUTO-TRADE: Nur Signale >= MIN_CONFIDENCE ──
        if req.auto_execute:
            if strong_signals:
                log.info(f"⚡ AUTO-TRADE: {len(strong_signals)} starke Signale (≥{MIN_CONFIDENCE}%)")
                send_whatsapp(
                    f"⚡ *Auto-Trade wird ausgeführt*\n"
                    f"{len(strong_signals)} Signal(e) mit ≥{MIN_CONFIDENCE}% Konfidenz..."
                )
                await auto_execute_signals(strong_signals, req.position_size)

                if weak_signals:
                    skipped = "\n".join([
                        f"⚠️ {s['action'].upper()} {s['asset']} ({s.get('confidence',0)}%) – unter Limit"
                        for s in weak_signals
                    ])
                    send_whatsapp(f"ℹ️ *Übersprungen (<{MIN_CONFIDENCE}%):*\n{skipped}")
            else:
                send_whatsapp(
                    f"⏸ *Kein Auto-Trade*\n"
                    f"Keine Signale mit ≥{MIN_CONFIDENCE}% Konfidenz.\n"
                    f"Nächste Analyse in {SCHEDULE_HOURS}h."
                )

        # Nächsten Lauf ankündigen
        job = scheduler.get_job("trading_pipeline")
        if job:
            next_run = job.next_run_time.strftime("%d.%m.%Y %H:%M")
            send_whatsapp(f"⏰ *Nächste automatische Analyse:* {next_run}")

    except Exception as e:
        log.error(f"Pipeline-Fehler: {e}")
        send_whatsapp(f"❌ *Pipeline-Fehler*\n{str(e)[:200]}")
    finally:
        pipeline_running = False

async def auto_execute_signals(signals: list, size: float):
    if not capital.is_connected():
        if not await capital.connect():
            send_whatsapp("⚠️ Capital.com nicht verbunden – Orders nicht ausgeführt!")
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
                send_whatsapp(
                    f"✅ *Auto-Order platziert*\n"
                    f"{sig['action'].upper()} {sig['asset']}\n"
                    f"Size: €{size:.0f} | Konfidenz: {sig.get('confidence',0)}%\n"
                    f"Deal ID: {result['dealId']}"
                )
                executed += 1
            else:
                send_whatsapp(f"⚠️ Order fehlgeschlagen: {sig['asset']}\n{str(result)[:150]}")
                failed += 1
        except Exception as e:
            log.error(f"Order-Fehler {sig['asset']}: {e}")
            send_whatsapp(f"❌ Order-Fehler: {sig['asset']}\n{str(e)[:150]}")
            failed += 1

    send_whatsapp(f"📊 *Zusammenfassung*\n✅ Ausgeführt: {executed} | ❌ Fehlgeschlagen: {failed}")

def asset_to_epic(asset: str) -> str:
    return {
        "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
        "AUD/USD": "AUDUSD", "USD/CHF": "USDCHF",
        "BTC/USD": "BTCUSD", "ETH/USD": "ETHUSD", "XRP/USD": "XRPUSD",
        "XAU/USD": "GOLD",   "XAG/USD": "SILVER",
        "US500": "US500", "US100": "USTEC", "DE40": "DE40", "UK100": "UK100",
    }.get(asset.upper(), asset.replace("/", ""))

# ── ENDPOINTS ──

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    return HTMLResponse(content=html)

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
        tracker.save_trade({"asset": req.asset, "direction": req.direction, "size": req.size,
                            "dealId": result["dealId"], "status": "manual",
                            "action": "buy" if req.direction == "long" else "sell"})
        send_whatsapp(f"⚡ *Manueller Trade*\n{req.direction.upper()} {req.asset}\nSize: €{req.size}\nDeal: {result['dealId']}")
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
    send_whatsapp(f"🔒 *Position geschlossen*\nDeal ID: {deal_id}")
    return result

@app.get("/balance")
async def get_balance():
    if not capital.is_connected():
        await capital.connect()
    return await capital.get_account_info()

@app.get("/signals")
async def get_signals():
    return {
        "signals": latest_signals,
        "count": len(latest_signals),
        "sessionScore": latest_analysis.get("sessionScore", 0),
        "marketOverview": latest_analysis.get("marketOverview", ""),
    }

@app.get("/history")
async def get_history():
    return tracker.get_trade_history()

@app.get("/schedule")
async def get_schedule():
    job = scheduler.get_job("trading_pipeline")
    return {
        "interval_hours":   SCHEDULE_HOURS,
        "auto_trade":       AUTO_TRADE,
        "min_confidence":   MIN_CONFIDENCE,
        "next_run":         job.next_run_time.isoformat() if job else None,
        "recent_runs":      schedule_log[-10:],
        "pipeline_running": pipeline_running,
    }

@app.post("/schedule/pause")
async def pause_schedule():
    scheduler.pause_job("trading_pipeline")
    send_whatsapp("⏸ *Scheduler pausiert* – kein automatischer Handel bis Resume")
    return {"status": "pausiert"}

@app.post("/schedule/resume")
async def resume_schedule():
    scheduler.resume_job("trading_pipeline")
    job = scheduler.get_job("trading_pipeline")
    next_run = job.next_run_time.strftime("%d.%m.%Y %H:%M") if job else "—"
    send_whatsapp(f"▶️ *Scheduler fortgesetzt*\nNächste Analyse: {next_run}")
    return {"status": "aktiv", "next_run": next_run}

@app.get("/status")
async def status():
    job = scheduler.get_job("trading_pipeline")
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
    }

@app.post("/connect")
async def connect_capital():
    ok = await capital.connect()
    return {"connected": ok, "message": "Verbunden" if ok else "Fehlgeschlagen"}
