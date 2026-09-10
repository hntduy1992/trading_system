"""
Zone Monitor & S/R Role Reversal Engine
Section 2.1 & 3.1: Dynamic HTF Zone Tracking & Two-Tier Breach Management
"""
from typing import List, Tuple, Optional, Dict, Any
from core.domain.models import Bar, HTFZone, SessionConfig, MarketRegime, InstrumentProfile, OrderSide
from core.domain.rules.swing_detector import SwingDetector, SwingType

class ZoneMonitor:
    @staticmethod
    def evaluate_zone_breaches(
        curr_bar_m3: Bar,
        config: SessionConfig,
        profile: Optional[InstrumentProfile] = None
    ) -> Dict[str, Any]:
        """
        Tier 1 Local Deterministic Breach & Role Reversal Reflex:
          - Distinguishes Wick Sweep (Liquidity Grab / BOF Potential) vs Confirmed Body Close Breach.
          - If Confirmed Breach: flips broken Resistance to Support, or broken Support to Resistance.
          - Updates SessionConfig zones dynamically in RAM without latency.
        """
        buffer = profile.min_buffer_points if profile else 0.50
        breached_zones: List[HTFZone] = []
        flipped_to_support: List[HTFZone] = []
        flipped_to_resistance: List[HTFZone] = []
        events: List[Dict[str, Any]] = []

        # 1. Check Resistance Zones
        remaining_res: List[HTFZone] = []
        for res in config.resistance_zones:
            # Confirmed Breakout: M3 candle body closes decisively above zone high
            if curr_bar_m3.close > res.high + buffer:
                breached_zones.append(res)
                # S/R Role Reversal: Broken Resistance becomes Support for BPB/PB
                flipped_sup = HTFZone(
                    id=f"FLIP_{res.id}",
                    high=res.high,
                    low=res.low,
                    significance=res.significance
                )
                flipped_to_support.append(flipped_sup)
                events.append({
                    "type": "ZONE_ROLE_REVERSAL",
                    "action": "RESISTANCE_FLIPPED_TO_SUPPORT",
                    "original_zone_id": res.id,
                    "price_level": res.high,
                    "breach_price": curr_bar_m3.close
                })
            else:
                remaining_res.append(res)

        # 2. Check Support Zones
        remaining_sup: List[HTFZone] = []
        for sup in config.support_zones:
            # Confirmed Breakdown: M3 candle body closes decisively below zone low
            if curr_bar_m3.close < sup.low - buffer:
                breached_zones.append(sup)
                # S/R Role Reversal: Broken Support becomes Resistance for BPB/PB
                flipped_res = HTFZone(
                    id=f"FLIP_{sup.id}",
                    high=sup.high,
                    low=sup.low,
                    significance=sup.significance
                )
                flipped_to_resistance.append(flipped_res)
                events.append({
                    "type": "ZONE_ROLE_REVERSAL",
                    "action": "SUPPORT_FLIPPED_TO_RESISTANCE",
                    "original_zone_id": sup.id,
                    "price_level": sup.low,
                    "breach_price": curr_bar_m3.close
                })
            else:
                remaining_sup.append(sup)

        # Apply in-place modifications to session config if any breaches occurred
        has_flipped = bool(flipped_to_support or flipped_to_resistance)
        if has_flipped:
            config.resistance_zones = remaining_res + flipped_to_resistance
            config.support_zones = remaining_sup + flipped_to_support

            # Dynamic Regime update on breakout
            if flipped_to_support or flipped_to_resistance:
                config.market_regime = MarketRegime.BREAKOUT_EXPANSION
                # Disable counter-trend TST when breaking range boundary
                config.setups_enabled["TST"] = False
                config.setups_enabled["BPB"] = True

        return {
            "has_flipped": has_flipped,
            "breached_count": len(breached_zones),
            "events": events
        }

    @staticmethod
    def find_next_htf_target(
        bars_m30: List[Bar],
        side: OrderSide,
        current_price: float,
        profile: Optional[InstrumentProfile] = None
    ) -> float:
        """
        Dynamically scans M30 historical swing points in memory to provide a valid T2 target
        when all predetermined session zones have been cleared.
        """
        default_dist = profile.default_t2_points if profile else 15.0
        if not bars_m30 or len(bars_m30) < 5:
            return (current_price + default_dist) if side == OrderSide.BUY else (current_price - default_dist)

        swings = SwingDetector.detect_swings(bars_m30)
        buffer = profile.min_buffer_points if profile else 0.50

        if side == OrderSide.BUY:
            higher_swings = [s for s in swings if s.swing_type == SwingType.SWING_HIGH and s.price > current_price + buffer]
            if higher_swings:
                # Nearest higher swing high
                higher_swings.sort(key=lambda s: s.price)
                return round(higher_swings[0].price, 5)
            return round(current_price + default_dist, 5)
        else:
            lower_swings = [s for s in swings if s.swing_type == SwingType.SWING_LOW and s.price < current_price - buffer]
            if lower_swings:
                lower_swings.sort(key=lambda s: s.price, reverse=True)
                return round(lower_swings[0].price, 5)
            return round(current_price - default_dist, 5)

    @staticmethod
    def should_trigger_ai_replan(
        curr_bar_m30: Bar,
        config: SessionConfig,
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, str]:
        """
        Tier 2 Async AI Re-Plan Trigger:
        Verifies if an M30 candle has officially closed beyond extreme session boundaries,
        warranting a full background AI re-analysis from Server B.
        """
        buffer = profile.min_buffer_points if profile else 1.0
        
        # Check if closing above all original resistance zones
        if config.resistance_zones:
            max_res = max(z.high for z in config.resistance_zones)
            if curr_bar_m30.close > max_res + buffer:
                return True, f"M30 close {curr_bar_m30.close} broke above max session resistance {max_res}."

        # Check if closing below all original support zones
        if config.support_zones:
            min_sup = min(z.low for z in config.support_zones)
            if curr_bar_m30.close < min_sup - buffer:
                return True, f"M30 close {curr_bar_m30.close} broke below min session support {min_sup}."

        return False, "BOUNDARIES_INTACT"
