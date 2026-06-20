"""risk.py — ATR-based stop-loss / take-profit (1.5x ATR stop, 3.0x ATR target = 1:2 RR).

Supports both directions: long stops below / targets above; short is the mirror.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RiskLevels:
    entry: float
    stop_loss: float
    take_profit: float
    risk_per_unit: float
    reward_per_unit: float
    rr: float
    side: str


def compute_levels(entry: float, atr: float, side: str = "long",
                   sl_mult: float = 1.5, tp_mult: float = 3.0) -> RiskLevels:
    if side == "long":
        stop_loss = entry - sl_mult * atr
        take_profit = entry + tp_mult * atr
    else:  # short
        stop_loss = entry + sl_mult * atr
        take_profit = entry - tp_mult * atr
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    return RiskLevels(entry, stop_loss, take_profit, risk, reward,
                      (reward / risk) if risk else 0.0, side)
