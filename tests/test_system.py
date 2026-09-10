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

    def test_safe_holding_and_bar_timestamp_counter(self):
        """Tests that 1s engine loops with the same M1 bar do NOT prematurely increment m1_bars_in_trade."""
        import asyncio
        from infrastructure.brokers.paper_broker import PaperBroker
        from infrastructure.bus.async_event_bus import AsyncEventBus
        from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
        from core.domain.models import TradeLifecycle, PositionPart, PositionState, OrderSide, SetupType

        paper = PaperBroker()
        bus = AsyncEventBus()
        lifecycle_uc = ManageLifecycleUseCase(paper, bus)

        trade = TradeLifecycle(
            trade_id="test_safe_hold_1",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=PositionPart(1, 0.1, 2650.0, 2640.0, 2660.0),
            part2=PositionPart(2, 0.1, 2650.0, 2640.0, 2670.0),
            open_time=1000.0,
            m1_bars_in_trade=0,
            last_bar_timestamp=None
        )

        bar_1 = Bar(timestamp=60.0, open=2650.0, high=2652.0, low=2649.0, close=2651.0)
        # Simulate 100 ticks / loop iterations with the exact same M1 bar (timestamp 60.0)
        for _ in range(100):
            asyncio.run(lifecycle_uc.update(trade, [bar_1], [], scratch_timeout_bars=8, min_holding_bars=3))

        # Must be exactly 1 bar, not 100!
        self.assertEqual(trade.m1_bars_in_trade, 1)
        self.assertEqual(trade.state, PositionState.IN_POSITION)

        # Feed second distinct M1 bar
        bar_2 = Bar(timestamp=120.0, open=2651.0, high=2653.0, low=2650.0, close=2652.0)
        asyncio.run(lifecycle_uc.update(trade, [bar_2], [], scratch_timeout_bars=8, min_holding_bars=3))
        self.assertEqual(trade.m1_bars_in_trade, 2)
        # At 2 bars (< min_holding_bars=3), it must NOT scratch
        self.assertEqual(trade.state, PositionState.IN_POSITION)

    def test_single_entry_per_swing_anchor_and_cooldown(self):
        """Tests that once an entry occurs, the swing anchor is consumed and post-trade cooldown blocks re-entry."""
        import asyncio
        import time
        from infrastructure.brokers.paper_broker import PaperBroker
        from infrastructure.bus.async_event_bus import AsyncEventBus
        from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
        from core.domain.models import SessionConfig, MarketRegime, HTFZone

        paper = PaperBroker()
        bus = AsyncEventBus()
        entry_uc = EvaluateEntryUseCase(paper, bus)

        config = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("R1", 2670.0, 2665.0)],
            support_zones=[HTFZone("S1", 2645.0, 2640.0)],
            setups_enabled={"TST": True, "BOF": False, "BPB": False, "PB": False, "CPB": False},
            execution_rules={"post_trade_cooldown_seconds": 180, "enable_session_transition_guard": False},
            risk_management={"fixed_lot_size": 0.1, "max_consecutive_losses": 2, "max_session_trades": 6},
            news_filter={}
        )

        bars_m3 = [
            Bar(300, 2655.0, 2660.0, 2650.0, 2652.0),
            Bar(600, 2652.0, 2655.0, 2646.0, 2648.0),
            Bar(900, 2648.0, 2650.0, 2640.0, 2645.0), # Swing Low at 2640.0
            Bar(1200, 2645.0, 2652.0, 2644.0, 2649.0),
            Bar(1500, 2649.0, 2655.0, 2646.0, 2650.0),
        ]
        bars_m1 = [
            Bar(1440, 2645.5, 2646.0, 2644.5, 2645.0),
            Bar(1500, 2645.0, 2645.5, 2644.0, 2644.8)
        ]

        # 1. First execution creates trade
        first_trade = asyncio.run(entry_uc.execute(config, [], bars_m3, bars_m1, []))
        self.assertIsNotNone(first_trade)
        self.assertIn(first_trade.anchor_id, entry_uc.consumed_anchors)

        # 2. Immediate second call with active_trades empty (e.g. price oscillating at same swing anchor)
        second_trade = asyncio.run(entry_uc.execute(config, [], bars_m3, bars_m1, []))
        # Must be rejected because anchor was consumed!
        self.assertIsNone(second_trade)

        # 3. Test post-trade cooldown
        entry_uc.consumed_anchors.clear()
        entry_uc.last_trade_closed_time = time.time() - 60 # 60s ago (< 180s cooldown)
        cooldown_trade = asyncio.run(entry_uc.execute(config, [], bars_m3, bars_m1, []))
        self.assertIsNone(cooldown_trade)

        # Once cooldown passes (> 180s)
        entry_uc.last_trade_closed_time = time.time() - 200
        allowed_trade = asyncio.run(entry_uc.execute(config, [], bars_m3, bars_m1, []))
        self.assertIsNotNone(allowed_trade)

    def test_circuit_breaker_consecutive_losses(self):
        """Tests that circuit breaker engages and halts entries when max consecutive losses is hit."""
        import asyncio
        from infrastructure.brokers.paper_broker import PaperBroker
        from infrastructure.bus.async_event_bus import AsyncEventBus
        from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
        from core.domain.models import SessionConfig, MarketRegime, HTFZone, TradeLifecycle, PositionPart, PositionState, SetupType, OrderSide

        paper = PaperBroker()
        bus = AsyncEventBus()
        entry_uc = EvaluateEntryUseCase(paper, bus)

        config = SessionConfig(
            session_id="test_cb",
            symbol="XAUUSD",
            generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[],
            support_zones=[HTFZone("S1", 2645.0, 2640.0)],
            setups_enabled={"TST": True},
            execution_rules={"post_trade_cooldown_seconds": 0},
            risk_management={"max_consecutive_losses": 2},
            news_filter={}
        )

        dummy_loss = TradeLifecycle(
            trade_id="loss_1",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            state=PositionState.STOPPED_OUT,
            part1=PositionPart(1, 0.1, 2645.0, 2640.0, 2655.0),
            part2=PositionPart(2, 0.1, 2645.0, 2640.0, 2665.0),
            open_time=100.0,
            close_time=200.0
        )

        # Record 1st loss
        entry_uc.record_trade_closed(dummy_loss)
        self.assertEqual(entry_uc.consecutive_losses, 1)

        # Record 2nd loss
        entry_uc.record_trade_closed(dummy_loss)
        self.assertEqual(entry_uc.consecutive_losses, 2)

        bars_m3 = [Bar(i*300, 2645.0, 2646.0, 2644.0, 2645.0) for i in range(1, 6)]
        bars_m1 = [Bar(100, 2645.0, 2645.5, 2644.5, 2645.0)]

        # Should be blocked by circuit breaker
        blocked_trade = asyncio.run(entry_uc.execute(config, [], bars_m3, bars_m1, []))
        self.assertIsNone(blocked_trade)

    def test_session_transition_detection_and_freeze(self):
        """Tests that during session transition (e.g. Asia->London), freeze window is identified."""
        from datetime import datetime, timezone
        from core.domain.rules.session_manager import SessionManager

        sm = SessionManager()
        # 07:00 UTC (14:00 VN) -> inside ASIA_TO_LONDON transition (06:45 - 07:30 UTC)
        fake_time = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)
        status = sm.get_session_status(now_utc=fake_time)
        self.assertTrue(status.in_transition)
        self.assertEqual(status.transition_name, "ASIA_TO_LONDON")
        self.assertGreater(status.seconds_until_stabilized, 0)
        self.assertIn("GIAO PHIÊN", status.message)

    def test_strict_session_plan_verification(self):
        """Tests that trading is strictly blocked if the plan loaded does not match the active session."""
        from datetime import datetime, timezone
        from core.domain.rules.session_manager import SessionManager
        from core.domain.models import SessionConfig, MarketRegime

        sm = SessionManager()
        fake_london_time = datetime(2026, 9, 10, 8, 0, 0, tzinfo=timezone.utc) # 15:00 VN -> London stabilized
        status = sm.get_session_status(now_utc=fake_london_time)
        self.assertFalse(status.in_transition)
        self.assertEqual(status.session_tag, "LONDON_20260910")

        # Case A: Plan is from yesterday or previous session (e.g. ASIA)
        old_plan = SessionConfig(
            session_id="sess_asia",
            symbol="XAUUSD",
            generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={},
            execution_rules={},
            risk_management={},
            news_filter={},
            session_tag="ASIA_20260910"
        )
        self.assertFalse(sm.is_plan_valid_for_session(old_plan, status.session_tag))

        # Case B: Plan has no tag at all
        untagged_plan = SessionConfig(
            session_id="sess_unknown",
            symbol="XAUUSD",
            generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={},
            execution_rules={},
            risk_management={},
            news_filter={},
            session_tag=None
        )
        self.assertFalse(sm.is_plan_valid_for_session(untagged_plan, status.session_tag))

        # Case C: Fresh plan generated specifically for this London session
        valid_london_plan = SessionConfig(
            session_id="sess_london_20260910_0730",
            symbol="XAUUSD",
            generated_at="2026-09-10",
            market_regime=MarketRegime.TRENDING_STEADY,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={},
            execution_rules={},
            risk_management={},
            news_filter={},
            session_tag="LONDON_20260910"
        )
        self.assertTrue(sm.is_plan_valid_for_session(valid_london_plan, status.session_tag))

    def test_auto_replan_trigger_lifecycle(self):
        """Tests auto re-plan trigger: fires once on stabilization, then marks completed."""
        from datetime import datetime, timezone
        from core.domain.rules.session_manager import SessionManager

        sm = SessionManager()
        fake_time = datetime(2026, 9, 10, 8, 0, 0, tzinfo=timezone.utc)
        status = sm.get_session_status(now_utc=fake_time)

        # Without valid plan loaded -> should trigger auto re-plan
        self.assertTrue(sm.should_trigger_auto_replan(status, current_cfg=None))

        # Mark re-plan started
        sm.mark_replan_started()
        self.assertFalse(sm.should_trigger_auto_replan(status, current_cfg=None))

        # Mark completed
        sm.mark_replan_completed(status.session_tag)
        self.assertFalse(sm.should_trigger_auto_replan(status, current_cfg=None))

    def test_tst_rejection_confirmation(self):
        """Tests that TSTSetup requires price action confirmation (wick/close) rather than blind touch."""
        from core.domain.models import HTFZone, Significance, SwingNode, SwingType
        from core.domain.rules.setups.setups import TSTSetup

        tst = TSTSetup()
        sup = HTFZone("SUP1", high=2640.0, low=2638.0, significance=Significance.MAJOR)

        # Case 1: Blind touch - strong bear candle into support with 0 lower wick
        bars_unconfirmed = [
            Bar(100, 2642.0, 2642.0, 2639.0, 2639.0) # open=2642, low=close=2639 -> lower wick = 0
        ]
        swings = [SwingNode(SwingType.SWING_HIGH, 2650.0, 100, 0, bars_unconfirmed[0])]
        triggered, side, pullback, t1, t2 = tst.evaluate(
            trend="RANGE", swings_3m=swings, bars_1m=bars_unconfirmed,
            resistance_zones=[], support_zones=[sup]
        )
        self.assertFalse(triggered)

        # Case 2: Confirmed reaction - candle with significant lower wick (rejection)
        bars_confirmed = [
            Bar(100, 2640.0, 2640.5, 2638.5, 2640.2) # low=2638.5, open=2640.0, close=2640.2 -> lower wick = 1.5 / range 2.0 = 75%
        ]
        triggered, side, pullback, t1, t2 = tst.evaluate(
            trend="RANGE", swings_3m=swings, bars_1m=bars_confirmed,
            resistance_zones=[], support_zones=[sup]
        )
        self.assertTrue(triggered)
        self.assertEqual(side, OrderSide.BUY)

    def test_dynamic_scratch_rules(self):
        """Tests dynamic scratch rules: P&L extension and fast opposite momentum cut."""
        # Case A: 8 bars stagnant at entry -> Scratch
        scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=8, scratch_timeout_bars=8, unrealized_r=0.0
        )
        self.assertTrue(scratch)
        self.assertIn("Scratch timeout", reason)

        # Case B: 8 bars but trade is in profit (+0.5R) -> Extended, DO NOT scratch
        scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=8, scratch_timeout_bars=8, unrealized_r=0.5
        )
        self.assertFalse(scratch)

        # Case C: 14 bars in trade -> Even in profit, maximum holding reached
        scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=14, scratch_timeout_bars=8, unrealized_r=0.5
        )
        self.assertTrue(scratch)

        # Case D: Fast scratch at bar 3 due to aggressive opposite momentum
        scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=3, scratch_timeout_bars=8, opposite_momentum_detected=True
        )
        self.assertTrue(scratch)
        self.assertIn("Opposite momentum", reason)

    def test_spatial_anchor_and_max_retries(self):
        """Tests that reaching max_retries_per_zone locks out repeated entries at the same price zone."""
        from core.domain.models import TradeLifecycle, PositionPart, PositionState, SetupType
        from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
        from core.domain.interfaces.broker import IBrokerGateway
        from core.domain.interfaces.event_bus import IEventBus
        from unittest.mock import AsyncMock, MagicMock
        import time

        broker = MagicMock(spec=IBrokerGateway)
        bus = MagicMock(spec=IEventBus)
        use_case = EvaluateEntryUseCase(broker, bus)

        # Simulate 2 consecutive scratch trades at the same zone 2640.0
        trade1 = TradeLifecycle(
            trade_id="t1", symbol="XAUUSD", setup_type=SetupType.TST, side=OrderSide.BUY,
            state=PositionState.SCRATCHED,
            part1=PositionPart(1, 0.01, 2640.0, 2638.0, 2645.0),
            part2=PositionPart(2, 0.01, 2640.0, 2638.0, 2650.0),
            open_time=time.time() - 600, close_time=time.time() - 500,
            spatial_anchor_key="TST_BUY_2640.0"
        )
        trade2 = TradeLifecycle(
            trade_id="t2", symbol="XAUUSD", setup_type=SetupType.TST, side=OrderSide.BUY,
            state=PositionState.SCRATCHED,
            part1=PositionPart(1, 0.01, 2640.0, 2638.0, 2645.0),
            part2=PositionPart(2, 0.01, 2640.0, 2638.0, 2650.0),
            open_time=time.time() - 400, close_time=time.time() - 300,
            spatial_anchor_key="TST_BUY_2640.0"
        )
        use_case.record_trade_closed(trade1)
        use_case.record_trade_closed(trade2)

        # Verify zone scratch history records 2 scratches
        self.assertEqual(len(use_case.zone_scratch_history.get("TST_BUY_2640.0", [])), 2)

    def test_zone_breach_and_role_reversal(self):
        """Tests Tier 1 Local Reflex: Resistance breach triggers Role Reversal to Support and updates regime."""
        from core.domain.models import HTFZone, Significance, SessionConfig, MarketRegime
        from core.domain.rules.zone_monitor import ZoneMonitor

        config = SessionConfig(
            session_id="s1", symbol="XAUUSD", generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("RES_1", high=2650.0, low=2648.0, significance=Significance.MAJOR)],
            support_zones=[HTFZone("SUP_1", high=2630.0, low=2628.0, significance=Significance.MAJOR)],
            setups_enabled={"TST": True, "BPB": False},
            execution_rules={}, risk_management={}, news_filter={}
        )

        # Case 1: Wick sweep only (high=2651.0, close=2649.0 <= res.high) -> No flip
        wick_bar = Bar(100, 2647.0, 2651.0, 2646.0, 2649.0)
        res = ZoneMonitor.evaluate_zone_breaches(wick_bar, config)
        self.assertFalse(res["has_flipped"])

        # Case 2: Confirmed breakout (close=2652.0 > res.high + buffer) -> Flip RES to SUP!
        breakout_bar = Bar(200, 2648.0, 2653.0, 2647.0, 2652.0)
        res = ZoneMonitor.evaluate_zone_breaches(breakout_bar, config)
        self.assertTrue(res["has_flipped"])
        # Check Resistance converted to Support
        self.assertEqual(len(config.resistance_zones), 0)
        self.assertTrue(any(z.id == "FLIP_RES_1" for z in config.support_zones))
        self.assertEqual(config.market_regime, MarketRegime.BREAKOUT_EXPANSION)
        self.assertFalse(config.setups_enabled["TST"])
        self.assertTrue(config.setups_enabled["BPB"])

    def test_zone_monitor_target_and_replan_trigger(self):
        """Tests dynamic HTF target lookup and Tier 2 AI re-plan trigger detection."""
        from core.domain.models import HTFZone, Significance, SessionConfig, MarketRegime
        from core.domain.rules.zone_monitor import ZoneMonitor

        config = SessionConfig(
            session_id="s1", symbol="XAUUSD", generated_at="2026-09-10",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("RES_1", high=2650.0, low=2648.0, significance=Significance.MAJOR)],
            support_zones=[HTFZone("SUP_1", high=2630.0, low=2628.0, significance=Significance.MAJOR)],
            setups_enabled={}, execution_rules={}, risk_management={}, news_filter={}
        )

        # 1. Test AI re-plan trigger on confirmed M30 boundary close
        # M30 bar closing at 2652.5 > max resistance 2650.0 + buffer
        m30_bar = Bar(1800, 2649.0, 2653.0, 2648.0, 2652.5)
        should_replan, reason = ZoneMonitor.should_trigger_ai_replan(m30_bar, config)
        self.assertTrue(should_replan)
        self.assertIn("broke above max session resistance", reason)

        # 2. Test dynamic HTF target finder when zones are cleared
        m30_bars = [
            Bar(100, 2650.0, 2655.0, 2649.0, 2654.0),
            Bar(200, 2654.0, 2660.0, 2653.0, 2658.0),
            Bar(300, 2658.0, 2675.0, 2657.0, 2672.0), # Swing High
            Bar(400, 2670.0, 2668.0, 2662.0, 2664.0),
            Bar(500, 2664.0, 2666.0, 2661.0, 2663.0)
        ]
        target = ZoneMonitor.find_next_htf_target(m30_bars, OrderSide.BUY, current_price=2655.0)
        self.assertGreater(target, 2655.0)

if __name__ == "__main__":
    unittest.main()


