"""strategy.py — Price-Action-First engine, long AND short, pattern-strength scored.

Decision flow per side:
  SETUP   (level)      long: close within tol of support; short: within tol of resistance.
  TRIGGER (candles)    at least one candlestick signal in-direction must exist — either a
                       patterns.py hit (the 10-detector library, now the PRIMARY trigger)
                       or a hand-coded pin/engulfing.
  CONFIRM (indicators) long: close > EMA200 & 40<=RSI<=60; short: close < EMA200 & 40<=RSI<=60.
  SCORE   (strength)   weighted candlestick/movement score must clear `min_score`
                       (replaces the old binary gate). Higher score = cleaner setup.
If both sides qualify on a bar, the higher-scoring one wins.
"""
from __future__ import annotations
import pandas as pd
import patterns


class PriceActionStrategy:
    def __init__(self, support_tol: float = 0.005, rsi_low: float = 40.0,
                 rsi_high: float = 60.0, min_score: float = 2.5):
        self.support_tol = support_tol
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.min_score = min_score

    # ---- pattern-strength score for one direction ----
    def _score(self, df: pd.DataFrame, side: str):
        row = df.iloc[-1]
        candles = df[["open", "high", "low", "close"]].to_dict("records")
        want = "bullish" if side == "long" else "bearish"
        pats = [n for n, s in patterns.detect(candles, len(candles) - 1) if s == want]

        score = 1.0 * len(pats)                                   # (1) patterns as trigger
        if side == "long":
            trigger = bool(row["bullish_pin"] or row["bullish_engulfing"])
            score += 1.5 if trigger else 0.0
            score += float(row["close_pos"])                     # closed near high
            score += float(row["body_to_range"])                 # strong body
            score += 0.5 if row["range_expansion"] else 0.0      # (3) movement: expansion
            score += 0.5 if row["up_move3"] else 0.0             # (3) movement: 3-bar HH/HL
        else:
            trigger = bool(row["bearish_pin"] or row["bearish_engulfing"])
            score += 1.5 if trigger else 0.0
            score += 1.0 - float(row["close_pos"])               # closed near low
            score += float(row["body_to_range"])
            score += 0.5 if row["range_expansion"] else 0.0
            score += 0.5 if row["down_move3"] else 0.0
        has_candle_signal = trigger or len(pats) > 0
        return score, pats, has_candle_signal

    def evaluate(self, df: pd.DataFrame):
        row = df.iloc[-1]
        if any(pd.isna(row[k]) for k in ("ema200", "rsi14", "atr14", "support", "resistance")):
            return None

        rsi_reset = self.rsi_low <= row["rsi14"] <= self.rsi_high
        cands = []

        # ---- LONG ----
        near_support = abs(row["close"] - row["support"]) / row["support"] <= self.support_tol
        if near_support and row["close"] > row["ema200"] and rsi_reset:
            sc, pats, ok = self._score(df, "long")
            if ok and sc >= self.min_score:
                cands.append(("long", sc, pats, float(row["support"])))

        # ---- SHORT (mirror) ----
        near_resist = abs(row["close"] - row["resistance"]) / row["resistance"] <= self.support_tol
        if near_resist and row["close"] < row["ema200"] and rsi_reset:
            sc, pats, ok = self._score(df, "short")
            if ok and sc >= self.min_score:
                cands.append(("short", sc, pats, float(row["resistance"])))

        if not cands:
            return None
        side, score, pats, level = max(cands, key=lambda x: x[1])
        return {
            "side": side,
            "entry": float(row["close"]),
            "atr": float(row["atr14"]),
            "score": round(score, 2),
            "reasons": {
                "level": round(level, 2),
                "patterns": pats,
                "rsi": round(float(row["rsi14"]), 1),
                "close_pos": round(float(row["close_pos"]), 2),
                "body_to_range": round(float(row["body_to_range"]), 2),
                "range_expansion": bool(row["range_expansion"]),
                "movement": bool(row["up_move3"] if side == "long" else row["down_move3"]),
            },
        }

    # convenience: keep a long-only entry point for callers that want it
    def evaluate_long(self, df: pd.DataFrame):
        sig = self.evaluate(df)
        return sig if (sig and sig["side"] == "long") else None
