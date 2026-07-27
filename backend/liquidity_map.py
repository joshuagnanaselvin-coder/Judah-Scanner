"""Liquidity pool detection — equal highs (buyside) and equal lows (sellside)."""
def detect_liquidity_pools(swings: dict, tolerance_pct: float = 0.3) -> dict:
    pools = {"buyside": [], "sellside": []}

    highs = swings.get("swing_highs", [])
    lows = swings.get("swing_lows", [])

    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            diff = abs(highs[i]["price"] - highs[j]["price"])
            if highs[j]["price"] > 0 and diff / highs[j]["price"] * 100 <= tolerance_pct:
                pools["buyside"].append({
                    "price": round((highs[i]["price"] + highs[j]["price"]) / 2, 2),
                    "strength": 2,
                })

    for i in range(len(lows)):
        for j in range(i+1, len(lows)):
            diff = abs(lows[i]["price"] - lows[j]["price"])
            if lows[j]["price"] > 0 and diff / lows[j]["price"] * 100 <= tolerance_pct:
                pools["sellside"].append({
                    "price": round((lows[i]["price"] + lows[j]["price"]) / 2, 2),
                    "strength": 2,
                })

    for side in ["buyside", "sellside"]:
        pools[side] = _merge_pools(pools[side])

    return pools

def _merge_pools(pools: list) -> list:
    if not pools: return []
    merged = []
    for p in sorted(pools, key=lambda x: x["price"]):
        for m in merged:
            if abs(m["price"] - p["price"]) / p["price"] * 100 < 0.5:
                m["strength"] += p["strength"]
                break
        else:
            merged.append(dict(p))
    return merged
