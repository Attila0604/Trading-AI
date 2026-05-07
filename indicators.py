"""
Technische Indikatoren - reines Python, keine Dependencies.
RSI, MACD, EMA, SMA, Bollinger Bands aus OHLC-Kerzen.
"""
from typing import Optional


def sma(values: list[float], period: int) -> Optional[float]:
    """Simple Moving Average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> Optional[float]:
    """Exponential Moving Average (letzter Wert)."""
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for v in values[period:]:
        ema_val = (v - ema_val) * multiplier + ema_val
    return ema_val


def ema_series(values: list[float], period: int) -> list[float]:
    """EMA-Serie für alle Werte ab Index `period-1`."""
    if len(values) < period:
        return []
    result = []
    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    result.append(ema_val)
    for v in values[period:]:
        ema_val = (v - ema_val) * multiplier + ema_val
        result.append(ema_val)
    return result


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index (Wilder's Smoothing)."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """MACD: Linie, Signal-Linie, Histogram."""
    if len(closes) < slow + signal:
        return None
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    diff = len(fast_series) - len(slow_series)
    fast_aligned = fast_series[diff:]
    macd_line = [f - s for f, s in zip(fast_aligned, slow_series)]
    if len(macd_line) < signal:
        return None
    signal_line = ema_series(macd_line, signal)
    macd_val = macd_line[-1]
    signal_val = signal_line[-1]
    histogram = macd_val - signal_val
    return {
        "macd":      round(macd_val, 4),
        "signal":    round(signal_val, 4),
        "histogram": round(histogram, 4),
        "trend":     "bullish" if histogram > 0 else "bearish" if histogram < 0 else "neutral",
    }


def bollinger_bands(closes: list[float], period: int = 20, std_mult: float = 2.0) -> Optional[dict]:
    """Bollinger Bands."""
    if len(closes) < period:
        return None
    recent = closes[-period:]
    middle = sum(recent) / period
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = variance ** 0.5
    upper = middle + (std_mult * std)
    lower = middle - (std_mult * std)
    current = closes[-1]
    if current >= upper:
        position = "breakout_up"
    elif current >= middle + (std * 0.5):
        position = "upper"
    elif current <= lower:
        position = "breakout_down"
    elif current <= middle - (std * 0.5):
        position = "lower"
    else:
        position = "middle"
    return {
        "upper":    round(upper, 5),
        "middle":   round(middle, 5),
        "lower":    round(lower, 5),
        "position": position,
        "width_pct": round((upper - lower) / middle * 100, 2),
    }


def calculate_all_indicators(candles: list[dict]) -> dict:
    """Alle Indikatoren auf einen Schlag berechnen."""
    if not candles or len(candles) < 30:
        return {"error": f"Zu wenige Kerzen: {len(candles) if candles else 0}"}
    
    closes = [c["close"] for c in candles]
    current = closes[-1]
    
    e20  = ema(closes, 20)
    e50  = ema(closes, 50)
    e200 = ema(closes, 200) if len(closes) >= 200 else None
    
    # Trend via EMA-Alignment
    trend = "sideways"
    if e20 and e50:
        if current > e20 > e50:
            trend = "uptrend"
        elif current < e20 < e50:
            trend = "downtrend"
    
    rsi_val = rsi(closes, 14)
    macd_data = macd(closes)
    bb_data = bollinger_bands(closes)
    
    # Signal & Confluence
    signal = "neutral"
    confluence = 5
    if rsi_val:
        if rsi_val < 30:
            signal = "buy"; confluence += 1
        elif rsi_val > 70:
            signal = "sell"; confluence += 1
    if macd_data:
        if macd_data["trend"] == "bullish" and trend == "uptrend":
            signal = "strong buy" if signal == "buy" else "buy"
            confluence += 2
        elif macd_data["trend"] == "bearish" and trend == "downtrend":
            signal = "strong sell" if signal == "sell" else "sell"
            confluence += 2
    if bb_data and bb_data["position"] in ("breakout_up", "breakout_down"):
        confluence += 1
    confluence = min(10, max(1, confluence))
    
    ema_alignment = "mixed"
    if e20 and e50:
        if e20 > e50:
            ema_alignment = "bullish"
        elif e20 < e50:
            ema_alignment = "bearish"
    
    return {
        "currentPrice":    round(current, 5),
        "trend":           trend,
        "signal":          signal,
        "rsi":             rsi_val,
        "macd":            macd_data,
        "ema20":           round(e20, 5) if e20 else None,
        "ema50":           round(e50, 5) if e50 else None,
        "ema200":          round(e200, 5) if e200 else None,
        "emaAlignment":    ema_alignment,
        "bollinger":       bb_data,
        "confluenceScore": confluence,
        "candleCount":     len(candles),
    }
