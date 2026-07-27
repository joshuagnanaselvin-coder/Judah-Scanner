def format_price(price: float) -> str:
    if price >= 1000: return f"${price:,.2f}"
    if price >= 1: return f"${price:.3f}"
    return f"${price:.5f}"
