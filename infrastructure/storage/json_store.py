"""
Local JSON Storage for Configurations, Telemetry and Trade Logs
"""
import os
import json
from typing import Dict, Any, List

class LocalJsonStore:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def save_session_config(self, config_dict: Dict[str, Any], filename: str = "trading_config.json") -> str:
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2)
        return filepath

    def load_session_config(self, filename: str = "trading_config.json") -> Dict[str, Any]:
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            return {}
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_session_trades(self, trades: List[Dict[str, Any]], filename: str = "session_trades.json") -> str:
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)
        return filepath

    def load_session_trades(self, filename: str = "session_trades.json") -> List[Dict[str, Any]]:
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            return []
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
