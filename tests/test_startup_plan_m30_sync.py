import unittest
from core.domain.models import SessionConfig, MarketRegime, HTFZone, Significance, session_config_to_dict

class TestStartupPlanM30Sync(unittest.TestCase):
    def test_session_config_to_dict_includes_htf_zones(self):
        plan = SessionConfig(
            session_id="test_sess_01",
            symbol="XAUUSD",
            generated_at="2026-09-17T08:00:00Z",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[
                HTFZone(id="rz_1", high=2655.50, low=2653.00, significance=Significance.MAJOR)
            ],
            support_zones=[
                HTFZone(id="sz_1", high=2638.00, low=2635.50, significance=Significance.MAJOR)
            ],
            setups_enabled={"TST": True, "BOF": True},
            execution_rules={},
            risk_management={"risk_per_trade": 0.01},
            news_filter={"macro_bias": "BULLISH"},
            session_tag="LONDON_SESSION"
        )

        d = session_config_to_dict(plan)
        self.assertIn("htf_zones", d)
        self.assertIn("resistance_zones", d["htf_zones"])
        self.assertIn("support_zones", d["htf_zones"])
        self.assertEqual(len(d["htf_zones"]["resistance_zones"]), 1)
        self.assertEqual(len(d["htf_zones"]["support_zones"]), 1)
        self.assertEqual(d["htf_zones"]["resistance_zones"][0]["high"], 2655.50)
        self.assertEqual(d["htf_zones"]["resistance_zones"][0]["low"], 2653.00)
        self.assertEqual(d["htf_zones"]["support_zones"][0]["high"], 2638.00)
        self.assertEqual(d["htf_zones"]["support_zones"][0]["low"], 2635.50)
        self.assertEqual(d["market_regime"], "SIDEWAYS_RANGE")
        self.assertEqual(d["session_tag"], "LONDON_SESSION")

    def test_session_config_to_dict_handles_empty(self):
        self.assertEqual(session_config_to_dict(None), {})

if __name__ == "__main__":
    unittest.main()
