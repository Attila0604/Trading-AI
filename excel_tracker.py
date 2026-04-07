import os, logging
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

log = logging.getLogger(__name__)

BG_DARK   = "FF030309"
BG_CARD   = "FF0D0D18"
BG_HEADER = "FF0F0F1A"
GOLD      = "FFF5C518"
CYAN      = "FF22D3EE"
GREEN     = "FF10B981"
RED       = "FFF43F5E"
PURPLE    = "FFA855F7"
MUTED     = "FF525270"
TEXT      = "FFC8C8E4"
WHITE     = "FFFFFFFF"


def _hfill(color): return PatternFill(start_color=color, end_color=color, fill_type="solid")
def _font(bold=False, color=TEXT, size=11): return Font(name="Consolas", bold=bold, color=color, size=size)
def _border():
    s = Side(border_style="thin", color="FF181830")
    return Border(left=s, right=s, top=s, bottom=s)
def _center(): return Alignment(horizontal="center", vertical="center", wrap_text=True)
def _left():   return Alignment(horizontal="left",   vertical="center", wrap_text=True)


class ExcelTracker:
    SHEETS = ["Demo-Kapital", "Analyse-Log", "Trades", "Performance", "Einstellungen"]

    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "Trading_Tracker.xlsx"
        self._init_workbook()

    def _init_workbook(self):
        if self.path.exists():
            log.info(f"Excel geladen: {self.path}")
            return
        log.info(f"Excel wird erstellt: {self.path}")
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        self._create_demo_kapital_sheet(wb)
        self._create_analyse_sheet(wb)
        self._create_trades_sheet(wb)
        self._create_performance_sheet(wb)
        self._create_settings_sheet(wb)
        wb.save(self.path)
        log.info("✅ Excel erstellt")

    # ── Sheet: Demo-Kapital ───────────────────────────────────────────────
    def _create_demo_kapital_sheet(self, wb):
        ws = wb.create_sheet("Demo-Kapital")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "F5C518"

        # Titel
        ws.merge_cells("A1:H1")
        title = ws["A1"]
        title.value    = "💰 DEMO-KAPITAL TRACKING"
        title.font     = _font(bold=True, color=GOLD, size=14)
        title.fill     = _hfill(BG_HEADER)
        title.alignment = _center()
        ws.row_dimensions[1].height = 36

        # KPI Bereich
        kpis = [
            ("Startkapital", "B3"),
            ("Aktuelles Kapital", "D3"),
            ("Gesamt P&L", "F3"),
            ("ROI %", "H3"),
        ]
        for label, cell in kpis:
            ws[cell].value     = label
            ws[cell].font      = _font(bold=True, color=MUTED, size=9)
            ws[cell].fill      = _hfill(BG_HEADER)
            ws[cell].alignment = _center()

        kpi_values = [
            ("=1000", "B4", CYAN),
            ("=B4+SUM(C8:C1000)", "D4", GREEN),
            ("=D4-B4", "F4", GOLD),
            ('=IF(B4>0,ROUND((D4-B4)/B4*100,2),0)&"%"', "H4", PURPLE),
        ]
        for formula, cell, color in kpi_values:
            ws[cell].value     = formula
            ws[cell].font      = _font(bold=True, color=color, size=16)
            ws[cell].fill      = _hfill(BG_CARD)
            ws[cell].alignment = _center()
        ws.row_dimensions[4].height = 32

        # Kapitalverlauf Header
        ws.merge_cells("A6:H6")
        ws["A6"].value     = "KAPITALVERLAUF"
        ws["A6"].font      = _font(bold=True, color=CYAN, size=10)
        ws["A6"].fill      = _hfill(BG_HEADER)
        ws["A6"].alignment = _center()

        headers  = ["Datum", "P&L Tag", "Kumulativ P&L", "Kapital", "Trades", "Gewonnen", "Verloren", "Win Rate"]
        widths   = [14, 12, 16, 14, 10, 12, 12, 12]
        for i, (h, w) in enumerate(zip(headers, widths), start=1):
            col = get_column_letter(i)
            ws.column_dimensions[col].width = w
            cell = ws.cell(row=7, column=i, value=h)
            cell.font      = _font(bold=True, color=WHITE, size=10)
            cell.fill      = _hfill(BG_HEADER)
            cell.alignment = _center()
            cell.border    = Border(
                bottom=Side(border_style="medium", color=GOLD),
                left=Side(border_style="thin", color="FF181830"),
                right=Side(border_style="thin", color="FF181830"),
            )
        ws.row_dimensions[7].height = 28

    # ── Sheet: Analyse-Log ────────────────────────────────────────────────
    def _create_analyse_sheet(self, wb):
        ws = wb.create_sheet("Analyse-Log")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "22D3EE"
        headers = ["Datum", "Uhrzeit", "Asset", "Action", "Konfidenz", "SL%", "TP%", "R:R", "Score", "Urgency", "Zusammenfassung"]
        widths  = [12, 10, 12, 10, 12, 8, 8, 8, 10, 12, 60]
        self._write_header_row(ws, headers, widths, CYAN)
        ws.row_dimensions[1].height = 28

    # ── Sheet: Trades ─────────────────────────────────────────────────────
    def _create_trades_sheet(self, wb):
        ws = wb.create_sheet("Trades")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "10B981"
        headers = ["Datum", "Uhrzeit", "Asset", "Direction", "Einsatz €", "SL%", "TP%", "R:R", "Status", "Ergebnis", "P&L €", "Kapital danach"]
        widths  = [12, 10, 12, 12, 12, 8, 8, 8, 12, 12, 10, 14]
        self._write_header_row(ws, headers, widths, GREEN)
        ws.row_dimensions[1].height = 28

    # ── Sheet: Performance ────────────────────────────────────────────────
    def _create_performance_sheet(self, wb):
        ws = wb.create_sheet("Performance")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "A855F7"

        ws.merge_cells("A1:I1")
        ws["A1"].value     = "📈 PERFORMANCE ÜBERSICHT"
        ws["A1"].font      = _font(bold=True, color=PURPLE, size=13)
        ws["A1"].fill      = _hfill(BG_HEADER)
        ws["A1"].alignment = _center()
        ws.row_dimensions[1].height = 32

        kpis = [
            ("Gesamt Trades",   "B3"), ("Win Rate",       "D3"),
            ("Gesamt P&L",      "F3"), ("Bester Trade",   "H3"),
        ]
        for label, cell in kpis:
            ws[cell].value     = label
            ws[cell].font      = _font(bold=True, color=MUTED, size=9)
            ws[cell].fill      = _hfill(BG_HEADER)
            ws[cell].alignment = _center()

        formulas = [
            ("=COUNTA(Trades!A2:A10000)", "B4", CYAN),
            ('=IF(B4>0,ROUND(COUNTIF(Trades!J2:J10000,"gewonnen")/B4*100,1)&"%","0%")', "D4", GREEN),
            ("=SUM(Trades!K2:K10000)", "F4", GOLD),
            ("=MAX(Trades!K2:K10000)", "H4", GREEN),
        ]
        for formula, cell, color in formulas:
            ws[cell].value     = formula
            ws[cell].font      = _font(bold=True, color=color, size=16)
            ws[cell].fill      = _hfill(BG_CARD)
            ws[cell].alignment = _center()
        ws.row_dimensions[4].height = 32

        ws.merge_cells("A6:I6")
        ws["A6"].value     = "MONATS-ÜBERSICHT"
        ws["A6"].font      = _font(bold=True, color=PURPLE, size=10)
        ws["A6"].fill      = _hfill(BG_HEADER)
        ws["A6"].alignment = _center()

        headers = ["Monat", "Trades", "Gewonnen", "Verloren", "Win Rate", "P&L €", "ROI %", "Bester", "Schlechtester"]
        widths  = [14, 10, 12, 12, 12, 12, 10, 12, 16]
        for i, (h, w) in enumerate(zip(headers, widths), start=1):
            col = get_column_letter(i)
            ws.column_dimensions[col].width = w
            cell = ws.cell(row=7, column=i, value=h)
            cell.font      = _font(bold=True, color=WHITE, size=10)
            cell.fill      = _hfill(BG_HEADER)
            cell.alignment = _center()
            cell.border    = Border(
                bottom=Side(border_style="medium", color=PURPLE),
                left=Side(border_style="thin", color="FF181830"),
                right=Side(border_style="thin", color="FF181830"),
            )
        ws.row_dimensions[7].height = 28

        for col in ["A", "B", "C", "D", "E", "F", "G", "H", "I"]:
            ws.column_dimensions[col].width = widths[ord(col) - ord("A")]

    # ── Sheet: Einstellungen ──────────────────────────────────────────────
    def _create_settings_sheet(self, wb):
        ws = wb.create_sheet("Einstellungen")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "F87171"
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 28

        settings = [
            ("System", ""),
            ("Version", "Trading Multi-Agent v3.0"),
            ("Erstellt am", datetime.now().strftime("%d.%m.%Y %H:%M")),
            ("", ""),
            ("Trading-Config", ""),
            ("Assets", os.getenv("TRADING_ASSETS", "EUR/USD,BTC/USD,XAU/USD,US500")),
            ("Strategie", os.getenv("TRADING_STRATEGY", "adaptive")),
            ("Max Risiko %", os.getenv("MAX_RISK_PCT", "2")),
            ("Stop Loss %", os.getenv("STOP_LOSS_PCT", "1.5")),
            ("Take Profit %", os.getenv("TAKE_PROFIT_PCT", "3.0")),
            ("Position Size €", os.getenv("POSITION_SIZE_EUR", "1000")),
            ("Auto Trade", os.getenv("AUTO_TRADE", "false")),
            ("Capital.com", "DEMO" if os.getenv("CAPITAL_DEMO", "true").lower() == "true" else "LIVE"),
            ("", ""),
            ("Demo-Kapital", ""),
            ("Startkapital €", os.getenv("DEMO_STARTKAPITAL", "1000")),
            ("Min. Konfidenz", os.getenv("MIN_CONFIDENCE", "70")),
            ("Schedule Stunden", os.getenv("SCHEDULE_INTERVAL_HOURS", "1")),
        ]
        for i, (k, v) in enumerate(settings, start=1):
            ws.row_dimensions[i].height = 22
            ck = ws.cell(row=i, column=1, value=k)
            cv = ws.cell(row=i, column=2, value=v)
            if v == "":
                ck.font = _font(bold=True, color=GOLD, size=12)
                ck.fill = _hfill(BG_HEADER)
            else:
                ck.font = _font(color=MUTED)
                ck.fill = _hfill(BG_CARD)
                cv.font = _font(color=TEXT)
                cv.fill = _hfill(BG_CARD)
            ck.border = _border()
            cv.border = _border()

    # ── Helpers ───────────────────────────────────────────────────────────
    def _write_header_row(self, ws, headers, widths, accent):
        for i, (h, w) in enumerate(zip(headers, widths), start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
            cell = ws.cell(row=1, column=i, value=h)
            cell.font      = _font(bold=True, color=WHITE, size=11)
            cell.fill      = _hfill(BG_HEADER)
            cell.alignment = _center()
            cell.border    = Border(
                bottom=Side(border_style="medium", color=accent),
                left=Side(border_style="thin", color="FF181830"),
                right=Side(border_style="thin", color="FF181830"),
            )

    def _write_data_row(self, ws, data, row_num, hl_col=None, hl_color=None):
        bg = BG_CARD if row_num % 2 == 0 else BG_DARK
        for i, value in enumerate(data, start=1):
            cell = ws.cell(row=row_num, column=i, value=value)
            cell.fill      = _hfill(bg)
            cell.border    = _border()
            cell.alignment = _left() if i == len(data) else _center()
            if hl_col and i == hl_col and hl_color:
                cell.font = _font(bold=True, color=hl_color)
            elif isinstance(value, str) and value.upper() in ("BUY", "LONG", "GEWONNEN"):
                cell.font = _font(bold=True, color=GREEN)
            elif isinstance(value, str) and value.upper() in ("SELL", "SHORT", "VERLOREN"):
                cell.font = _font(bold=True, color=RED)
            elif isinstance(value, (int, float)) and value > 0 and "€" not in str(value):
                cell.font = _font(color=GREEN)
            elif isinstance(value, (int, float)) and value < 0:
                cell.font = _font(color=RED)
            else:
                cell.font = _font(color=TEXT)
        ws.row_dimensions[row_num].height = 20

    def _load_wb(self):
        return openpyxl.load_workbook(self.path)

    # ── Public Methods ────────────────────────────────────────────────────
    def save_analysis(self, result: dict):
        try:
            wb = self._load_wb()
            ws = wb["Analyse-Log"]
            row = ws.max_row + 1
            now = datetime.now()
            for d in result.get("decisions", []):
                if d.get("action") == "hold":
                    continue
                color = GREEN if d["action"] == "buy" else RED
                self._write_data_row(ws, [
                    now.strftime("%d.%m.%Y"),
                    now.strftime("%H:%M:%S"),
                    d.get("asset", ""),
                    d.get("action", "").upper(),
                    f"{d.get('confidence', 0)}%",
                    f"{d.get('stopLoss', 0):.1f}%",
                    f"{d.get('takeProfit', 0):.1f}%",
                    f"{d.get('riskReward', 0):.1f}:1",
                    f"{result.get('sessionScore', 0)}/100",
                    d.get("urgency", ""),
                    d.get("summary", "")[:200],
                ], row, hl_col=4, hl_color=color)
                row += 1
            wb.save(self.path)
        except Exception as e:
            log.error(f"save_analysis Fehler: {e}")

    def save_trade(self, trade: dict):
        try:
            wb  = self._load_wb()
            ws  = wb["Trades"]
            row = ws.max_row + 1
            now = datetime.now()
            direction = trade.get("direction", trade.get("action", ""))
            ergebnis  = trade.get("ergebnis", "offen")
            pnl       = trade.get("pnl", 0)
            color     = GREEN if direction in ("long", "buy", "BUY", "LONG") else RED

            self._write_data_row(ws, [
                now.strftime("%d.%m.%Y"),
                now.strftime("%H:%M:%S"),
                trade.get("asset", ""),
                direction.upper(),
                f"€{trade.get('einsatz', trade.get('size', 0)):.2f}",
                f"{trade.get('sl_pct', trade.get('stopLoss', 0)):.1f}%",
                f"{trade.get('tp_pct', trade.get('takeProfit', 0)):.1f}%",
                f"{trade.get('rr', 0):.1f}:1",
                trade.get("status", "offen"),
                ergebnis,
                pnl,
                trade.get("kapital_danach", ""),
            ], row, hl_col=4, hl_color=color)
            wb.save(self.path)
        except Exception as e:
            log.error(f"save_trade Fehler: {e}")

    def save_demo_snapshot(self, snapshot: dict):
        """Speichert einen täglichen Kapital-Snapshot"""
        try:
            wb  = self._load_wb()
            ws  = wb["Demo-Kapital"]
            row = ws.max_row + 1
            pnl = snapshot.get("pnl", 0)
            self._write_data_row(ws, [
                snapshot.get("datum", ""),
                pnl,
                snapshot.get("kumulativ_pnl", 0),
                snapshot.get("kapital", 0),
                snapshot.get("trades", 0),
                snapshot.get("gewonnen", 0),
                snapshot.get("verloren", 0),
                f"{snapshot.get('win_rate', 0):.1f}%",
            ], row, hl_col=2, hl_color=GREEN if pnl >= 0 else RED)
            wb.save(self.path)
        except Exception as e:
            log.error(f"save_demo_snapshot Fehler: {e}")

    def get_trade_history(self) -> dict:
        try:
            wb = self._load_wb()
            ws = wb["Trades"]
            trades = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if any(c is not None for c in row):
                    trades.append({
                        "date": row[0], "time": row[1], "asset": row[2],
                        "direction": row[3], "size": row[4],
                        "sl": row[5], "tp": row[6],
                        "status": row[8], "ergebnis": row[9], "pnl": row[10],
                    })
            return {"trades": trades, "count": len(trades)}
        except Exception as e:
            log.error(f"get_trade_history Fehler: {e}")
            return {"trades": [], "count": 0}
