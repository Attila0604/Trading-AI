import os, logging
from datetime import datetime
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

log = logging.getLogger(__name__)

# Colors
BG_DARK   = "FF030309"
BG_CARD   = "FF0D0D18"
BG_HEADER = "FF0F0F1A"
GOLD      = "FFF5C518"
CYAN      = "FF22D3EE"
GREEN     = "FF10B981"
RED       = "FFF43F5E"
MUTED     = "FF525270"
TEXT      = "FFC8C8E4"
WHITE     = "FFFFFFFF"


def _hfill(color: str) -> PatternFill:
    return PatternFill(start_color=color, end_color=color, fill_type="solid")

def _font(bold=False, color=TEXT, size=11):
    return Font(name="Consolas", bold=bold, color=color, size=size)

def _border():
    side = Side(border_style="thin", color="FF181830")
    return Border(left=side, right=side, top=side, bottom=side)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)

def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


class ExcelTracker:
    SHEETS = ["Analyse-Log", "Trades", "Positionen", "Performance", "Einstellungen"]

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

        self._create_analyse_sheet(wb)
        self._create_trades_sheet(wb)
        self._create_positions_sheet(wb)
        self._create_performance_sheet(wb)
        self._create_settings_sheet(wb)

        wb.save(self.path)
        log.info("✅ Excel erstellt")

    def _create_analyse_sheet(self, wb):
        ws = wb.create_sheet("Analyse-Log")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "F5C518"
        headers = ["Datum", "Uhrzeit", "Asset", "Action", "Konfidenz", "Signal", "SL%", "TP%", "R:R", "Urgency", "Session Score", "Zusammenfassung"]
        widths  = [12, 10, 12, 10, 12, 15, 8, 8, 8, 12, 14, 50]
        self._write_header_row(ws, headers, widths, GOLD)
        ws.row_dimensions[1].height = 28

    def _create_trades_sheet(self, wb):
        ws = wb.create_sheet("Trades")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "22D3EE"
        headers = ["Datum", "Uhrzeit", "Asset", "Direction", "Size €", "Entry", "SL%", "TP%", "Deal ID", "Status", "P&L €", "P&L %"]
        widths  = [12, 10, 12, 12, 10, 12, 8, 8, 24, 12, 10, 10]
        self._write_header_row(ws, headers, widths, CYAN)
        ws.row_dimensions[1].height = 28

    def _create_positions_sheet(self, wb):
        ws = wb.create_sheet("Positionen")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "10B981"
        headers = ["Datum", "Asset", "Direction", "Entry", "Aktuell", "Size", "P&L €", "P&L %", "Status", "Deal ID"]
        widths  = [16, 12, 12, 12, 12, 10, 10, 10, 12, 24]
        self._write_header_row(ws, headers, widths, GREEN)
        ws.row_dimensions[1].height = 28

    def _create_performance_sheet(self, wb):
        ws = wb.create_sheet("Performance")
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "A855F7"
        headers = ["Datum", "Trades Total", "Wins", "Losses", "Win Rate %", "Gesamt P&L €", "Avg P&L €", "Bestes Trade", "Schlechtester Trade"]
        widths  = [14, 14, 10, 10, 12, 14, 12, 16, 20]
        self._write_header_row(ws, headers, widths, "A855F7")
        ws.row_dimensions[1].height = 28

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
        ]
        for i, (k, v) in enumerate(settings, start=1):
            ws.row_dimensions[i].height = 22
            cell_k = ws.cell(row=i, column=1, value=k)
            cell_v = ws.cell(row=i, column=2, value=v)
            if v == "":
                cell_k.font = _font(bold=True, color=GOLD, size=12)
                cell_k.fill = _hfill(BG_HEADER)
            else:
                cell_k.font = _font(color=MUTED)
                cell_k.fill = _hfill(BG_CARD)
                cell_v.font = _font(color=TEXT)
                cell_v.fill = _hfill(BG_CARD)
            cell_k.border = _border()
            cell_v.border = _border()

    def _write_header_row(self, ws, headers, widths, accent_color):
        for i, (h, w) in enumerate(zip(headers, widths), start=1):
            col = get_column_letter(i)
            ws.column_dimensions[col].width = w
            cell = ws.cell(row=1, column=i, value=h)
            cell.font      = _font(bold=True, color=WHITE, size=11)
            cell.fill      = _hfill(BG_HEADER)
            cell.alignment = _center()
            cell.border    = Border(
                bottom=Side(border_style="medium", color=accent_color),
                left=Side(border_style="thin", color="FF181830"),
                right=Side(border_style="thin", color="FF181830"),
            )

    def _write_data_row(self, ws, data: list, row_num: int, highlight_col: int = None, highlight_color: str = None):
        is_even = row_num % 2 == 0
        bg = BG_CARD if is_even else BG_DARK
        for i, value in enumerate(data, start=1):
            cell = ws.cell(row=row_num, column=i, value=value)
            cell.fill      = _hfill(bg)
            cell.border    = _border()
            cell.alignment = _center() if i != len(data) else _left()
            if highlight_col and i == highlight_col and highlight_color:
                cell.font = _font(bold=True, color=highlight_color)
            elif isinstance(value, (int, float)) and "€" not in str(value) and "%" not in str(value):
                cell.font = _font(color=CYAN)
            elif isinstance(value, str) and value in ("BUY", "LONG", "buy", "long"):
                cell.font = _font(bold=True, color=GREEN)
            elif isinstance(value, str) and value in ("SELL", "SHORT", "sell", "short"):
                cell.font = _font(bold=True, color=RED)
            else:
                cell.font = _font(color=TEXT)
        ws.row_dimensions[row_num].height = 20

    def _load_wb(self):
        return openpyxl.load_workbook(self.path)

    def save_analysis(self, result: dict):
        try:
            wb = self._load_wb()
            ws = wb["Analyse-Log"]
            row = ws.max_row + 1
            now = datetime.now()

            for decision in result.get("decisions", []):
                action = decision.get("action", "hold")
                if action == "hold":
                    continue
                color = GREEN if action == "buy" else RED if action == "sell" else MUTED
                self._write_data_row(ws, [
                    now.strftime("%d.%m.%Y"),
                    now.strftime("%H:%M:%S"),
                    decision.get("asset", ""),
                    action.upper(),
                    f"{decision.get('confidence', 0)}%",
                    decision.get("signal", decision.get("action", "")),
                    f"{decision.get('stopLoss', 0):.1f}%",
                    f"{decision.get('takeProfit', 0):.1f}%",
                    f"{decision.get('riskReward', 0):.1f}:1",
                    decision.get("urgency", ""),
                    f"{result.get('sessionScore', 0)}/100",
                    decision.get("summary", "")[:200],
                ], row, highlight_col=4, highlight_color=color)
                row += 1

            wb.save(self.path)
            log.info(f"✅ Analyse in Excel gespeichert ({row - ws.max_row - 1 + len(result.get('decisions', []))} Zeilen)")
        except Exception as e:
            log.error(f"save_analysis Fehler: {e}")

    def save_trade(self, trade: dict):
        try:
            wb = self._load_wb()
            ws = wb["Trades"]
            row = ws.max_row + 1
            now = datetime.now()
            direction = trade.get("direction", trade.get("action", ""))
            color = GREEN if direction in ("long", "buy", "BUY", "LONG") else RED

            self._write_data_row(ws, [
                now.strftime("%d.%m.%Y"),
                now.strftime("%H:%M:%S"),
                trade.get("asset", ""),
                direction.upper(),
                f"€{trade.get('size', 0):.0f}",
                trade.get("entry", "market"),
                f"{trade.get('stopLoss', 0):.1f}%",
                f"{trade.get('takeProfit', 0):.1f}%",
                trade.get("dealId", ""),
                trade.get("status", "executed"),
                "",
                "",
            ], row, highlight_col=4, highlight_color=color)

            wb.save(self.path)
            log.info(f"✅ Trade in Excel gespeichert: {trade.get('asset')} {direction.upper()}")
        except Exception as e:
            log.error(f"save_trade Fehler: {e}")

    def get_trade_history(self) -> dict:
        try:
            wb = self._load_wb()
            ws = wb["Trades"]
            trades = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if any(cell is not None for cell in row):
                    trades.append({
                        "date": row[0], "time": row[1], "asset": row[2],
                        "direction": row[3], "size": row[4], "entry": row[5],
                        "sl": row[6], "tp": row[7], "dealId": row[8], "status": row[9],
                    })
            return {"trades": trades, "count": len(trades)}
        except Exception as e:
            log.error(f"get_trade_history Fehler: {e}")
            return {"trades": [], "count": 0}
