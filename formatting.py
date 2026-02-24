"""
All text formatting and keyboards.

Uses plain text everywhere — no MarkdownV2, no escaping headaches.
market_card_plain() is used for both inline results AND callback edits.
"""

from logic import SEED
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _bar(pct: float, width: int = 16) -> str:
    n = round(pct / 100 * width)
    return "█" * n + "░" * (width - n)

def _pools(m: dict):
    yes = max(m["yes_pool"] - SEED, 0)
    no  = max(m["no_pool"]  - SEED, 0)
    tot = yes + no
    yp  = (yes / tot * 100) if tot > 0 else 50.0
    return yes, no, tot, yp

def _safe(text: str) -> str:
    """Escape MarkdownV2 special chars — only used if markdown is needed."""
    for c in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(c, f"\\{c}")
    return text


# ── Market card (plain text, safe everywhere) ─────────────────────────────────

def market_card_plain(m: dict) -> str:
    icon = {
        "pending": "Pending", "open": "Open", "closed": "Closed",
        "resolved": "Resolved", "rejected": "Rejected", "cancelled": "Cancelled"
    }.get(m["status"], m["status"])

    yes, no, tot, yp = _pools(m)
    np = 100 - yp

    lines = [
        f"[{icon}] {m['question']}",
        f"{_bar(yp)}",
        f"YES {yp:.0f}%  |  NO {np:.0f}%",
        f"Pool: {tot:.0f} pts",
    ]
    if m.get("deadline"):
        lines.append(f"Closes: {m['deadline']} UTC")
    if m.get("resolution"):
        lines.append(f"Result: {m['resolution']}")
    return "\n".join(lines)

# Keep old name as alias so nothing breaks
market_card = market_card_plain


# ── Other plain text cards ────────────────────────────────────────────────────

def balance_plain(username: str, groups: list) -> str:
    if not groups:
        return f"{username} - not in any groups yet.\nUse /join in a group to get started."
    lines = [f"Balances for {username}"]
    for g in groups:
        lines.append(f"  {g['name']}: {g['balance']:.1f} pts")
    return "\n".join(lines)

def leaderboard_plain(rows: list, group_name: str) -> str:
    medals = ["1st", "2nd", "3rd"]
    lines  = [f"Leaderboard: {group_name}"]
    for i, r in enumerate(rows):
        pos = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"  {pos} {r['username']}  {r['balance']:.1f} pts")
    return "\n".join(lines)

def position_text(pos: dict) -> str:
    tot   = pos["yes_pool"] + pos["no_pool"]
    price = (pos["yes_pool"] / tot) if pos["side"] == "YES" else (pos["no_pool"] / tot)
    est   = pos["net_shares"] * price
    diff  = est - pos["net_spent"]
    sign  = "+" if diff >= 0 else ""
    return (
        f"{pos['side']} position:\n"
        f"  {pos['net_shares']:.3f} shares  (spent {pos['net_spent']:.1f} pts)\n"
        f"  Est. value: {est:.1f} pts  ({sign}{diff:.1f})"
    )

def refill_text(affected: list) -> str:
    if not affected:
        return "Weekly refill: everyone is above the floor, no top-ups needed!"
    lines = ["Weekly refill! Topped up to 200 pts:"]
    for r in affected:
        lines.append(f"  {r['username']}  ({r['balance']:.0f} pts -> 200 pts)")
    return "\n".join(lines)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def kb_load(market_id: int, group_id: int) -> InlineKeyboardMarkup:
    """One-button keyboard on the inline placeholder — loads the full card."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Tap to load market",
            callback_data=f"load_{market_id}_{group_id}"
        )
    ]])

def kb_market(market_id: int, group_id: int) -> InlineKeyboardMarkup:
    g, m = group_id, market_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Bet YES", callback_data=f"side_YES_{m}_{g}"),
            InlineKeyboardButton("Bet NO",  callback_data=f"side_NO_{m}_{g}"),
        ],
        [
            InlineKeyboardButton("Sell",        callback_data=f"sell_pick_{m}_{g}"),
            InlineKeyboardButton("My position", callback_data=f"mypos_{m}_{g}"),
        ],
        [InlineKeyboardButton("Refresh", callback_data=f"load_{m}_{g}")],
    ])

def kb_amounts(side: str, market_id: int, group_id: int) -> InlineKeyboardMarkup:
    g, m = group_id, market_id
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("50",  callback_data=f"amt_{side}_{m}_50_{g}"),
            InlineKeyboardButton("100", callback_data=f"amt_{side}_{m}_100_{g}"),
            InlineKeyboardButton("200", callback_data=f"amt_{side}_{m}_200_{g}"),
            InlineKeyboardButton("500", callback_data=f"amt_{side}_{m}_500_{g}"),
        ],
        [InlineKeyboardButton("Back", callback_data=f"load_{m}_{g}")],
    ])

def kb_sell_sides(market_id: int, group_id: int, has_yes: bool, has_no: bool) -> InlineKeyboardMarkup:
    g, m = group_id, market_id
    row  = []
    if has_yes:
        row.append(InlineKeyboardButton("Sell YES", callback_data=f"sellside_YES_{m}_{g}"))
    if has_no:
        row.append(InlineKeyboardButton("Sell NO",  callback_data=f"sellside_NO_{m}_{g}"))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("Back", callback_data=f"load_{m}_{g}")]])

def kb_sell_amounts(side: str, market_id: int, group_id: int, max_shares: float) -> InlineKeyboardMarkup:
    g, m = group_id, market_id
    q25  = round(max_shares * 0.25, 3)
    q50  = round(max_shares * 0.50, 3)
    q100 = round(max_shares, 3)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"25% ({q25})",  callback_data=f"sellamt_{side}_{m}_{q25}_{g}"),
            InlineKeyboardButton(f"50% ({q50})",  callback_data=f"sellamt_{side}_{m}_{q50}_{g}"),
            InlineKeyboardButton(f"All ({q100})", callback_data=f"sellamt_{side}_{m}_{q100}_{g}"),
        ],
        [InlineKeyboardButton("Back", callback_data=f"sell_pick_{m}_{g}")],
    ])
