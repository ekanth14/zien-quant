"""
MarketScope Algorithmic AI Decision Engine
Deterministic, zero-latency local intelligence synthesizing technical indicators,
order-block zones, VWAP anchor, and volume dynamics into actionable trade setups:
- Recommended Action (Breakout Long, Pullback Long, Breakdown Short, Pullback Short, Range Bound, Avoid)
- Suggested Entry Zone
- Stop Loss (Invalidation Level)
- Target 1 & Target 2
- Risk-to-Reward Ratio (R:R)
- AI Conviction Rating & Multi-Factor Rationale
- Setup Grading (A+, A, B, C)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def evaluate_ai_decision(
    symbol: str,
    price: float,
    metrics: Dict[str, Any],
    vwap: Optional[float],
    support: float,
    resistance: float,
    rvol: float,
    order_blocks: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    closed_confirming: bool = True,
    atr: Optional[float] = None,
    supertrend: Optional[Dict[str, Any]] = None,
    cpr: Optional[Dict[str, Any]] = None,
    camarilla: Optional[Dict[str, Any]] = None,
    laya_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Computes actionable trade decision metrics based on multi-factor alignment.

    Conviction Rating Formula:
    --------------------------
    1. Base Conviction:
       - Breakout Long / Breakdown Short:
         aligned_score = score (Long) or (100 - score) (Short)
         vol_boost = min(12.0, max(0.0, (rvol - 1.0) * 8.0))
         base = min(96, round(aligned_score * 1.02 + vol_boost))
       - Pullback Long / Pullback Short:
         aligned_score = score (Long) or (100 - score) (Short)
         base = min(90, round(aligned_score * 0.98))
       - Range Bound / Avoid:
         base = 50
       - Overbought / Exhaustion:
         base = 75
    2. Conviction Capping Hurdles:
       - When R:R < 1.8 OR (RVOL < 1.0 on a breakout/breakdown setup):
         conviction = min(conviction, 60)
       - When R:R < 1.5:
         conviction = min(conviction, 40)
       - Final conviction is clamped between 0 and 100.

    Setup Grading (A+, A, B, C):
    ----------------------------
    - Grade A+: R:R >= 2.2 AND RVOL >= 1.5 AND abs(momentum) > 1.2% AND closed confirming candle.
    - Grade A:  R:R >= 1.8 AND score aligned with signal AND volume confirmed (RVOL >= 1.0).
    - Grade B:  R:R >= 1.5 with valid setup parameters.
    - Grade C:  R:R < 1.5 (Trade Avoided) or range-bound chop.
    """
    cfg = config or {}

    score_bo_long = int(cfg.get("score_breakout_long", 70))
    score_pb_long = int(cfg.get("score_pullback_long", 65))
    score_bd_short = int(cfg.get("score_breakdown_short", 30))
    score_pb_short = int(cfg.get("score_pullback_short", 35))

    rsi_pb_long_min = float(cfg.get("rsi_pullback_long_min", 42.0))
    rsi_pb_long_max = float(cfg.get("rsi_pullback_long_max", 68.0))
    rsi_pb_short_min = float(cfg.get("rsi_pullback_short_min", 32.0))
    rsi_pb_short_max = float(cfg.get("rsi_pullback_short_max", 58.0))
    rsi_ob = float(cfg.get("rsi_overbought", 76.0))

    min_risk_pct_bo = float(cfg.get("min_risk_pct_breakout", 0.006))
    min_risk_pct_pb = float(cfg.get("min_risk_pct_pullback", 0.005))

    min_rr_hurdle = float(cfg.get("min_rr_hurdle", 1.5))
    conv_rr_hurdle = float(cfg.get("conviction_rr_hurdle", 1.8))
    target1_mult = float(cfg.get("rr_target1_mult", 1.5))
    target2_mult = float(cfg.get("rr_target2_mult", 2.6))

    grade_aplus_rr = float(cfg.get("grade_aplus_rr", 2.2))
    grade_aplus_rvol = float(cfg.get("grade_aplus_rvol", 1.5))
    grade_aplus_mom = float(cfg.get("grade_aplus_momentum", 1.2))
    grade_a_rr = float(cfg.get("grade_a_rr", 1.8))

    score = metrics.get("score")
    if score is None:
        return {
            "action": "INSUFFICIENT DATA",
            "signal_type": "NEUTRAL",
            "grade": "C",
            "conviction_pct": 0,
            "entry_low": price,
            "entry_high": price,
            "stop_loss": round(price * 0.99, 2),
            "target_1": round(price * 1.01, 2),
            "target_2": round(price * 1.02, 2),
            "risk_reward_ratio": 1.0,
            "risk_amount": 0.0,
            "reward_amount": 0.0,
            "rationale": "Warming indicators: minimum history required for scoring.",
            "factors": ["Data history warm-up in progress"],
        }

    raw = metrics.get("raw", {})
    rsi = raw.get("rsi", 50.0)
    momentum = raw.get("momentum_pct", 0.0)
    trend_pct = raw.get("trend_pct", 0.0)
    vwap_dist = raw.get("vwap_dist_pct", 0.0)

    bull_zones = [z for z in order_blocks if z.get("type") == "bullish"]
    bear_zones = [z for z in order_blocks if z.get("type") == "bearish"]

    setup_found = False
    action = "RANGE BOUND / AVOID"
    signal_type = "NEUTRAL"
    entry_low = round(support, 2)
    entry_high = round(resistance, 2)
    sl = round(support * 0.995, 2)
    t1 = round(resistance, 2)
    t2 = round(resistance * 1.01, 2)
    risk = round(price - support, 2)
    rr = 1.0
    conviction = 50
    factors: List[str] = []
    rationale = ""
    is_breakout = False

    # 1. STRONG BUY / BREAKOUT LONG SETUP
    if score >= score_bo_long and momentum > 0.3 and price > (vwap or 0):
        setup_found = True
        is_breakout = True
        action = "BREAKOUT LONG"
        signal_type = "BUY"
        entry_high = round(price * 1.001, 2)
        entry_low = round(price * 0.998, 2)
        if bull_zones:
            sl = round(bull_zones[-1]["low"] * 0.997, 2)
            sl_reason = f"below bullish order-block zone low (₹{bull_zones[-1]['low']})"
        else:
            sl = round(support * 0.996, 2)
            sl_reason = f"below rolling 20-bar support (₹{support})"

        risk = max(price * min_risk_pct_bo, round(price - sl, 2))
        sl = round(price - risk, 2)

        eff_atr = float(atr) if (atr is not None and atr > 0) else float(raw.get("atr") or 0.0)
        if eff_atr <= 0:
            eff_atr = max(price * 0.008, 1.0)

        # Breakout target logic: max(next structural level beyond price, price + 1.5*ATR(14)) with T2 = price + 2.5*ATR
        atr_t1 = round(price + 1.5 * eff_atr, 2)
        if resistance > price:
            t1 = max(round(resistance, 2), atr_t1)
        else:
            t1 = atr_t1
        t2 = round(price + 2.5 * eff_atr, 2)

        rr = round((t1 - price) / max(0.1, risk), 2)
        vol_boost = min(12.0, max(0.0, (rvol - 1.0) * 8.0))
        conviction = min(96, round(score * 1.02 + vol_boost))

        factors = [
            f"Directional Score at {score}/100 with bullish EMA separation (+{trend_pct:.2f}%)",
            f"Price trading firmly above session VWAP (+{vwap_dist:.2f}%)",
            f"Volume expansion at {rvol:.2f}x average",
            f"Active demand defense {sl_reason}",
        ]
        rationale = f"Bullish breakout structure confirmed above VWAP. Order flow aligns with upward trend momentum. Target ₹{t1} offers favorable {rr} R:R."

    # 2. PULLBACK BUY SETUP (BUY ON RETEST / DIP)
    elif score >= score_pb_long and trend_pct > 0 and rsi_pb_long_min <= rsi <= rsi_pb_long_max:
        setup_found = True
        action = "PULLBACK LONG"
        signal_type = "BUY"
        ref_entry = vwap if (vwap and vwap > 0) else price
        entry_low = round(min(price, ref_entry) * 0.998, 2)
        entry_high = round(max(price, ref_entry) * 1.002, 2)

        sl = round(support * 0.995, 2)
        risk = max(price * min_risk_pct_pb, round(price - sl, 2))
        sl = round(price - risk, 2)

        t1 = round(resistance, 2)
        if t1 <= price:
            t1 = round(price + risk * 1.8, 2)
        t2 = round(t1 + risk * target1_mult, 2)

        rr = round((t1 - price) / max(0.1, risk), 2)
        conviction = min(90, round(score * 0.98))

        factors = [
            f"Macro EMA trend bullish (+{trend_pct:.2f}%) on healthy RSI retest ({rsi:.1f})",
            f"Price consolidating near session VWAP demand zone",
            f"Tight invalidation below rolling support (₹{support})",
        ]
        rationale = f"Constructive pullback into intraday support. Favorable R:R ({rr}) with defined invalidation at ₹{sl}."

    # 3. STRONG SELL / BREAKDOWN SHORT SETUP
    elif score <= score_bd_short and momentum < -0.3 and price < (vwap or 999999):
        setup_found = True
        is_breakout = True
        action = "BREAKDOWN SHORT"
        signal_type = "SELL"
        entry_low = round(price * 0.999, 2)
        entry_high = round(price * 1.002, 2)

        if bear_zones:
            sl = round(bear_zones[-1]["high"] * 1.003, 2)
            sl_reason = f"above bearish order-block zone high (₹{bear_zones[-1]['high']})"
        else:
            sl = round(resistance * 1.004, 2)
            sl_reason = f"above rolling 20-bar resistance (₹{resistance})"

        risk = max(price * min_risk_pct_bo, round(sl - price, 2))
        sl = round(price + risk, 2)

        eff_atr = float(atr) if (atr is not None and atr > 0) else float(raw.get("atr") or 0.0)
        if eff_atr <= 0:
            eff_atr = max(price * 0.008, 1.0)

        # Breakdown target logic: min(next structural level beyond price, price - 1.5*ATR(14)) with T2 = price - 2.5*ATR
        atr_t1 = round(price - 1.5 * eff_atr, 2)
        if support < price:
            t1 = min(round(support, 2), atr_t1)
        else:
            t1 = atr_t1
        t2 = round(price - 2.5 * eff_atr, 2)

        rr = round((price - t1) / max(0.1, risk), 2)
        vol_boost = min(12.0, max(0.0, (rvol - 1.0) * 8.0))
        conviction = min(96, round((100 - score) * 1.02 + vol_boost))

        factors = [
            f"Directional Score at {score}/100 with heavy bearish trend ({trend_pct:.2f}%)",
            f"Price submerged below session VWAP ({vwap_dist:.2f}%)",
            f"Downward volume acceleration ({rvol:.2f}x normal)",
            f"Clear invalidation ceiling {sl_reason}",
        ]
        rationale = f"Structural breakdown confirmed below VWAP with supply pressure. Downside target at ₹{t1} with {rr} R:R."

    # 4. PULLBACK SHORT SETUP (SELL ON RALLY)
    elif score <= score_pb_short and trend_pct < 0 and rsi_pb_short_min <= rsi <= rsi_pb_short_max:
        setup_found = True
        action = "PULLBACK SHORT"
        signal_type = "SELL"
        ref_entry = vwap if (vwap and vwap > 0) else price
        entry_low = round(min(price, ref_entry) * 0.998, 2)
        entry_high = round(max(price, ref_entry) * 1.002, 2)

        sl = round(resistance * 1.005, 2)
        risk = max(price * min_risk_pct_pb, round(sl - price, 2))
        sl = round(price + risk, 2)

        t1 = round(support, 2)
        if t1 >= price:
            t1 = round(price - risk * 1.8, 2)
        t2 = round(t1 - risk * target1_mult, 2)

        rr = round((price - t1) / max(0.1, risk), 2)
        conviction = min(90, round((100 - score) * 0.98))

        factors = [
            f"Primary trend bearish with RSI relief rally stalling at {rsi:.1f}",
            f"Price retesting overhead VWAP resistance level",
            f"Defensive stop loss placed above resistance (₹{resistance})",
        ]
        rationale = f"Counter-trend bounce exhausting into VWAP supply band. Trend continuation anticipated with target ₹{t1}."

    # 5. OVERBOUGHT / EXHAUSTION
    elif rsi >= rsi_ob:
        return {
            "action": "TAKE PROFIT / EXHAUSTION",
            "signal_type": "NEUTRAL",
            "grade": "C",
            "conviction_pct": 75,
            "entry_low": price,
            "entry_high": price,
            "stop_loss": round(support, 2),
            "target_1": round(resistance, 2),
            "target_2": round(resistance * 1.01, 2),
            "risk_reward_ratio": 1.0,
            "risk_amount": round(price - support, 2),
            "reward_amount": round(resistance - price, 2),
            "rationale": f"Oscillator stretch alert: RSI({rsi:.1f}) in extreme territory. Risk of mean reversion is elevated; protect gains.",
            "factors": [f"RSI extended at {rsi:.1f}", "Reversal risk high", "Trailing stops advised"],
        }

    # 6. NEUTRAL / RANGE-BOUND / CHOP
    else:
        return {
            "action": "RANGE BOUND / AVOID",
            "signal_type": "NEUTRAL",
            "grade": "C",
            "conviction_pct": 50,
            "entry_low": round(support, 2),
            "entry_high": round(resistance, 2),
            "stop_loss": round(support * 0.995, 2),
            "target_1": round(resistance, 2),
            "target_2": round(resistance * 1.01, 2),
            "risk_reward_ratio": 1.0,
            "risk_amount": round(price - support, 2),
            "reward_amount": round(resistance - price, 2),
            "rationale": f"Neutral composite score ({score}/100). Market in sideways consolidation between ₹{support} and ₹{resistance}. Await breakout.",
            "factors": ["Score in neutral corridor", "No directional breakout edge", "Consolidation regime"],
        }

    # Directional setup evaluated: enforce R:R gate and conviction capping
    if setup_found:
        factors.append(f"Calculated R:R: 1:{rr:.2f}")

        # 2.1 Enforce the documented R:R gate: if R:R < 1.5 -> AVOID - POOR R:R
        if rr < min_rr_hurdle:
            action = "AVOID - POOR R:R"
            signal_type = "NEUTRAL"
            conviction = min(40, conviction)
            grade = "C"
            factors.append(f"R:R ({rr:.2f}) below minimum {min_rr_hurdle:.1f} hurdle: trade avoided")
            rationale = (
                f"Trade avoided: computed risk-to-reward ratio of 1:{rr:.2f} fails the mandatory "
                f"{min_rr_hurdle:.1f} minimum hurdle. Invalidation parameters offer poor risk asymmetry."
            )
            return {
                "action": action,
                "signal_type": signal_type,
                "grade": grade,
                "conviction_pct": conviction,
                "entry_low": None,
                "entry_high": None,
                "stop_loss": None,
                "target_1": None,
                "target_2": None,
                "risk_reward_ratio": rr,
                "risk_amount": 0.0,
                "reward_amount": 0.0,
                "rationale": rationale,
                "factors": factors,
                "gated": 1,
                "gate_reason": f"R:R ({rr:.2f}) < {min_rr_hurdle:.1f}",
                "shadow_stop_loss": sl,
                "shadow_target_1": t1,
                "shadow_target_2": t2,
                "shadow_rr": rr,
            }

        # 2.2 Conviction capping
        if rr < conv_rr_hurdle or (is_breakout and rvol < 1.0):
            conviction = min(conviction, 60)
            if rr < conv_rr_hurdle:
                factors.append(f"Conviction capped at 60 (R:R {rr:.2f} < {conv_rr_hurdle:.1f})")
            if is_breakout and rvol < 1.0:
                factors.append(f"Conviction capped at 60 (Breakout RVOL {rvol:.2f} < 1.0)")

        # 2.3 Quant indicators from PDF reference (Supertrend, CPR, Camarilla)
        if supertrend and isinstance(supertrend, dict):
            st_dir = supertrend.get("direction")
            st_val = supertrend.get("value")
            if st_dir == 1:
                factors.append(f"Supertrend (10, 3.0) bullish trailing support at ₹{st_val}")
                if signal_type == "BUY":
                    conviction = min(96, conviction + 3)
            elif st_dir == -1:
                factors.append(f"Supertrend (10, 3.0) bearish trailing resistance at ₹{st_val}")
                if signal_type == "SELL":
                    conviction = min(96, conviction + 3)

        if cpr and isinstance(cpr, dict):
            if cpr.get("is_narrow"):
                factors.append(f"Narrow CPR ({cpr.get('width_pct'):.2f}%) indicates high trending breakout probability")
            if cpr.get("tc") and price > cpr["tc"]:
                factors.append(f"Price trading above CPR Top Central (₹{cpr['tc']})")
            elif cpr.get("bc") and price < cpr["bc"]:
                factors.append(f"Price trading below CPR Bottom Central (₹{cpr['bc']})")

        if camarilla and isinstance(camarilla, dict):
            if camarilla.get("h4") and price >= camarilla["h4"]:
                factors.append(f"Camarilla H4 breakout level (₹{camarilla['h4']}) active")
            elif camarilla.get("l4") and price <= camarilla["l4"]:
                factors.append(f"Camarilla L4 breakdown level (₹{camarilla['l4']}) active")

        # 2.4 Laya AI System 1 Decision Integration
        if laya_decision and isinstance(laya_decision, dict):
            laya_choice = str(laya_decision.get("choice", "")).lower()
            laya_conf = float(laya_decision.get("confidence", 0.0))
            if (signal_type == "BUY" and laya_choice == "bullish") or (signal_type == "SELL" and laya_choice == "bearish"):
                factors.append(f"Laya AI decision confirmed: {laya_choice.title()} ({laya_conf*100:.0f}% confidence)")
                conviction = min(98, round(conviction * 1.05 + laya_conf * 5))
            elif laya_choice == "neutral":
                factors.append("Laya AI decision: Neutral / Range-bound consolidation")
            elif (signal_type == "BUY" and laya_choice == "bearish") or (signal_type == "SELL" and laya_choice == "bullish"):
                factors.append(f"Laya AI divergence: AI indicates {laya_choice.title()}")
                conviction = max(35, conviction - 15)

        # 2.5 Grading
        is_closed = closed_confirming if closed_confirming is not None else metrics.get("is_closed", True)
        if rr >= grade_aplus_rr and rvol >= grade_aplus_rvol and abs(momentum) > grade_aplus_mom and is_closed:
            grade = "A+"
        elif rr >= grade_a_rr and rvol >= 1.0 and ((signal_type == "BUY" and score >= 65) or (signal_type == "SELL" and score <= 35)):
            grade = "A"
        elif rr >= 1.5:
            grade = "B"
        else:
            grade = "C"

        # Laya confirmation elevates Grade A to Grade A+
        if laya_decision and isinstance(laya_decision, dict):
            laya_choice = str(laya_decision.get("choice", "")).lower()
            laya_conf = float(laya_decision.get("confidence", 0.0))
            if grade == "A" and laya_conf >= 0.60 and (
                (signal_type == "BUY" and laya_choice == "bullish") or
                (signal_type == "SELL" and laya_choice == "bearish")
            ):
                grade = "A+"

        reward_amt = round(abs(t1 - price), 2) if t1 is not None else 0.0

        return {
            "action": action,
            "signal_type": signal_type,
            "grade": grade,
            "conviction_pct": max(0, min(100, conviction)),
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "risk_reward_ratio": rr,
            "risk_amount": round(risk, 2),
            "reward_amount": reward_amt,
            "rationale": rationale,
            "factors": factors,
            "laya_decision": laya_decision,
        }

    return {
        "action": action,
        "signal_type": signal_type,
        "grade": "C",
        "conviction_pct": conviction,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": sl,
        "target_1": t1,
        "target_2": t2,
        "risk_reward_ratio": rr,
        "risk_amount": 0.0,
        "reward_amount": 0.0,
        "rationale": rationale,
        "factors": factors,
    }

