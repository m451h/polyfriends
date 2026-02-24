from logic import get_odds, SEED


# ── Market card (the shareable unit) ─────────────────────────────────────────

def market_card(market: dict) -> str:
    """Full market card — posted into chat when shared via inline."""
    status_emoji = {
        "pending":   "⏳", "open": "🟢", "closed": "🔴",
        "resolved":  "✅", "rejected": "❌", "cancelled": "↩️",
    }.get(market["status"], "❓")

    yes_pool = max(market["yes_pool"] - SEED, 0)
    no_pool  = max(market["no_pool"]  - SEED, 0)
    total    = yes_pool + no_pool

    yes_pct = (yes_pool / total * 100) if total > 0 else 50.0
    no_pct  = 100 - yes_pct

    bar = _bar(yes_pct)

    lines = [
        f"{status_emoji} *{market['question']}*",
        f"`{bar}`",
        f"✅ YES  {yes_pct:.0f}%   ❌ NO  {no_pct:.0f}%",
        f"💰 Pool: {total:.0f} pts",
    ]
    if market.get("deadline"):
        lines.append(f"⏰ Closes {market['deadline']} UTC")
    if market.get("resolution"):
        lines.append(f"🏁 Result: *{market['resolution']}*")

    return "\n".join(lines)

def market_card_short(market: dict) -> str:
    """One-liner for inline result list."""
    yes_pool = max(market["yes_pool"] - SEED, 0)
    no_pool  = max(market["no_pool"]  - SEED, 0)
    total    = yes_pool + no_pool
    yes_pct  = (yes_pool / total * 100) if total > 0 else 50.0
    return f"#{market['id']} · {market['question'][:55]} · YES {yes_pct:.0f}%"

def _bar(yes_pct: float, width: int = 18) -> str:
    filled = round(yes_pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── Keyboards ─────────────────────────────────────────────────────────────────

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def kb_market(market_id: int, group_id: int) -> InlineKeyboardMarkup:
    """group_id is embedded in every callback_data so handlers work on inline messages."""
    g = group_id
    mid = market_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Bet YES", callback_data=f"side_YES_{mid}_{g}"),
            InlineKeyboardButton("❌ Bet NO",  callback_data=f"side_NO_{mid}_{g}"),
        ],
        [
            InlineKeyboardButton("💸 Sell",        callback_data=f"sell_pick_{mid}_{g}"),
            InlineKeyboardButton("📊 My position", callback_data=f"mypos_{mid}_{g}"),
        ],
        [InlineKeyboardButton("🔄 Refresh odds", callback_data=f"refresh_{mid}_{g}")],
    ])

def kb_amounts(side: str, market_id: int, group_id: int) -> InlineKeyboardMarkup:
    g = group_id
    mid = market_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("50",   callback_data=f"amt_{side}_{mid}_50_{g}"),
            InlineKeyboardButton("100",  callback_data=f"amt_{side}_{mid}_100_{g}"),
            InlineKeyboardButton("200",  callback_data=f"amt_{side}_{mid}_200_{g}"),
            InlineKeyboardButton("500",  callback_data=f"amt_{side}_{mid}_500_{g}"),
        ],
        [InlineKeyboardButton("« Back", callback_data=f"refresh_{mid}_{g}")],
    ])

def kb_sell_sides(market_id: int, group_id: int, has_yes: bool, has_no: bool) -> InlineKeyboardMarkup:
    g = group_id
    btns = []
    if has_yes:
        btns.append(InlineKeyboardButton("Sell YES shares", callback_data=f"sellside_YES_{market_id}_{g}"))
    if has_no:
        btns.append(InlineKeyboardButton("Sell NO shares",  callback_data=f"sellside_NO_{market_id}_{g}"))
    return InlineKeyboardMarkup([btns, [InlineKeyboardButton("« Back", callback_data=f"refresh_{market_id}_{g}")]])

def kb_sell_amounts(side: str, market_id: int, group_id: int, max_shares: float) -> InlineKeyboardMarkup:
    g    = group_id
    mid  = market_id
    q25  = round(max_shares * 0.25, 3)
    q50  = round(max_shares * 0.50, 3)
    q100 = round(max_shares, 3)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"25% ({q25})",  callback_data=f"sellamt_{side}_{mid}_{q25}_{g}"),
            InlineKeyboardButton(f"50% ({q50})",  callback_data=f"sellamt_{side}_{mid}_{q50}_{g}"),
            InlineKeyboardButton(f"All ({q100})", callback_data=f"sellamt_{side}_{mid}_{q100}_{g}"),
        ],
        [InlineKeyboardButton("« Back", callback_data=f"sell_pick_{mid}_{g}")],
    ])


# ── Other cards ───────────────────────────────────────────────────────────────

def balance_card(username: str, groups: list) -> str:
    lines = [f"💰 *{username}'s balances*\n"]
    for g in groups:
        lines.append(f"• {g['name']}: *{g['balance']:.1f} pts*")
    return "\n".join(lines) if len(lines) > 1 else f"💰 *{username}* — not in any group yet."

def leaderboard_card(rows: list, group_name: str) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines  = [f"🏆 *{group_name} Leaderboard*\n"]
    for i, r in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {r['username']} — {r['balance']:.1f} pts")
    return "\n".join(lines)

def position_card(pos: dict) -> str:
    total = pos["yes_pool"] + pos["no_pool"]
    price = (pos["yes_pool"] / total) if pos["side"] == "YES" else (pos["no_pool"] / total)
    est   = pos["net_shares"] * price
    profit = est - pos["net_spent"]
    sign  = "+" if profit >= 0 else ""
    return (
        f"📊 *Your position*\n"
        f"{pos['side']} · {pos['net_shares']:.3f} shares\n"
        f"Spent: {pos['net_spent']:.1f} pts\n"
        f"Est. value: {est:.1f} pts ({sign}{profit:.1f})"
    )

def refill_announce(affected: list) -> str:
    if not affected:
        return "🔄 Weekly refill: everyone is above the floor!"
    lines = ["🔄 *Weekly refill!* Topped up to 200 pts:\n"]
    for r in affected:
        lines.append(f"  • {r['username']} ({r['balance']:.0f} → 200 pts)")
    return "\n".join(lines)
