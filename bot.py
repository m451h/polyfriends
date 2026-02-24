"""
PolyFriends Bot v5
==================

Changes from v4:
- Markets open instantly — no admin approval needed
- Propose works fully inline (card with buttons, guided flow via DM)
- Admin /resolve still settles markets + pays out winners
- Fixed MarkdownV2 slash/escape bug throughout
- Simplified text: plain text where possible, markdown only when needed
"""

import logging
import os
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    InlineQueryHandler, ConversationHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ParseMode

import logic
import formatting as fmt
from database import init_db, get_db

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]

# Conversation states
ASK_QUESTION, ASK_DEADLINE = range(2)
# DM propose states (triggered from inline)
DM_QUESTION, DM_DEADLINE = range(2, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gid(data: str) -> int:
    return int(data.rsplit("_", 1)[-1])

def _parse_deadline(text: str):
    text = text.strip()
    try:
        if text.endswith("d"):
            return (datetime.utcnow() + timedelta(days=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        if text.endswith("h"):
            return (datetime.utcnow() + timedelta(hours=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        return datetime.strptime(text, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None

async def _reg(update: Update) -> dict:
    u = update.effective_user
    return logic.get_or_create_user(u.id, u.username or u.first_name)

async def _grp(update: Update) -> dict:
    c = update.effective_chat
    return logic.get_or_create_group(c.id, c.title or str(c.id))

def _md(text: str) -> str:
    """Escape special MarkdownV2 chars in user-provided text."""
    for c in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(c, f"\\{c}")
    return text

async def _reply(update: Update, text: str, **kw):
    """Send a plain message — no markdown, clean output."""
    await update.message.reply_text(text, **kw)

async def _reply_md(update: Update, text: str, **kw):
    """Send a MarkdownV2 message."""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, **kw)

def _group_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == "private":
            await update.message.reply_text("This command only works in a group chat.")
            return ConversationHandler.END
        return await fn(update, ctx)
    wrapper.__name__ = fn.__name__
    return wrapper

async def _notify_admins(app, group_id: int, text: str):
    for uid in logic.get_admin_ids(group_id):
        try:
            await app.bot.send_message(uid, text)
        except Exception:
            pass

def _memberships(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT g.group_id, g.name, m.balance
               FROM memberships m JOIN groups g ON g.group_id = m.group_id
               WHERE m.user_id = ?""",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    me = await ctx.bot.get_me()
    await _reply(update,
        f"Welcome to PolyFriends!\n\n"
        f"1. Add me to a group\n"
        f"2. Type /join in the group to get 1000 points\n"
        f"3. Type @{me.username} in the group to browse and bet\n\n"
        f"Markets open instantly when you propose them. Have fun!"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /join
# ─────────────────────────────────────────────────────────────────────────────

@_group_only
async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = await _reg(update)
    group = await _grp(update)
    me    = await ctx.bot.get_me()
    newly = logic.join_group(user["telegram_id"], group["group_id"])

    if newly:
        await _reply(update,
            f"{user['username']} joined {group['name']}!\n"
            f"You have 1000 points to start betting.\n\n"
            f"Type @{me.username} to see open markets."
        )
    else:
        m = logic.get_membership(user["telegram_id"], group["group_id"])
        await _reply(update,
            f"Already in {group['name']}.\n"
            f"Balance: {m['balance']:.1f} pts\n\n"
            f"Type @{me.username} to see open markets."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /propose — slash command (group only, instant open)
# ─────────────────────────────────────────────────────────────────────────────

@_group_only
async def cmd_propose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = await _reg(update)
    group = await _grp(update)

    if not logic.is_member(user["telegram_id"], group["group_id"]):
        await _reply(update, "Use /join first!")
        return ConversationHandler.END

    ctx.user_data["propose_group_id"] = group["group_id"]
    await _reply(update,
        "What's your market question? (clear YES/NO question)\n\n"
        "Example: Will it rain on Friday?\n\n"
        "/cancel to abort."
    )
    return ASK_QUESTION

async def propose_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["propose_q"] = update.message.text
    await _reply(update,
        "When should betting close?\n\n"
        "Type 7d, 24h or a date like 2025-06-01 20:00 (UTC)"
    )
    return ASK_DEADLINE

async def propose_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user     = await _reg(update)
    deadline = _parse_deadline(update.message.text)
    if not deadline:
        await _reply(update, "Can't parse that. Try 7d, 48h or 2025-06-01 20:00")
        return ASK_DEADLINE

    question = ctx.user_data["propose_q"]
    group_id = ctx.user_data["propose_group_id"]

    # Open instantly — no approval needed
    market_id = logic.propose_market(question, user["telegram_id"], group_id, deadline)
    logic.approve_market(market_id, user["telegram_id"], group_id)

    market = logic.get_market(market_id, group_id)
    me = await ctx.bot.get_me()
    await _reply(update,
        f"Market #{market_id} is open!\n\n"
        f"{question}\n"
        f"Deadline: {deadline} UTC\n\n"
        f"Type @{me.username} to share it in the chat."
    )
    return ConversationHandler.END

async def propose_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "Cancelled.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# DM propose flow — triggered when user taps "Propose" in inline UI
# State is stored in bot_data keyed by user_id
# ─────────────────────────────────────────────────────────────────────────────

async def dm_message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles DM text messages for the inline-triggered propose flow."""
    if update.effective_chat.type != "private":
        return

    user_id  = update.effective_user.id
    state    = ctx.bot_data.get(f"dm_state_{user_id}")
    group_id = ctx.bot_data.get(f"dm_group_{user_id}")

    if state is None or group_id is None:
        return  # not in a DM flow

    if state == DM_QUESTION:
        ctx.bot_data[f"dm_q_{user_id}"] = update.message.text
        ctx.bot_data[f"dm_state_{user_id}"] = DM_DEADLINE
        await _reply(update,
            "When should betting close?\n\n"
            "Type 7d, 24h or a date like 2025-06-01 20:00 (UTC)"
        )

    elif state == DM_DEADLINE:
        deadline = _parse_deadline(update.message.text)
        if not deadline:
            await _reply(update, "Can't parse that. Try 7d, 48h or 2025-06-01 20:00")
            return

        user     = await _reg(update)
        question = ctx.bot_data.get(f"dm_q_{user_id}", "")

        # Open instantly
        market_id = logic.propose_market(question, user_id, group_id, deadline)
        logic.approve_market(market_id, user_id, group_id)

        # Clean up state
        for k in [f"dm_state_{user_id}", f"dm_group_{user_id}", f"dm_q_{user_id}"]:
            ctx.bot_data.pop(k, None)

        market = logic.get_market(market_id, group_id)
        me = await ctx.bot.get_me()
        await _reply(update,
            f"Market #{market_id} is now open!\n\n"
            f"{question}\n"
            f"Deadline: {deadline} UTC\n\n"
            f"Go back to the group and type @{me.username} to share it!"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Admin commands (only /resolve and /givepoints remain essential)
# ─────────────────────────────────────────────────────────────────────────────

async def _is_admin(update: Update) -> bool:
    if not logic.is_admin(update.effective_user.id, update.effective_chat.id):
        await _reply(update, "Admins only.")
        return False
    return True

async def cmd_addadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    group    = logic.get_or_create_group(
        update.effective_chat.id, update.effective_chat.title or ""
    )
    group_id = group["group_id"]
    caller   = update.effective_user.id

    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM admins WHERE group_id=?", (group_id,)
        ).fetchone()[0]

    if count > 0 and not logic.is_admin(caller, group_id):
        await _reply(update, "Only existing admins can add admins.")
        return

    logic.add_admin(caller, group_id)
    await _reply(update, "You are now an admin in this group.")

@_group_only
async def cmd_resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if len(ctx.args) < 2:
        await _reply(update, "Usage: /resolve <id> YES|NO|CANCELLED")
        return
    mid, res = int(ctx.args[0]), ctx.args[1].upper()
    try:
        logic.resolve_market(mid, group["group_id"], res, update.effective_user.id)
    except ValueError as e:
        await _reply(update, str(e))
        return
    market = logic.get_market(mid, group["group_id"])
    await _reply(update, fmt.market_card_plain(market) + f"\n\nResolved: {res}")

@_group_only
async def cmd_givepoints(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if len(ctx.args) < 2:
        await _reply(update, "Usage: /givepoints @username <amount>")
        return
    uname = ctx.args[0].lstrip("@")
    try:
        amount = float(ctx.args[1])
    except ValueError:
        await _reply(update, "Amount must be a number.")
        return
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
    if not target:
        await _reply(update, f"User @{uname} not found.")
        return
    logic.give_points(target["telegram_id"], group["group_id"], amount)
    await _reply(update, f"Gave {amount:.0f} pts to @{uname}.")


# ─────────────────────────────────────────────────────────────────────────────
# Inline query
# ─────────────────────────────────────────────────────────────────────────────

async def inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q_text  = update.inline_query.query.strip().lower()
    user_id = update.inline_query.from_user.id
    uname   = update.inline_query.from_user.first_name
    results = []

    memberships = _memberships(user_id)

    if not q_text:
        # Balance card
        results.append(InlineQueryResultArticle(
            id="balance",
            title="My Balances",
            description=" | ".join(f"{g['name']}: {g['balance']:.0f}pts" for g in memberships)
                        or "Join a group with /join first",
            input_message_content=InputTextMessageContent(
                fmt.balance_plain(uname, memberships)
            ),
        ))

        # Leaderboard per group
        for g in memberships:
            rows = logic.get_leaderboard(g["group_id"])
            results.append(InlineQueryResultArticle(
                id=f"lb_{g['group_id']}",
                title=f"Leaderboard: {g['name']}",
                description=" | ".join(f"{r['username']} {r['balance']:.0f}" for r in rows[:3]),
                input_message_content=InputTextMessageContent(
                    fmt.leaderboard_plain(rows, g["name"])
                ),
            ))

        # Propose card per group — posts a card with a Start button
        for g in memberships:
            results.append(InlineQueryResultArticle(
                id=f"propose_{g['group_id']}",
                title=f"+ Propose a market in {g['name']}",
                description="Tap to propose a new market",
                input_message_content=InputTextMessageContent(
                    f"New market proposal for {g['name']} — loading..."
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Start Proposal",
                        callback_data=f"propose_start_{g['group_id']}"
                    )
                ]]),
            ))

    # Market cards
    for g in memberships:
        for m in logic.get_markets(g["group_id"], "open"):
            if q_text and q_text not in m["question"].lower() and q_text != str(m["id"]):
                continue
            results.append(InlineQueryResultArticle(
                id=f"m_{g['group_id']}_{m['id']}",
                title=f"#{m['id']} {m['question'][:55]}",
                description="Tap to post market card with bet buttons",
                input_message_content=InputTextMessageContent(
                    fmt.market_card_plain(m)
                ),
                reply_markup=fmt.kb_load(m["id"], g["group_id"]),
            ))

    await update.inline_query.answer(results[:50], cache_time=5)


# ─────────────────────────────────────────────────────────────────────────────
# Callback handlers
# ─────────────────────────────────────────────────────────────────────────────

def _uinfo(query) -> dict:
    u = query.from_user
    return logic.get_or_create_user(u.id, u.username or u.first_name)

async def _show_market(q, market: dict, group_id: int, note: str = ""):
    text = fmt.market_card_plain(market)
    if note:
        text += f"\n\n{note}"
    await q.edit_message_text(
        text,
        reply_markup=fmt.kb_market(market["id"], group_id),
    )


# ── load_<market_id>_<group_id> ───────────────────────────────────────────────

async def cb_load(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    await q.answer()
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[1])
    market    = logic.get_market(market_id, group_id)
    if not market:
        await q.answer("Market not found.", show_alert=True)
        return
    await _show_market(q, market, group_id)


# ── propose_start_<group_id> ──────────────────────────────────────────────────
# Tapping "Start Proposal" on the inline propose card sends the user a DM
# and updates the card to show "Check your DMs!"

async def cb_propose_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join in the group first!", show_alert=True)
        return

    group = logic.get_group(group_id)

    # Start DM state
    ctx.bot_data[f"dm_state_{user['telegram_id']}"] = DM_QUESTION
    ctx.bot_data[f"dm_group_{user['telegram_id']}"] = group_id

    # Try to DM the user
    try:
        await ctx.bot.send_message(
            user["telegram_id"],
            f"New market proposal for {group['name']}\n\n"
            f"What's your YES/NO question?\n\n"
            f"Example: Will it rain on Friday?"
        )
        await q.edit_message_text(
            f"Proposing a market in {group['name']}\n\n"
            f"Check your DMs with me to continue!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Open DM", url=f"https://t.me/{(await ctx.bot.get_me()).username}")
            ]])
        )
    except Exception:
        # Bot can't DM — user needs to start chat first
        me = await ctx.bot.get_me()
        await q.edit_message_text(
            f"To propose a market, start a chat with me first:\n"
            f"https://t.me/{me.username}\n\n"
            f"Then come back and tap Start Proposal again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Start chat with bot", url=f"https://t.me/{me.username}")
            ]])
        )


# ── side_<YES|NO>_<market_id>_<group_id> ─────────────────────────────────────

async def cb_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")
    side, market_id = parts[1], int(parts[2])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join in this group first!", show_alert=True)
        return

    market = logic.get_market(market_id, group_id)
    if not market or market["status"] != "open":
        await q.answer("This market is no longer open.", show_alert=True)
        return

    mem         = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    emoji       = "YES" if side == "YES" else "NO"

    await q.edit_message_text(
        f"{fmt.market_card_plain(market)}\n\n"
        f"Betting {emoji} at {price:.0%}\n"
        f"Your balance: {mem['balance']:.1f} pts\n\n"
        f"How many points?",
        reply_markup=fmt.kb_amounts(side, market_id, group_id),
    )


# ── amt_<YES|NO>_<market_id>_<pts>_<group_id> ────────────────────────────────

async def cb_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")
    side, market_id, points = parts[1], int(parts[2]), float(parts[3])

    try:
        market, shares = logic.place_bet(user["telegram_id"], group_id, market_id, side, points)
    except logic.BetError as e:
        await q.answer(str(e), show_alert=True)
        return

    mem         = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p

    await _show_market(q, market, group_id,
        f"{user['username']} bet {side} - {shares:.2f} shares for {points:.0f} pts at {price:.0%}\n"
        f"Balance: {mem['balance']:.1f} pts"
    )


# ── mypos_<market_id>_<group_id> ─────────────────────────────────────────────

async def cb_mypos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    user      = _uinfo(q)
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[1])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join first!", show_alert=True)
        return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_here  = [p for p in positions if p["market_id"] == market_id]

    if not pos_here:
        await q.answer("You have no position in this market yet.", show_alert=True)
        return

    text = "\n\n".join(fmt.position_text(p) for p in pos_here)
    await q.answer(text[:200], show_alert=True)


# ── sell_pick_<market_id>_<group_id> ─────────────────────────────────────────

async def cb_sell_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    await q.answer()
    user      = _uinfo(q)
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[2])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join first!", show_alert=True)
        return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_map   = {p["side"]: p for p in positions if p["market_id"] == market_id}

    if not pos_map:
        await q.answer("You have no position in this market.", show_alert=True)
        return

    market = logic.get_market(market_id, group_id)
    await q.edit_message_text(
        f"{fmt.market_card_plain(market)}\n\nWhich side do you want to sell?",
        reply_markup=fmt.kb_sell_sides(market_id, group_id, "YES" in pos_map, "NO" in pos_map),
    )


# ── sellside_<YES|NO>_<market_id>_<group_id> ─────────────────────────────────

async def cb_sell_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")
    side, market_id = parts[1], int(parts[2])

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos       = next((p for p in positions if p["market_id"] == market_id and p["side"] == side), None)

    if not pos or pos["net_shares"] <= 0:
        await q.answer("No shares to sell.", show_alert=True)
        return

    market      = logic.get_market(market_id, group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    value       = pos["net_shares"] * price

    await q.edit_message_text(
        f"{fmt.market_card_plain(market)}\n\n"
        f"Selling {side} shares\n"
        f"You hold: {pos['net_shares']:.3f} shares = {value:.1f} pts at {price:.0%}",
        reply_markup=fmt.kb_sell_amounts(side, market_id, group_id, pos["net_shares"]),
    )


# ── sellamt_<YES|NO>_<market_id>_<shares>_<group_id> ─────────────────────────

async def cb_sell_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")
    side, market_id, shares = parts[1], int(parts[2]), float(parts[3])

    try:
        market, payout = logic.sell_position(user["telegram_id"], group_id, market_id, side, shares)
    except logic.SellError as e:
        await q.answer(str(e), show_alert=True)
        return

    mem = logic.get_membership(user["telegram_id"], group_id)
    await _show_market(q, market, group_id,
        f"{user['username']} sold {shares} {side} shares   +{payout:.1f} pts\n"
        f"Balance: {mem['balance']:.1f} pts"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────────────────────

async def job_close_expired(ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        markets = conn.execute(
            "SELECT * FROM markets WHERE status='open' AND deadline IS NOT NULL"
        ).fetchall()
    now = datetime.utcnow()
    for m in [dict(r) for r in markets]:
        if datetime.fromisoformat(m["deadline"]) < now:
            logic.close_market(m["id"])
            logger.info(f"Auto-closed market #{m['id']}")
            await _notify_admins(ctx.application, m["group_id"],
                f"Market #{m['id']} has closed.\n{m['question']}\n\n"
                f"Resolve with: /resolve {m['id']} YES or /resolve {m['id']} NO"
            )

async def job_weekly_refill(ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        groups = conn.execute("SELECT group_id FROM groups").fetchall()
    for g in groups:
        affected = logic.do_weekly_refill(g["group_id"])
        if affected:
            try:
                await ctx.bot.send_message(g["group_id"], fmt.refill_text(affected))
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# App wiring
# ─────────────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # /propose conversation (group slash command)
    propose_conv = ConversationHandler(
        entry_points=[CommandHandler("propose", cmd_propose)],
        states={
            ASK_QUESTION: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
                propose_question
            )],
            ASK_DEADLINE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
                propose_deadline
            )],
        },
        fallbacks=[CommandHandler("cancel", propose_cancel)],
    )

    # Commands
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("join",       cmd_join))
    app.add_handler(CommandHandler("addadmin",   cmd_addadmin))
    app.add_handler(CommandHandler("resolve",    cmd_resolve))
    app.add_handler(CommandHandler("givepoints", cmd_givepoints))
    app.add_handler(propose_conv)

    # DM handler for inline-triggered propose flow
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        dm_message_handler
    ))

    # Inline
    app.add_handler(InlineQueryHandler(inline_query))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_load,          pattern=r"^load_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_propose_start, pattern=r"^propose_start_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_side,          pattern=r"^side_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_amount,        pattern=r"^amt_(YES|NO)_\d+_[\d.]+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mypos,         pattern=r"^mypos_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_pick,     pattern=r"^sell_pick_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_side,     pattern=r"^sellside_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_amount,   pattern=r"^sellamt_(YES|NO)_\d+_[\d.]+_-?\d+$"))

    # Scheduler
    app.job_queue.run_repeating(job_close_expired, interval=60,     first=15)
    app.job_queue.run_repeating(job_weekly_refill, interval=604800, first=30)

    logger.info("PolyFriends v5 started!")

    import sys
    if sys.version_info >= (3, 12):
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
