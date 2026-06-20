"""features.py — load cached Indian-stock OHLCV + price-action & indicator features.

Price action is built from three things, all candle-derived:
  (1) Levels        — 20-bar rolling support/resistance.
  (2) Candle shape  — bullish/bearish pin bars & engulfing; body-to-range; close position.
  (3) Candle movement (multi-bar) — 3-bar higher-high/higher-low (and the bearish mirror),
                       and range expansion vs ATR.
patterns.py (the 10-detector library) is applied in strategy.py on top of these.

Data source: cached Kite payloads data/<SYMBOL>_<interval>.json (JSON array of
{date,open,high,low,close,volume}).
"""
from __future__ import annotations
import json
import os

import pandas as pd

try:
    import pandas_ta as ta
    _HAS_TA = True
except Exception:
    _HAS_TA = False

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ---- Built-in indicator fallbacks (Wilder smoothing == pandas_ta defaults) ----
def _ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False).mean()


def _rsi(s: pd.Series, length: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, length: int = 14) -> pd.Series:
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def load_cached(symbol: str, interval: str = "60minute") -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    df["timestamp"] = pd.to_datetime(df["date"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


# ---- (1) Levels ----
def add_support_resistance(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df["support"] = df["low"].rolling(window).min()
    df["resistance"] = df["high"].rolling(window).max()
    return df


# ---- (2) Candle shape + (3) multi-bar movement ----
def add_candlestick_signals(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l)
    lower_wick = df[["open", "close"]].min(axis=1) - l
    upper_wick = h - df[["open", "close"]].max(axis=1)

    # pin bars (wick rejection)
    df["bullish_pin"] = (lower_wick >= 2 * body) & (lower_wick > upper_wick) & (body > 0)
    df["bearish_pin"] = (upper_wick >= 2 * body) & (upper_wick > lower_wick) & (body > 0)
    # engulfing (momentum flip)
    prev_o, prev_c = o.shift(1), c.shift(1)
    df["bullish_engulfing"] = (prev_c < prev_o) & (c > o) & (o <= prev_c) & (c >= prev_o)
    df["bearish_engulfing"] = (prev_c > prev_o) & (c < o) & (o >= prev_c) & (c <= prev_o)

    # shape quality
    df["body_to_range"] = (body / rng).where(rng > 0, 0.0)
    df["close_pos"] = ((c - l) / rng).where(rng > 0, 0.5)   # 0=closed at low, 1=at high

    # multi-bar movement: 3-bar higher-high/higher-low and bearish mirror
    df["up_move3"] = (h > h.shift(1)) & (h.shift(1) > h.shift(2)) & \
                     (l > l.shift(1)) & (l.shift(1) > l.shift(2))
    df["down_move3"] = (h < h.shift(1)) & (h.shift(1) < h.shift(2)) & \
                       (l < l.shift(1)) & (l.shift(1) < l.shift(2))
    return df


# ---- Indicators (confirmation) + range expansion (needs ATR) ----
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if _HAS_TA:
        df["ema200"] = ta.ema(df["close"], length=200)
        df["rsi14"] = ta.rsi(df["close"], length=14)
        df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    else:
        df["ema200"] = _ema(df["close"], 200)
        df["rsi14"] = _rsi(df["close"], 14)
        df["atr14"] = _atr(df["high"], df["low"], df["close"], 14)
    df["range_expansion"] = (df["high"] - df["low"]) > df["atr14"]
    df["adx14"] = _adx(df["high"], df["low"], df["close"], 14)
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    return df


def _adx(h: pd.Series, l: pd.Series, c: pd.Series, length: int = 14) -> pd.Series:
    up, dn = h.diff(), -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    denom = (plus_di + minus_di)
    dx = (100 * (plus_di - minus_di).abs() / denom).where(denom != 0, 0.0)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def engineer(df: pd.DataFrame, sr_window: int = 20) -> pd.DataFrame:
    df = add_support_resistance(df, sr_window)
    df = add_candlestick_signals(df)
    df = add_indicators(df)
    return df
