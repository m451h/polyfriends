from logic import get_odds, SEED

def fmt_market(market: dict, show_id=True) -> str:
    status_emoji = {
        "pending":   "⏳",
        "open":      "🟢",
        "closed":    "🔴",
        "resolved":  "✅",
        "rejected":  "❌",
        "cancelled": "↩️",
    }.get(market["status"], "❓")

    # Subtract seed so displayed pool = actual user money
    yes_pool = max(market["yes_pool"] - SEED, 0)
    no_pool  = max(market["no_pool"]  - SEED, 0)
    total    = yes_pool + no_pool

    if total > 0:
        yes_pct = yes_pool / total * 100
        no_pct  = no_pool  / total * 100
    else:
        yes_pct = no_pct = 50.0

    bar      = _make_bar(yes_pct)
    id_str   = f"#{market['id']} " if show_id else ""

    lines = [
        f"{status_emoji} {id_str}*{market['question']}*",
        f"`{bar}`",
        f"YES {yes_pct:.0f}%  —  NO {no_pct:.0f}%",
        f"Pool: {total:.0f} pts  (YES {yes_pool:.0f} | NO {no_pool:.0f})",
    ]

    if market.get("deadline"):
        lines.append(f"⏰ Closes: {market['deadline']} UTC")
    if market.get("resolution"):
        lines.append(f"🏁 Resolved: *{market['resolution']}*")

    return "\n".join(lines)

def _make_bar(yes_pct: float, width: int = 20) -> str:
    filled = round(yes_pct / 100 * width)
    empty  = width - filled
    return f"{'█' * filled}{'░' * empty}"

def fmt_position(pos: dict) -> str:
    status_emoji = {"open": "🟢", "closed": "🔴", "resolved": "✅"}.get(pos["status"], "❓")
    side_emoji   = "✅" if pos["side"] == "YES" else "❌"

    # Estimate current liquidation value
    yes_pool = pos["yes_pool"]
    no_pool  = pos["no_pool"]
    total    = yes_pool + no_pool

    if pos["side"] == "YES":
        price = yes_pool / total if total else 0.5
        total_side_shares = pos["yes_shares"]
    else:
        price = no_pool / total if total else 0.5
        total_side_shares = pos["no_shares"]

    est_value = pos["net_shares"] * price
    profit    = est_value - pos["net_spent"]
    profit_str = f"+{profit:.1f}" if profit >= 0 else f"{profit:.1f}"

    return (
        f"{status_emoji} *#{pos['market_id']}* {pos['question'][:45]}\n"
        f"  {side_emoji} {pos['side']} · {pos['net_shares']:.3f} shares · "
        f"spent {pos['net_spent']:.1f} pts\n"
        f"  Est. value: {est_value:.1f} pts ({profit_str})"
    )

def fmt_leaderboard(rows: list, group_name: str = "") -> str:
    medals = ["🥇", "🥈", "🥉"]
    title  = f"🏆 *Leaderboard {group_name}*\n"
    lines  = [title]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"*{i+1}.*"
        lines.append(f"{medal} {row['username']} — {row['balance']:.1f} pts")
    return "\n".join(lines)

def fmt_refill_announce(affected: list) -> str:
    if not affected:
        return "🔄 Weekly refill: everyone is above the floor, no top-ups needed!"
    lines = ["🔄 *Weekly refill!* Players topped up to 200 pts:\n"]
    for r in affected:
        lines.append(f"  • {r['username']} ({r['balance']:.0f} → 200 pts)")
    return "\n".join(lines)
