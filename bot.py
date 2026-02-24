"""
PolyFriends Bot - simplified UX + full inline mode
===================================================
Slash commands are kept minimal. Almost everything happens through
market cards posted into the chat via @bot inline query.

Flow:
  User types @bot          → list of open markets + balance + leaderboard cards
  Taps a market card       → posts card into chat with action buttons
  Taps Bet YES / Bet NO    → amount picker buttons appear on the card
  Taps an amount           → card updates with result (visible to all)
  Taps Sell                → side picker → amount picker → card updates
"""

import logging
import os
from datetime import datetime, timedelta
from uuid import uuid4

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    InlineQueryHandler, ChosenInlineResultHandler,
    ConversationHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ParseMode

import logic
import formatting as fmt
from database import init_db, get_db

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]

# Conversation states for /propose
PROPOSE_QUESTION, PROPOSE_DEADLINE = range(2)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def register(update: Update) -> dict:
    u = update.effective_user
    return logic.get_or_create_user(u.id, u.username or u.first_name)

async def ensure_group(update: Update) -> dict:
    c = update.effective_chat
    return logic.get_or_create_group(c.id, c.title or str(c.id))

def group_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == "private":
            await update.message.reply_text("⚠️ Use this command inside a group chat.")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper

async def notify_admins(app, group_id: int, text: str):
    for uid in logic.get_admin_ids(group_id):
        try:
            await app.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

def _parse_deadline(text: str):
    try:
        if text.endswith("d"):
            return (datetime.utcnow() + timedelta(days=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        if text.endswith("h"):
            return (datetime.utcnow() + timedelta(hours=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        return datetime.strptime(text, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None

def _group_id_from_cb(data: str) -> int:
    """group_id is always the last segment of callback_data."""
    return int(data.rsplit("_", 1)[-1])


# ─────────────────────────────────────────────────────────────────────────────
# Simple slash commands (kept as minimal as possible)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await register(update)
    await update.message.reply_text(
        "👋 *Welcome to PolyFriends!*\n\n"
        "To play in a group:\n"
        "1️⃣ Type */join* in the group chat\n"
        "2️⃣ Type *@YourBot* in the group to browse & bet on markets\n\n"
        "That's it! Everything else happens inside the market cards 🎯",
        parse_mode=ParseMode.MARKDOWN
    )

@group_only
async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = await register(update)
    group = await ensure_group(update)
    newly = logic.join_group(user["telegram_id"], group["group_id"])

    if newly:
        await update.message.reply_text(
            f"🎉 *{user['username']}* joined *{group['name']}*!\n"
            f"You have *1000 points* to bet with.\n\n"
            f"Type @{ctx.bot.username} to browse markets 👆",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        m = logic.get_membership(user["telegram_id"], group["group_id"])
        await update.message.reply_text(
            f"You're already in *{group['name']}*.\n"
            f"Balance: *{m['balance']:.1f} pts*\n\n"
            f"Type @{ctx.bot.username} to browse markets 👆",
            parse_mode=ParseMode.MARKDOWN
        )

@group_only
async def cmd_propose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await register(update)
    await ensure_group(update)
    if not logic.is_member(update.effective_user.id, update.effective_chat.id):
        await update.message.reply_text("Use /join first!")
        return ConversationHandler.END

    ctx.user_data["propose_group_id"] = update.effective_chat.id
    await update.message.reply_text(
        "📝 What's your market question? (clear YES/NO question)\n\n"
        "Example: _Will it rain in London on Friday?_\n\n"
        "/cancel to abort.",
        parse_mode=ParseMode.MARKDOWN
    )
    return PROPOSE_QUESTION

async def propose_got_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["question"] = update.message.text
    await update.message.reply_text(
        "⏰ When should betting close?\n\n"
        "Type `7d`, `24h` or a date like `2025-06-01 20:00` (UTC)",
        parse_mode=ParseMode.MARKDOWN
    )
    return PROPOSE_DEADLINE

async def propose_got_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user     = await register(update)
    deadline = _parse_deadline(update.message.text.strip())
    if not deadline:
        await update.message.reply_text("❌ Can't parse that. Try `7d`, `48h` or `2025-06-01 20:00`")
        return PROPOSE_DEADLINE

    question = ctx.user_data["question"]
    group_id = ctx.user_data["propose_group_id"]
    market_id = logic.propose_market(question, user["telegram_id"], group_id, deadline)

    await update.message.reply_text(
        f"✅ *Market #{market_id} proposed!*\n_{question}_\nDeadline: {deadline} UTC\n\nWaiting for admin approval…",
        parse_mode=ParseMode.MARKDOWN
    )
    await notify_admins(ctx.application, group_id,
        f"📢 New proposal from @{user['username']}\n\n"
        f"*#{market_id}:* {question}\nDeadline: {deadline} UTC\n\n"
        f"/approve {market_id} | /reject {market_id}"
    )
    return ConversationHandler.END

async def propose_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ── Admin slash commands ──────────────────────────────────────────────────────

async def _admin_check(update: Update) -> bool:
    if not logic.is_admin(update.effective_user.id, update.effective_chat.id):
        await update.message.reply_text("⛔ Admins only.")
        return False
    return True

@group_only
async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_check(update): return
    group = await ensure_group(update)
    if not ctx.args:
        await update.message.reply_text("Usage: /approve <id>"); return
    mid = int(ctx.args[0])
    logic.approve_market(mid, update.effective_user.id, group["group_id"])
    market = logic.get_market(mid, group["group_id"])
    await update.message.reply_text(
        f"✅ Market #{mid} is now open!\n\n{fmt.market_card(market)}\n\n"
        f"Share it: type @{ctx.bot.username} in the chat",
        parse_mode=ParseMode.MARKDOWN
    )

@group_only
async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_check(update): return
    group = await ensure_group(update)
    if not ctx.args:
        await update.message.reply_text("Usage: /reject <id>"); return
    logic.reject_market(int(ctx.args[0]), update.effective_user.id, group["group_id"])
    await update.message.reply_text(f"❌ Market #{ctx.args[0]} rejected.")

@group_only
async def cmd_resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_check(update): return
    group = await ensure_group(update)
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /resolve <id> YES|NO|CANCELLED"); return
    mid, res = int(ctx.args[0]), ctx.args[1].upper()
    try:
        logic.resolve_market(mid, group["group_id"], res, update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}"); return
    market = logic.get_market(mid, group["group_id"])
    await update.message.reply_text(
        f"🏁 *Market #{mid} resolved: {res}*\n\n{fmt.market_card(market)}",
        parse_mode=ParseMode.MARKDOWN
    )

@group_only
async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_check(update): return
    group   = await ensure_group(update)
    markets = logic.get_markets(group["group_id"], "pending")
    if not markets:
        await update.message.reply_text("No pending markets."); return
    lines = [f"⏳ *Pending ({len(markets)})*\n"]
    for m in markets:
        lines.append(f"*#{m['id']}* {m['question']}\n/approve {m['id']} | /reject {m['id']}\n")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_addadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    group    = logic.get_or_create_group(update.effective_chat.id, update.effective_chat.title or "")
    group_id = group["group_id"]
    caller   = update.effective_user.id
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM admins WHERE group_id=?", (group_id,)).fetchone()[0]
    if count > 0 and not logic.is_admin(caller, group_id):
        await update.message.reply_text("⛔ Only existing admins can add admins."); return
    logic.add_admin(caller, group_id)
    await update.message.reply_text("✅ You're now an admin in this group.")

@group_only
async def cmd_givepoints(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_check(update): return
    group = await ensure_group(update)
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /givepoints @username <amount>"); return
    username = ctx.args[0].lstrip("@")
    try: amount = float(ctx.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number."); return
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        await update.message.reply_text(f"User @{username} not found."); return
    logic.give_points(target["telegram_id"], group["group_id"], amount)
    await update.message.reply_text(f"✅ Gave *{amount:.0f} pts* to @{username}.", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────────────────────────────────────
# Inline Query - the main entry point for players
# ─────────────────────────────────────────────────────────────────────────────

async def inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.strip().lower()
    user_id    = update.inline_query.from_user.id
    results    = []

    # All groups this user has joined
    with get_db() as conn:
        memberships = conn.execute(
            """SELECT g.group_id, g.name, m.balance
               FROM memberships m JOIN groups g ON g.group_id = m.group_id
               WHERE m.user_id = ?""",
            (user_id,)
        ).fetchall()

    memberships = [dict(r) for r in memberships]

    # ── Balance card (always first when no query) ─────────────────────────
    if not query_text and memberships:
        results.append(InlineQueryResultArticle(
            id="balance",
            title="💰 My Balances",
            description=" · ".join(f"{g['name']}: {g['balance']:.0f}pts" for g in memberships),
            input_message_content=InputTextMessageContent(
                fmt.balance_card(update.inline_query.from_user.first_name, memberships),
                parse_mode=ParseMode.MARKDOWN,
            ),
        ))

    # ── Leaderboard cards ─────────────────────────────────────────────────
    if not query_text:
        for g in memberships:
            rows = logic.get_leaderboard(g["group_id"])
            results.append(InlineQueryResultArticle(
                id=f"lb_{g['group_id']}",
                title=f"🏆 {g['name']} Leaderboard",
                description=f"Top: {', '.join(r['username'] for r in rows[:3])}",
                input_message_content=InputTextMessageContent(
                    fmt.leaderboard_card(rows, g["name"]),
                    parse_mode=ParseMode.MARKDOWN,
                ),
            ))

    # ── Market cards (one per open market across all joined groups) ───────
    for g in memberships:
        markets = logic.get_markets(g["group_id"], "open")
        for m in markets:
            if query_text and query_text not in m["question"].lower() and query_text != str(m["id"]):
                continue
            # Buttons are injected AFTER posting via chosen_inline_result handler.
            # reply_markup on InlineQueryResultArticle does NOT attach buttons to the message.
            results.append(InlineQueryResultArticle(
                id=f"market_{g['group_id']}_{m['id']}",
                title=fmt.market_card_short(m),
                description="Tap to post this market card with bet buttons",
                input_message_content=InputTextMessageContent(
                    fmt.market_card(m) + "\n\n_⏳ Loading buttons…_",
                    parse_mode=ParseMode.MARKDOWN,
                ),
            ))

    await update.inline_query.answer(results[:50], cache_time=5)


# ─────────────────────────────────────────────────────────────────────────────
# Chosen inline result - fires when user picks a result from the inline list.
# This is the ONLY reliable way to attach buttons to an inline-posted message.
# REQUIREMENT: Set inline feedback to 100% in BotFather → /setinlinefeedback
# ─────────────────────────────────────────────────────────────────────────────

async def chosen_inline_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    result    = update.chosen_inline_result
    result_id = result.result_id   # e.g. "market_-1001234_7"

    if not result_id.startswith("market_"):
        return  # balance/leaderboard cards need no buttons

    try:
        _, group_id_str, market_id_str = result_id.split("_", 2)
        group_id  = int(group_id_str)
        market_id = int(market_id_str)
    except ValueError:
        return

    market = logic.get_market(market_id, group_id)
    if not market:
        return

    # Edit the just-posted message → add real card text + action buttons
    try:
        await ctx.bot.edit_message_text(
            inline_message_id=result.inline_message_id,
            text=fmt.market_card(market),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=fmt.kb_market(market_id, group_id),
        )
    except Exception as e:
        logger.warning(f"Could not edit inline message: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Callback handlers - all the button logic on posted market cards
# ─────────────────────────────────────────────────────────────────────────────

def _user_from_query(query) -> dict:
    u = query.from_user
    return logic.get_or_create_user(u.id, u.username or u.first_name)

def _check_member(user_id: int, group_id: int, query) -> bool:
    if not logic.is_member(user_id, group_id):
        return False
    return True

async def _refresh_card(query, market: dict, group_id: int):
    """Update the card text + restore main keyboard."""
    await query.edit_message_text(
        fmt.market_card(market),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_market(market["id"], group_id),
    )

# ── Refresh ───────────────────────────────────────────────────────────────────

async def cb_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # data: refresh_<market_id>_<group_id>
    query     = update.callback_query
    await query.answer()
    group_id  = _group_id_from_cb(query.data)
    market_id = int(query.data.split("_")[1])
    market    = logic.get_market(market_id, group_id)
    if not market:
        await query.answer("Market not found.", show_alert=True); return
    await _refresh_card(query, market, group_id)

# ── Bet side picker ───────────────────────────────────────────────────────────

async def cb_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User tapped Bet YES or Bet NO - show amount buttons."""
    # data: side_<YES|NO>_<market_id>_<group_id>
    query    = update.callback_query
    await query.answer()
    user     = _user_from_query(query)
    group_id = _group_id_from_cb(query.data)

    if not _check_member(user["telegram_id"], group_id, query):
        await query.answer("Use /join in this group first!", show_alert=True); return

    parts     = query.data.split("_")
    side      = parts[1]
    market_id = int(parts[2])
    market    = logic.get_market(market_id, group_id)

    if not market or market["status"] != "open":
        await query.answer("This market is no longer open.", show_alert=True); return

    m           = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    emoji       = "✅" if side == "YES" else "❌"

    await query.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"{emoji} *Betting {side}* at {price:.0%}\n"
        f"Your balance: *{m['balance']:.1f} pts*\n\n"
        f"How many points?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_amounts(side, market_id, group_id),
    )

# ── Bet amount ────────────────────────────────────────────────────────────────

async def cb_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User picked an amount - place the bet."""
    # data: amt_<YES|NO>_<market_id>_<points>_<group_id>
    query    = update.callback_query
    await query.answer()
    user     = _user_from_query(query)
    group_id = _group_id_from_cb(query.data)

    parts     = query.data.split("_")
    side      = parts[1]
    market_id = int(parts[2])
    points    = float(parts[3])

    try:
        market, shares = logic.place_bet(user["telegram_id"], group_id, market_id, side, points)
    except logic.BetError as e:
        await query.answer(str(e), show_alert=True); return

    m           = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    emoji       = "✅" if side == "YES" else "❌"

    await query.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"*{user['username']}* bet {emoji} *{side}* - "
        f"{shares:.2f} shares for {points:.0f} pts @ {price:.0%}\n"
        f"Balance: {m['balance']:.1f} pts",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_market(market_id, group_id),
    )

# ── My position ───────────────────────────────────────────────────────────────

async def cb_mypos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # data: mypos_<market_id>_<group_id>
    query     = update.callback_query
    user      = _user_from_query(query)
    group_id  = _group_id_from_cb(query.data)
    market_id = int(query.data.split("_")[1])

    if not _check_member(user["telegram_id"], group_id, query):
        await query.answer("Use /join in this group first!", show_alert=True); return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_here  = [p for p in positions if p["market_id"] == market_id]

    if not pos_here:
        await query.answer("You have no position in this market yet.", show_alert=True); return

    text = "\n\n".join(fmt.position_card(p) for p in pos_here)
    await query.answer(text[:200], show_alert=True)

# ── Sell: pick side ───────────────────────────────────────────────────────────

async def cb_sell_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # data: sell_pick_<market_id>_<group_id>
    query     = update.callback_query
    await query.answer()
    user      = _user_from_query(query)
    group_id  = _group_id_from_cb(query.data)
    market_id = int(query.data.split("_")[2])

    if not _check_member(user["telegram_id"], group_id, query):
        await query.answer("Use /join first!", show_alert=True); return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_here  = {p["side"]: p for p in positions if p["market_id"] == market_id}

    if not pos_here:
        await query.answer("You have no position in this market.", show_alert=True); return

    market  = logic.get_market(market_id, group_id)
    has_yes = "YES" in pos_here
    has_no  = "NO"  in pos_here

    await query.edit_message_text(
        f"{fmt.market_card(market)}\n\n💸 *Which position do you want to sell?*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_sell_sides(market_id, group_id, has_yes, has_no),
    )

# ── Sell: pick side → show amount buttons ─────────────────────────────────────

async def cb_sell_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # data: sellside_<YES|NO>_<market_id>_<group_id>
    query    = update.callback_query
    await query.answer()
    user     = _user_from_query(query)
    group_id = _group_id_from_cb(query.data)

    parts     = query.data.split("_")
    side      = parts[1]
    market_id = int(parts[2])

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos       = next((p for p in positions if p["market_id"] == market_id and p["side"] == side), None)

    if not pos or pos["net_shares"] <= 0:
        await query.answer("No shares to sell.", show_alert=True); return

    market      = logic.get_market(market_id, group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    value       = pos["net_shares"] * price

    await query.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"💸 Selling *{side}* shares\n"
        f"You hold: {pos['net_shares']:.3f} shares ≈ {value:.1f} pts at {price:.0%}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_sell_amounts(side, market_id, group_id, pos["net_shares"]),
    )

# ── Sell: execute ─────────────────────────────────────────────────────────────

async def cb_sell_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # data: sellamt_<YES|NO>_<market_id>_<shares>_<group_id>
    query    = update.callback_query
    await query.answer()
    user     = _user_from_query(query)
    group_id = _group_id_from_cb(query.data)

    parts          = query.data.split("_")
    side           = parts[1]
    market_id      = int(parts[2])
    shares_to_sell = float(parts[3])

    try:
        market, payout = logic.sell_position(user["telegram_id"], group_id, market_id, side, shares_to_sell)
    except logic.SellError as e:
        await query.answer(str(e), show_alert=True); return

    m = logic.get_membership(user["telegram_id"], group_id)
    await query.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"*{user['username']}* sold {shares_to_sell} *{side}* shares → +{payout:.1f} pts\n"
        f"Balance: {m['balance']:.1f} pts",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_market(market_id, group_id),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler jobs
# ─────────────────────────────────────────────────────────────────────────────

async def job_close_expired(ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        markets = conn.execute(
            "SELECT * FROM markets WHERE status='open' AND deadline IS NOT NULL"
        ).fetchall()
    now = datetime.utcnow()
    for m in markets:
        m = dict(m)
        if datetime.fromisoformat(m["deadline"]) < now:
            logic.close_market(m["id"])
            logger.info(f"Auto-closed market #{m['id']}")
            await notify_admins(ctx.application, m["group_id"],
                f"⏰ Market *#{m['id']}* closed.\n_{m['question']}_\n\n"
                f"Resolve: /resolve {m['id']} YES | NO | CANCELLED"
            )

async def job_weekly_refill(ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        groups = conn.execute("SELECT group_id FROM groups").fetchall()
    for g in groups:
        group_id = g["group_id"]
        affected = logic.do_weekly_refill(group_id)
        if affected:
            try:
                await ctx.bot.send_message(
                    group_id,
                    fmt.refill_announce(affected),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# App wiring
# ─────────────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    # Propose conversation
    propose_conv = ConversationHandler(
        entry_points=[CommandHandler("propose", cmd_propose)],
        states={
            PROPOSE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, propose_got_question)],
            PROPOSE_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, propose_got_deadline)],
        },
        fallbacks=[CommandHandler("cancel", propose_cancel)],
    )

    # Slash commands (kept minimal)
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("join",       cmd_join))
    app.add_handler(CommandHandler("addadmin",   cmd_addadmin))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))
    app.add_handler(CommandHandler("resolve",    cmd_resolve))
    app.add_handler(CommandHandler("pending",    cmd_pending))
    app.add_handler(CommandHandler("givepoints", cmd_givepoints))
    app.add_handler(propose_conv)

    # Inline query + chosen result (chosen result injects buttons after posting)
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))

    # Card button callbacks - group_id is embedded in callback_data as last segment
    # Pattern: action_[side_]marketId_groupId
    app.add_handler(CallbackQueryHandler(cb_refresh,     pattern=r"^refresh_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_side,        pattern=r"^side_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_amount,      pattern=r"^amt_(YES|NO)_\d+_[\d.]+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mypos,       pattern=r"^mypos_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_pick,   pattern=r"^sell_pick_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_side,   pattern=r"^sellside_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_amount, pattern=r"^sellamt_(YES|NO)_\d+_[\d.]+_-?\d+$"))

    # Scheduler
    app.job_queue.run_repeating(job_close_expired,  interval=60,     first=15)
    app.job_queue.run_repeating(job_weekly_refill,  interval=604800, first=30)

    logger.info("🚀 PolyFriends v2 started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
