"""Performance tracking — win/loss stats by scenario, direction, tier.

Phase 22: DB persistence — every record() call is also written to SQLite
via backend.db.insert_outcome(). In-memory ring buffer stays for
fast get_stats() access; DB is the durable trading journal.
"""
import logging
import asyncio

logger = logging.getLogger("judah.perf")


class PerformanceTracker:
    """Tracks win/loss by scenario, direction, tier, timeframe."""

    def __init__(self):
        self.completed = []
        self.scenario_stats = {}
        self.direction_stats = {}
        self.tier_stats = {}

    def record(self, signal):
        entry = {
            "symbol": signal.get("symbol"),
            "engine": signal.get("engine"),
            "direction": signal.get("direction"),
            "tier": signal.get("tier", "UNKNOWN"),
            "base_score": signal.get("base_score", 0),
            "rr": signal.get("rr", 0),
            "session": signal.get("session", ""),
            "outcome": signal.get("outcome", "TIMEOUT"),
            "scenario": signal.get("scenario", ""),
        }
        self.completed.append(entry)
        if len(self.completed) > 1000:
            self.completed = self.completed[-1000:]

        # Phase 22: persist to SQLite (fire-and-forget — never blocks caller)
        self._persist_async(entry)

    @staticmethod
    def _persist_async(entry: dict) -> None:
        """Schedule a DB write without blocking the caller."""
        try:
            from backend import db
            row = {
                "signal_id": entry.get("signal_id"),
                "symbol": entry.get("symbol"),
                "timeframe": entry.get("engine"),
                "direction": entry.get("direction"),
                "tier": entry.get("tier"),
                "signal_type": entry.get("signal_type"),
                "d1_tier": entry.get("d1_tier"),
                "d1_score": entry.get("d1_score"),
                "d2_tier": entry.get("d2_tier"),
                "d2_score": entry.get("d2_score"),
                "entry_price": entry.get("entry_price"),
                "sl_price": entry.get("sl_price"),
                "tp_price": entry.get("tp_price"),
                "rr": entry.get("rr"),
                "session": entry.get("session"),
                "scenario": entry.get("scenario"),
                "outcome": entry.get("outcome"),
                "pnl_pct": entry.get("pnl_pct"),
                "opened_at": entry.get("opened_at"),
                "closed_at": entry.get("closed_at"),
                "engine": entry.get("engine"),
            }
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(db.insert_outcome(row))
            else:
                loop.run_until_complete(db.insert_outcome(row))
        except Exception:
            logger.exception("[perf] DB persist failed for %s", entry.get("symbol"))

    def get_stats(self):
        if not self.completed:
            return {"total": 0, "win_rate": 0}

        total = len(self.completed)
        wins = sum(1 for r in self.completed if r["outcome"] == "WIN")

        def _group(field):
            groups = {}
            for r in self.completed:
                k = r.get(field, "UNKNOWN")
                groups.setdefault(k, {"total": 0, "wins": 0})
                groups[k]["total"] += 1
                if r["outcome"] == "WIN":
                    groups[k]["wins"] += 1
            return {
                k: {
                    "total": v["total"],
                    "wins": v["wins"],
                    "win_rate": round(v["wins"] / v["total"] * 100, 1),
                }
                for k, v in groups.items()
            }

        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "by_tier": _group("tier"),
            "by_session": _group("session"),
            "by_timeframe": _group("engine"),
        }


performance_tracker = PerformanceTracker()
