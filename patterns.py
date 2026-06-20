"""Rule-based Japanese candlestick pattern detection (pure stdlib).

Each detector looks at candle index i (and a few preceding candles) and returns
a signal in {"bullish", "bearish", "neutral"} or None if the pattern is absent.
`detect(candles, i)` runs them all and returns a list of (name, signal).

Candles are dicts: {date, open, high, low, close, volume}. No external deps so
the skill needs zero installation.
"""


def _body(c):
    return abs(c["close"] - c["open"])


def _rng(c):
    return c["high"] - c["low"]


def _upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def _lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def _is_green(c):
    return c["close"] > c["open"]


def _is_red(c):
    return c["close"] < c["open"]


def _mid(c):
    return (c["open"] + c["close"]) / 2.0


def trend(candles, i, lookback=10):
    """Crude prior-trend context: 'up', 'down', or 'flat' just before bar i.
    Hammer vs hanging man (same shape) depends on this."""
    j = i - 1
    if j - lookback < 0:
        return "flat"
    past, now = candles[j - lookback]["close"], candles[j]["close"]
    chg = (now - past) / past if past else 0
    if chg > 0.02:
        return "up"
    if chg < -0.02:
        return "down"
    return "flat"


# ---- single-candle patterns -------------------------------------------------

def doji(candles, i):
    c = candles[i]
    r = _rng(c)
    if r <= 0:
        return None
    if _body(c) <= 0.1 * r:
        uw, lw = _upper_wick(c), _lower_wick(c)
        if lw >= 0.6 * r and uw <= 0.1 * r:
            return "bullish"      # dragonfly
        if uw >= 0.6 * r and lw <= 0.1 * r:
            return "bearish"      # gravestone
        return "neutral"
    return None


def marubozu(candles, i):
    c = candles[i]
    r = _rng(c)
    if r <= 0 or _body(c) < 0.9 * r:
        return None
    return "bullish" if _is_green(c) else "bearish"


def hammer_family(candles, i):
    """Hammer / hanging man / inverted hammer / shooting star (shape + trend)."""
    c = candles[i]
    r = _rng(c)
    b = _body(c)
    if r <= 0 or b > 0.4 * r:
        return None
    uw, lw = _upper_wick(c), _lower_wick(c)
    t = trend(candles, i)
    if lw >= 2 * b and uw <= b:          # long lower wick
        return "bullish" if t == "down" else ("bearish" if t == "up" else "neutral")
    if uw >= 2 * b and lw <= b:          # long upper wick
        return "bearish" if t == "up" else ("bullish" if t == "down" else "neutral")
    return None


def spinning_top(candles, i):
    c = candles[i]
    r = _rng(c)
    b = _body(c)
    if r <= 0 or not (0.1 * r < b <= 0.4 * r):
        return None
    if _upper_wick(c) >= b and _lower_wick(c) >= b:
        return "neutral"
    return None


# ---- two-candle patterns ----------------------------------------------------

def engulfing(candles, i):
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if _is_red(p) and _is_green(c) and c["close"] >= p["open"] and c["open"] <= p["close"]:
        return "bullish"
    if _is_green(p) and _is_red(c) and c["open"] >= p["close"] and c["close"] <= p["open"]:
        return "bearish"
    return None


def harami(candles, i):
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    inside = max(c["open"], c["close"]) <= max(p["open"], p["close"]) and \
        min(c["open"], c["close"]) >= min(p["open"], p["close"])
    if not inside or _body(p) <= 0:
        return None
    if _is_red(p) and _is_green(c):
        return "bullish"
    if _is_green(p) and _is_red(c):
        return "bearish"
    return None


def piercing_dark_cloud(candles, i):
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    if _is_red(p) and _is_green(c) and c["open"] < p["low"] and \
            c["close"] > _mid(p) and c["close"] < p["open"]:
        return "bullish"          # piercing line
    if _is_green(p) and _is_red(c) and c["open"] > p["high"] and \
            c["close"] < _mid(p) and c["close"] > p["open"]:
        return "bearish"          # dark cloud cover
    return None


def tweezer(candles, i):
    if i < 1:
        return None
    p, c = candles[i - 1], candles[i]
    tol = 0.0015 * c["close"]
    t = trend(candles, i)
    if abs(c["low"] - p["low"]) <= tol and t == "down":
        return "bullish"          # tweezer bottom
    if abs(c["high"] - p["high"]) <= tol and t == "up":
        return "bearish"          # tweezer top
    return None


# ---- three-candle patterns --------------------------------------------------

def star(candles, i):
    """Morning star (bullish) / evening star (bearish)."""
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    small = _body(b) <= 0.5 * _body(a) if _body(a) else False
    if _is_red(a) and small and _is_green(c) and c["close"] > _mid(a):
        return "bullish"
    if _is_green(a) and small and _is_red(c) and c["close"] < _mid(a):
        return "bearish"
    return None


def three_soldiers_crows(candles, i):
    if i < 2:
        return None
    a, b, c = candles[i - 2], candles[i - 1], candles[i]
    if all(_is_green(x) for x in (a, b, c)) and a["close"] < b["close"] < c["close"] \
            and b["open"] > a["open"] and c["open"] > b["open"]:
        return "bullish"          # three white soldiers
    if all(_is_red(x) for x in (a, b, c)) and a["close"] > b["close"] > c["close"] \
            and b["open"] < a["open"] and c["open"] < b["open"]:
        return "bearish"          # three black crows
    return None


DETECTORS = {
    "doji": doji,
    "marubozu": marubozu,
    "hammer/star": hammer_family,
    "spinning_top": spinning_top,
    "engulfing": engulfing,
    "harami": harami,
    "piercing/dark_cloud": piercing_dark_cloud,
    "tweezer": tweezer,
    "morning/evening_star": star,
    "three_soldiers/crows": three_soldiers_crows,
}


def detect(candles, i):
    """Return list of (pattern_name, signal) found at bar i."""
    out = []
    for name, fn in DETECTORS.items():
        sig = fn(candles, i)
        if sig:
            out.append((name, sig))
    return out
