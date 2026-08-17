"""Phase 1 — Decision Snapshot acceptance tests.

Acceptance criterion:
  For a given snapshot_id + code_version + configuration_hash,
  the same input must produce the same decision.
"""
from unittest.mock import MagicMock

import pytest

from backend.decision_snapshot import DecisionSnapshot, SnapshotBuilder


class TestDecisionSnapshot:

    def test_snapshot_is_frozen_dataclass(self):
        """DecisionSnapshot must be immutable (frozen=True)."""
        snap = DecisionSnapshot(
            snapshot_id="abc123",
            snapshot_timestamp=1000.0,
            processing_timestamp=1000.1,
            symbol="BTCUSDT",
            market_data_version="v1",
            configuration_hash="cfg_hash",
            code_version="abc1234",
        )
        with pytest.raises((AttributeError, TypeError)):
            snap.snapshot_id = "new_id"

    def test_same_snapshot_same_decisions(self):
        """Identical candle data + identical snapshot_id → identical quality map."""
        candles = [
            MagicMock(time=1000 + i * 60, open=100 + i, high=105 + i,
                     low=95 + i, close=102 + i, volume=1000)
            for i in range(30)
        ]
        candles_t = tuple(candles)
        snap1 = DecisionSnapshot(
            snapshot_id="fixed-id",
            snapshot_timestamp=2000.0,
            processing_timestamp=2000.1,
            symbol="BTCUSDT",
            market_data_version="v1",
            configuration_hash="cfg",
            code_version="code",
            candles={"BTCUSDT:1H": candles_t},
            data_quality={"BTCUSDT:1H": "VALID"},
        )
        snap2 = DecisionSnapshot(
            snapshot_id="fixed-id",
            snapshot_timestamp=2000.0,
            processing_timestamp=2000.1,
            symbol="BTCUSDT",
            market_data_version="v1",
            configuration_hash="cfg",
            code_version="code",
            candles={"BTCUSDT:1H": candles_t},
            data_quality={"BTCUSDT:1H": "VALID"},
        )
        assert snap1.snapshot_id == snap2.snapshot_id
        assert snap1.code_version == snap2.code_version
        assert snap1.configuration_hash == snap2.configuration_hash
        assert snap1.get_candles("BTCUSDT", "1H") == snap2.get_candles("BTCUSDT", "1H")
        assert snap1.candle_quality("BTCUSDT", "1H") == snap2.candle_quality("BTCUSDT", "1H")

    def test_get_candles_returns_empty_for_missing(self):
        snap = DecisionSnapshot(
            snapshot_id="id", snapshot_timestamp=0,
            processing_timestamp=0, symbol="",
            market_data_version="v1", configuration_hash="",
            code_version="",
        )
        assert snap.get_candles("ETHUSDT", "15M") == ()

    def test_candle_quality_defaults_missing(self):
        snap = DecisionSnapshot(
            snapshot_id="id", snapshot_timestamp=0,
            processing_timestamp=0, symbol="",
            market_data_version="v1", configuration_hash="",
            code_version="",
        )
        assert snap.candle_quality("ETHUSDT", "15M") == "MISSING"

    def test_is_valid_for(self):
        snap = DecisionSnapshot(
            snapshot_id="id", snapshot_timestamp=0,
            processing_timestamp=0, symbol="",
            market_data_version="v1", configuration_hash="",
            code_version="",
            candles={"BTC:1H": (MagicMock(),)},
            data_quality={"BTC:1H": "VALID"},
        )
        assert snap.is_valid_for("BTC", "1H") is True
        assert snap.is_valid_for("ETH", "1H") is False

    def test_snapshot_builder_has_required_fields(self):
        md = MagicMock()
        md.get_candles = MagicMock(return_value=())
        builder = SnapshotBuilder(md)
        snap = builder.build(["BTCUSDT"])
        assert hasattr(snap, 'snapshot_id')
        assert hasattr(snap, 'snapshot_timestamp')
        assert hasattr(snap, 'processing_timestamp')
        assert hasattr(snap, 'code_version')
        assert hasattr(snap, 'configuration_hash')
        assert hasattr(snap, 'candles')
        assert hasattr(snap, 'data_quality')
        assert hasattr(snap, 'liquidity_state')

    def test_code_version_is_string(self):
        from backend.decision_snapshot import _CODE_VERSION
        assert isinstance(_CODE_VERSION, str)
        assert len(_CODE_VERSION) > 0

    def test_config_hash_is_string(self):
        from backend.decision_snapshot import _CONFIG_HASH
        assert isinstance(_CONFIG_HASH, str)
        assert len(_CONFIG_HASH) > 0