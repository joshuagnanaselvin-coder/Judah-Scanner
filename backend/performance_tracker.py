"""Performance tracking — win/loss stats by scenario, direction, tier."""
import csv
import logging
import os

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


class PerformanceTrackerCSV:
    """Extended tracker that reads/writes signal_log.csv for alpha attribution."""

    def __init__(self, log_file="signal_log.csv"):
        self.log_file = log_file
        self.trades = []
        self.scenario_stats = {}
        self.direction_stats = {}
        self.tier_stats = {}

    def load_from_csv(self):
        if not os.path.exists(self.log_file):
            return
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.trades.append(row)
                    self._categorize(row)
        except Exception as e:
            logger.error(f"[perf] CSV load error: {e}")

    def _categorize(self, signal):
        """Add signal to appropriate category buckets."""
        scenario = signal.get("scenario", "unknown")
        direction = signal.get("direction", "unknown")
        tier = signal.get("tier", "unknown")

        for key, store in [
            (scenario, self.scenario_stats),
            (direction, self.direction_stats),
            (tier, self.tier_stats),
        ]:
            if key not in store:
                store[key] = {"total": 0, "wins": 0, "losses": 0, "total_rr": 0}
            store[key]["total"] += 1
            try:
                store[key]["total_rr"] += float(signal.get("rr", 0) or 0)
            except (ValueError, TypeError):
                pass

    def record_outcome(self, signal_id, outcome):
        """Record a trade outcome (WIN/LOSS).
        outcome: 'WIN' or 'LOSS'
        """
        for signal in self.trades:
            if signal.get("id") == signal_id:
                signal["outcome"] = outcome
                scenario = signal.get("scenario", "unknown")
                if scenario in self.scenario_stats:
                    if outcome == "WIN":
                        self.scenario_stats[scenario]["wins"] += 1
                    else:
                        self.scenario_stats[scenario]["losses"] += 1
                break

    def get_scenario_report(self):
        report = []
        for scenario, stats in self.scenario_stats.items():
            total = stats["total"]
            if total == 0:
                continue
            win_rate = stats["wins"] / total * 100
            avg_rr = stats["total_rr"] / total
            expectancy = (win_rate / 100) * avg_rr - ((100 - win_rate) / 100) * 1.0
            profit_factor = (stats["wins"] * avg_rr) / max(stats["losses"], 1)

            if expectancy > 0.5:
                verdict = "EDGE"
            elif expectancy < 0:
                verdict = "NO_EDGE"
            else:
                verdict = "MARGINAL"

            report.append({
                "scenario": scenario,
                "total_trades": total,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate_pct": round(win_rate, 1),
                "avg_rr": round(avg_rr, 2),
                "expectancy": round(expectancy, 2),
                "profit_factor": round(profit_factor, 2),
                "verdict": verdict,
            })

        report.sort(key=lambda x: x["expectancy"], reverse=True)
        return report

    def get_summary(self):
        total = len(self.trades)
        if total == 0:
            return {"status": "No data yet"}

        wins = sum(1 for t in self.trades if t.get("outcome") == "WIN")
        losses = sum(1 for t in self.trades if t.get("outcome") == "LOSS")
        pending = total - wins - losses

        total_rr = 0
        for t in self.trades:
            try:
                total_rr += float(t.get("rr", 0) or 0)
            except (ValueError, TypeError):
                pass

        return {
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
            "avg_rr": round(total_rr / max(total, 1), 2),
        }


performance_tracker = PerformanceTracker()
