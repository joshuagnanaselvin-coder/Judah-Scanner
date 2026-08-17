"""Symbol filter tests — USDT-M futures only, no B-stocks."""
from __future__ import annotations

import pytest

from backend.symbol_filter import is_valid_usdt_future, filter_usdt_futures


class TestIsValidUsdtFuture:

    def test_standard_btc(self):
        assert is_valid_usdt_future("BTCUSDT") is True

    def test_standard_eth(self):
        assert is_valid_usdt_future("ETHUSDT") is True

    def test_standard_doge(self):
        assert is_valid_usdt_future("DOGEUSDT") is True

    def test_lowercase_accepted(self):
        """Lowercase should be upper-cased and pass."""
        assert is_valid_usdt_future("btcusdt") is True

    def test_mixed_case_accepted(self):
        assert is_valid_usdt_future("BtcUsdt") is True

    def test_empty_rejected(self):
        assert is_valid_usdt_future("") is False

    def test_none_rejected(self):
        assert is_valid_usdt_future(None) is False

    def test_int_rejected(self):
        assert is_valid_usdt_future(42) is False

    def test_busd_rejected(self):
        assert is_valid_usdt_future("BTCBUSD") is False

    def test_bare_usdt_rejected(self):
        """'USDT' alone is not a valid pair."""
        assert is_valid_usdt_future("USDT") is False

    def test_no_quote_asset_rejected(self):
        assert is_valid_usdt_future("BTCUSD") is False

    def test_eth_quote_rejected(self):
        assert is_valid_usdt_future("ETHUSDT") is True  # USDT is fine
        assert is_valid_usdt_future("ETHBTC") is False

    def test_stock_aapl(self):
        assert is_valid_usdt_future("AAPLUSDT") is False

    def test_stock_tsla(self):
        assert is_valid_usdt_future("TSLAUSDT") is False

    def test_stock_msft(self):
        assert is_valid_usdt_future("MSFTUSDT") is False

    def test_stock_nvda(self):
        assert is_valid_usdt_future("NVDAUSDT") is False

    def test_stock_googl(self):
        assert is_valid_usdt_future("GOOGLUSDT") is False

    def test_leveraged_btcup(self):
        assert is_valid_usdt_future("BTCUPUSDT") is False

    def test_leveraged_btcdown(self):
        assert is_valid_usdt_future("BTCDOWNUSDT") is False

    def test_leveraged_ethup(self):
        assert is_valid_usdt_future("ETHUPUSDT") is False

    def test_leveraged_ethdown(self):
        assert is_valid_usdt_future("ETHDOWNUSDT") is False

    def test_leveraged_bnbdown(self):
        assert is_valid_usdt_future("BNBDOWNUSDT") is False

    def test_leveraged_solup(self):
        assert is_valid_usdt_future("SOLUPUSDT") is False

    def test_leveraged_soldown(self):
        assert is_valid_usdt_future("SOLDOWNUSDT") is False

    def test_leveraged_maticdown(self):
        assert is_valid_usdt_future("MATICDOWNUSDT") is False

    def test_sports_token_alpine(self):
        assert is_valid_usdt_future("ALPINEUSDT") is False

    def test_sports_token_lazio(self):
        assert is_valid_usdt_future("LAZIOUSDT") is False

    def test_short_base_rejected(self):
        """Base asset with fewer than 2 chars after stripping USDT."""
        assert is_valid_usdt_future("AUSDT") is False

    def test_long_base_accepted(self):
        """Long base asset is fine as long as it's clean."""
        assert is_valid_usdt_future("PEPEUSDT") is True

    def test_shib_is_accepted(self):
        assert is_valid_usdt_future("SHIBUSDT") is True

    def test_ray_is_accepted(self):
        assert is_valid_usdt_future("RAYUSDT") is True


class TestFilterUsdtFutures:

    def test_basic_filter(self):
        raw = ["BTCUSDT", "ETHUSDT", "AAPLUSDT", "TSLAUSDT", "BTCBUSD"]
        result = filter_usdt_futures(raw)
        assert result == ["BTCUSDT", "ETHUSDT"]

    def test_deduplicates(self):
        raw = ["BTCUSDT", "btcusdt", "BTCUSDT", "ETHUSDT"]
        result = filter_usdt_futures(raw)
        assert result == ["BTCUSDT", "ETHUSDT"]
        assert len(result) == 2

    def test_sorted_output(self):
        raw = ["ZECUSDT", "AAVEUSDT", "BTCUSDT"]
        result = filter_usdt_futures(raw)
        assert result == ["AAVEUSDT", "BTCUSDT", "ZECUSDT"]

    def test_empty_input(self):
        assert filter_usdt_futures([]) == []

    def test_all_blocked(self):
        raw = ["AAPLUSDT", "BTCUPUSDT", "BTCDOWNUSDT", "TSLAUSDT"]
        result = filter_usdt_futures(raw)
        assert result == []

    def test_mixed_case_normalized(self):
        raw = ["btcusdt", "ETHUSDT", "aaveusdt"]
        result = filter_usdt_futures(raw)
        assert result == ["AAVEUSDT", "BTCUSDT", "ETHUSDT"]

    def test_stock_tokens_removed(self):
        """Real-world spot-style stock tokens that slip into futures exchangeInfo."""
        stocks = [
            "AAPLUSDT", "TSLAUSDT", "MSFTUSDT", "GOOGLUSDT",
            "AMZNUSDT", "METAUSDT", "NVDAUSDT", "NFLXUSDT",
        ]
        result = filter_usdt_futures(stocks)
        assert result == []

    def test_leveraged_tokens_removed(self):
        """Leveraged tokens from spot (BTCUP, ETHDOWN etc.) are blocked."""
        leveraged = [
            "BTCUPUSDT", "BTCDOWNUSDT", "ETHUPUSDT", "ETHDOWNUSDT",
            "BNBUPUSDT", "BNBDOWNUSDT", "SOLUPUSDT", "SOLDOWNUSDT",
        ]
        result = filter_usdt_futures(leveraged)
        assert result == []

    def test_real_crypto_survives(self):
        """Actual crypto pairs pass through."""
        pairs = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
            "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
            "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT",
            "LTCUSDT", "XRPUSDT", "ADAUSDT", "ATOMUSDT",
        ]
        result = filter_usdt_futures(pairs)
        assert len(result) == 16
        assert "BTCUSDT" in result
        assert "SHIBUSDT" in result  # SHIB is a legitimate crypto meme coin
