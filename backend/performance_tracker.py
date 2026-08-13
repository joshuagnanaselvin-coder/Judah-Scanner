"""Performance tracking — win/loss stats by scenario, direction, tier."""
import logging

logger = logging.getLogger("judah.perf")


class PerformanceTracker:
    """Tracks win/loss by scenario, direction, tier, timeframe."""

    def __init__(self):
        self.completed = []
        self.scenario_stats = {}
        self.direction_stats = {}
        self.tier_stats = {}

    def record(self, signal):
        self.completed.append({
            "symbol": signal.get("symbol"),
            "engine": signal.get("engine"),
            "direction": signal.get("direction"),
            "tier": signal.get("tier", "UNKNOWN"),
            "base_score": signal.get("base_score", 0),
            "rr": signal.get("rr", 0),
            "session": signal.get("session", ""),
            "outcome": signal.get("outcome", "TIMEOUT"),
            "scenario": signal.get("scenario", ""),
        })
        if len(self.completed) > 1000:
            self.completed = self.completed[-1000:]

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
