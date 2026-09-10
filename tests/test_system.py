"""
Comprehensive Unit Tests for YTC Price Action Trader (v2.1.0-STRICT)
Verifies mathematical correctness of all rules and state machines
"""
import sys
import os
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.domain.models import Bar, SwingType, OrderSide, SetupType, PositionState
from core.domain.rules.swing_detector import SwingDetector
from core.domain.rules.vector_dynamics import MicroPatternDetector, VectorDynamicsCalculator
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.risk_manager import RiskManager

class TestYTCSystem(unittest.TestCase):

    def test_swing_detector_5_bar_rule(self):
        """Tests the 5-bar Swing High and Swing Low calculation."""
        # Create 5 bars where middle bar (index 2) is Swing High
        bars = [
            Bar(100, 1.0800, 1.0820, 1.0790, 1.0810),
            Bar(200, 1.0810, 1.0840, 1.0800, 1.0830),
            Bar(300, 1.0830, 1.0880, 1.0820, 1.0870),  # Bar C (High=1.0880 is max)
            Bar(400, 1.0870, 1.0850, 1.0810, 1.0820),
            Bar(500, 1.0820, 1.0830, 1.0790, 1.0800),
        ]
        swings = SwingDetector.detect_swings(bars)
        self.assertEqual(len(swings), 1)
        self.assertEqual(swings[0].swing_type, SwingType.SWING_HIGH)
        self.assertEqual(swings[0].price, 1.0880)

    def test_wholesale_engine_lrp_and_rr(self):
        """Tests LRP formula: (S1 + T1)/2 guarantees Part 1 R:R >= 1.0"""
        s1 = 1.08000
        t1 = 1.08400
        t2 = 1.08800
        stall_low = 1.08150
        stall_high = 1.08200

        res = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=1.08020,
            t1_price=t1,
            t2_price=t2,
            micro_stall_high=stall_high,
            micro_stall_low=stall_low,
            buffer_pts=0.00020
        )

        # LRP must be exactly midpoint (1.08000 + 1.08400)/2 = 1.08200
        expected_lrp = (res.S1 + t1) / 2.0
        self.assertAlmostEqual(res.LRP, expected_lrp, places=5)

        # Recommended entry at stall low (1.08150) must be <= min(LWP, LRP)
        self.assertTrue(res.is_valid_entry)

        # Verify reward-to-risk for Part 1 at entry
        risk = res.recommended_entry - res.S1
        reward = t1 - res.recommended_entry
        self.assertGreaterEqual(reward / risk, 1.0)

    def test_wholesale_engine_no_chasing(self):
        """Tests EMIT CANCEL_EVENT when market price exceeds LWP/LRP."""
        lwp = 1.08250
        lrp = 1.08200
        # If market reaches 1.08260, do NOT chase
        valid, msg = WholesaleEngine.validate_entry_fill(OrderSide.BUY, 1.08260, lwp, lrp)
        self.assertFalse(valid)
        self.assertIn("CANCEL_EVENT", msg)

    def test_risk_manager_position_split(self):
        """Tests 50/50 lot sizing and risk allocation."""
        lot_total, lot_p1, lot_p2 = RiskManager.calculate_lot_size(
            balance=10000.0,
            risk_percent=1.0,      # $100 risk
            entry_price=1.08200,
            sl_price=1.08000,      # 20 pips distance
            point_size=0.00001,
            tick_value=1.0
        )
        self.assertGreater(lot_total, 0)
        self.assertEqual(lot_p1, round(lot_total * 0.5, 2))
        self.assertEqual(lot_p1 + lot_p2, lot_total)

    def test_scratch_rule_timeout(self):
        """Tests scratch timeout rule after 5 bars without resolution."""
        is_scratch, reason = RiskManager.evaluate_scratch_rule(bars_in_trade=5, scratch_timeout_bars=5)
        self.assertTrue(is_scratch)
        self.assertIn("Scratch timeout", reason)

    def test_xauusd_wholesale_and_sizing(self):
        """Tests Wholesale levels and position sizing specifically for XAUUSD (Gold)."""
        from core.domain.models import get_instrument_profile
        profile = get_instrument_profile("XAUUSD")
        self.assertEqual(profile.point, 0.01)
        self.assertEqual(profile.digits, 2)

        # Wholesale calculation on Gold
        pullback_swing = 2640.00
        t1 = 2655.00
        t2 = 2680.00
        stall_low = 2645.00
        stall_high = 2646.00

        res = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=pullback_swing,
            t1_price=t1,
            t2_price=t2,
            micro_stall_high=stall_high,
            micro_stall_low=stall_low,
            buffer_pts=profile.min_buffer_points  # 0.80
        )

        # S1 should be pullback - 0.80 = 2639.20
        self.assertEqual(res.S1, 2639.20)
        # LRP = (2639.20 + 2655.00) / 2 = 2647.10
        self.assertEqual(res.LRP, 2647.10)
        # Entry at 2645.00 <= min(2646.00, 2647.10)
        self.assertTrue(res.is_valid_entry)

        # Lot sizing on Gold: $10,000 balance, 1% risk ($100), distance 2645 - 2639.20 = 5.80 = 580 points
        lot_total, lot_p1, lot_p2 = RiskManager.calculate_lot_size(
            balance=10000.0,
            risk_percent=1.0,
            entry_price=res.recommended_entry,
            sl_price=res.S1,
            point_size=profile.point,
            tick_value=profile.tick_value
        )
        self.assertAlmostEqual(lot_total, 0.17, delta=0.01)
        self.assertAlmostEqual(lot_p1 + lot_p2, lot_total, places=2)

    def test_broker_get_terminal_status(self):
        """Tests get_terminal_status on PaperBroker and MT5Broker."""
        import asyncio
        from infrastructure.brokers.paper_broker import PaperBroker
        from infrastructure.brokers.mt5_broker import MT5Broker

        paper = PaperBroker()
        paper_status = asyncio.run(paper.get_terminal_status())
        self.assertTrue(paper_status["auto_trading_ready"])
        self.assertTrue(paper_status["trade_allowed"])

        mt5_broker = MT5Broker()
        mt5_status = asyncio.run(mt5_broker.get_terminal_status())
        self.assertIn("auto_trading_ready", mt5_status)
        self.assertIn("trade_allowed", mt5_status)

    def test_manual_trade_and_modify(self):
        """Tests manual placing and modifying of orders on broker."""
        import asyncio
        from infrastructure.brokers.paper_broker import PaperBroker
        from core.domain.models import OrderSide

        paper = PaperBroker()
        ticket = asyncio.run(paper.place_order(
            symbol="XAUUSD",
            side=OrderSide.BUY,
            order_type="MARKET",
            volume=0.20,
            price=2650.00,
            sl=2645.00,
            tp=2660.00
        ))
        self.assertIsNotNone(ticket)
        self.assertIn(ticket, paper.orders)
        self.assertEqual(paper.orders[ticket]["volume"], 0.20)

        # Test modify SL/TP
        mod_ok = asyncio.run(paper.modify_position(ticket, sl=2648.00, tp=2665.00))
        self.assertTrue(mod_ok)
        self.assertEqual(paper.orders[ticket]["sl"], 2648.00)
        self.assertEqual(paper.orders[ticket]["tp"], 2665.00)

if __name__ == "__main__":
    unittest.main()


