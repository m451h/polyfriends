"""
PolyFriends Bot v4
==================

HOW INLINE WORKS (reliable pattern):
  1. User types @bot in group
  2. Sees list: markets, balance, leaderboard, propose
  3. Taps a market → plain-text placeholder posts into chat
     with ONE button: [Tap to load market]
  4. Anyone taps that button → callback fires → message is
     edited to show the full card with YES/NO/Sell buttons
  5. All subsequent button taps work normally via callbacks

This avoids ChosenInlineResultHandler which is unreliable.
The load button callback is the bridge from inline → interactive.

SLASH COMMANDS (minimal):
  /join      - join this group (get 1000 pts)
  /propose   - propose a market (guided conversation)
  /addadmin  - make yourself admin (first time only)
  /approve   - admin: approve a market
  /reject    - admin: reject a market
  /resolve   - admin: resolve a market
  /pending   - admin: list pending markets
  /givepoints - admin: give points to a player
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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]

# Conversation states for /propose
ASK_QUESTION, ASK_DEADLINE = range(2)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gid(data: str) -> int:
    """Pull group_id from last segment of callback_data."""
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

async def _notify_admins(app, group_id: int, text: str):
    for uid in logic.get_admin_ids(group_id):
        try:
            await app.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

def _group_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == "private":
            await update.message.reply_text(
                "This command only works in a group chat."
            )
            return ConversationHandler.END
        return await fn(update, ctx)
    wrapper.__name__ = fn.__name__
    return wrapper

async def _send(update: Update, text: str, **kw):
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _reg(update)
    me = await ctx.bot.get_me()
    await _send(update,
        f"*Welcome to PolyFriends!*\n\n"
        f"1\\. Add me to a group\n"
        f"2\\. Type /join in the group to get 1000 points\n"
        f"3\\. Type @{me.username} in the group to browse and bet on markets\n\n"
        f"Everything happens through the market cards\\. Enjoy\\!"
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
        await _send(update,
            f"*{fmt._safe(user['username'])}* joined *{fmt._safe(group['name'])}*\\!\n"
            f"You have *1000 points* to start betting\\.\n\n"
            f"Type @{me.username} to see open markets\\."
        )
    else:
        m = logic.get_membership(user["telegram_id"], group["group_id"])
        await _send(update,
            f"Already in *{fmt._safe(group['name'])}*\\.\n"
            f"Balance: *{m['balance']:.1f} pts*\n\n"
            f"Type @{me.username} to see open markets\\."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /propose  (conversation, works in group only)
# ─────────────────────────────────────────────────────────────────────────────

@_group_only
async def cmd_propose(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await _reg(update)
    group = await _grp(update)

    if not logic.is_member(user["telegram_id"], group["group_id"]):
        await update.message.reply_text("Use /join first\\!", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    ctx.user_data["propose_group_id"] = group["group_id"]
    await update.message.reply_text(
        "What's your market question\\? \\(clear YES/NO question\\)\n\n"
        "_Example: Will it rain on Friday?_\n\n"
        "/cancel to abort\\.",
        parse_mode=ParseMode.MARKDOWN
    )
    return ASK_QUESTION

async def propose_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["propose_q"] = update.message.text
    await update.message.reply_text(
        "When should betting close\\?\n\n"
        "Type `7d`, `24h` or a date like `2025-06-01 20:00` \\(UTC\\)",
        parse_mode=ParseMode.MARKDOWN
    )
    return ASK_DEADLINE

async def propose_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user     = await _reg(update)
    deadline = _parse_deadline(update.message.text)
    if not deadline:
        await update.message.reply_text(
            "Can't parse that\\. Try `7d`, `48h` or `2025\\-06\\-01 20:00`",
            parse_mode=ParseMode.MARKDOWN
        )
        return ASK_DEADLINE

    question  = ctx.user_data["propose_q"]
    group_id  = ctx.user_data["propose_group_id"]
    market_id = logic.propose_market(question, user["telegram_id"], group_id, deadline)

    await update.message.reply_text(
        f"*Market \\#{market_id} proposed\\!*\n_{fmt._safe(question)}_\n"
        f"Deadline: {deadline} UTC\n\nWaiting for admin approval\\.",
        parse_mode=ParseMode.MARKDOWN
    )
    await _notify_admins(ctx.application, group_id,
        f"New proposal from @{user['username']}\n\n"
        f"*#{market_id}:* {question}\n"
        f"Deadline: {deadline} UTC\n\n"
        f"/approve {market_id}    /reject {market_id}"
    )
    return ConversationHandler.END

async def propose_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled\\.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Admin commands
# ─────────────────────────────────────────────────────────────────────────────

async def _is_admin(update: Update) -> bool:
    if not logic.is_admin(update.effective_user.id, update.effective_chat.id):
        await update.message.reply_text("Admins only\\.", parse_mode=ParseMode.MARKDOWN)
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
        await update.message.reply_text("Only existing admins can add admins\\.", parse_mode=ParseMode.MARKDOWN)
        return

    logic.add_admin(caller, group_id)
    await update.message.reply_text("You are now an admin in this group\\.", parse_mode=ParseMode.MARKDOWN)

@_group_only
async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group   = await _grp(update)
    markets = logic.get_markets(group["group_id"], "pending")
    if not markets:
        await update.message.reply_text("No pending markets\\.", parse_mode=ParseMode.MARKDOWN)
        return
    lines = [f"*Pending markets \\({len(markets)}\\)*\n"]
    for m in markets:
        lines.append(f"*\\#{m['id']}* {fmt._safe(m['question'])}")
        lines.append(f"Deadline: {m['deadline']} UTC")
        lines.append(f"/approve {m['id']}    /reject {m['id']}\n")
    await _send(update, "\n".join(lines))

@_group_only
async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if not ctx.args:
        await update.message.reply_text("Usage: /approve \\<id\\>", parse_mode=ParseMode.MARKDOWN); return
    mid = int(ctx.args[0])
    logic.approve_market(mid, update.effective_user.id, group["group_id"])
    market = logic.get_market(mid, group["group_id"])
    if not market:
        await update.message.reply_text("Market not found\\."); return
    me = await ctx.bot.get_me()
    await _send(update,
        f"Market \\#{mid} is now open\\!\n\n{fmt.market_card(market)}\n\n"
        f"Share it: type @{me.username} in the chat"
    )

@_group_only
async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if not ctx.args:
        await update.message.reply_text("Usage: /reject \\<id\\>", parse_mode=ParseMode.MARKDOWN); return
    logic.reject_market(int(ctx.args[0]), update.effective_user.id, group["group_id"])
    await update.message.reply_text(f"Market \\#{ctx.args[0]} rejected\\.", parse_mode=ParseMode.MARKDOWN)

@_group_only
async def cmd_resolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /resolve \\<id\\> YES\\|NO\\|CANCELLED", parse_mode=ParseMode.MARKDOWN); return
    mid, res = int(ctx.args[0]), ctx.args[1].upper()
    try:
        logic.resolve_market(mid, group["group_id"], res, update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(str(e)); return
    market = logic.get_market(mid, group["group_id"])
    await _send(update, f"Market \\#{mid} resolved: *{res}*\n\n{fmt.market_card(market)}")

@_group_only
async def cmd_givepoints(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update): return
    group = await _grp(update)
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /givepoints @username \\<amount\\>", parse_mode=ParseMode.MARKDOWN); return
    uname = ctx.args[0].lstrip("@")
    try:    amount = float(ctx.args[1])
    except: await update.message.reply_text("Amount must be a number\\."); return
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
    if not target:
        await update.message.reply_text(f"User @{uname} not found\\."); return
    logic.give_points(target["telegram_id"], group["group_id"], amount)
    await update.message.reply_text(f"Gave *{amount:.0f} pts* to @{uname}\\.", parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────────────────────────────────────
# Inline query  — shows markets list + balance + leaderboard + propose
# ─────────────────────────────────────────────────────────────────────────────

async def inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q       = update.inline_query.query.strip().lower()
    user_id = update.inline_query.from_user.id
    uname   = update.inline_query.from_user.first_name
    results = []

    # Groups this user belongs to
    with get_db() as conn:
        rows = conn.execute(
            """SELECT g.group_id, g.name, m.balance
               FROM memberships m JOIN groups g ON g.group_id = m.group_id
               WHERE m.user_id = ?""",
            (user_id,)
        ).fetchall()
    memberships = [dict(r) for r in rows]

    if not q:
        # Balance card
        results.append(InlineQueryResultArticle(
            id="balance",
            title="My Balances",
            description=" | ".join(f"{g['name']}: {g['balance']:.0f}pts" for g in memberships)
                        or "You haven't joined any groups yet",
            input_message_content=InputTextMessageContent(
                fmt.balance_plain(uname, memberships)
            ),
        ))

        # Leaderboard per group
        for g in memberships:
            rows2 = logic.get_leaderboard(g["group_id"])
            results.append(InlineQueryResultArticle(
                id=f"lb_{g['group_id']}",
                title=f"Leaderboard: {g['name']}",
                description=" | ".join(f"{r['username']} {r['balance']:.0f}" for r in rows2[:3]),
                input_message_content=InputTextMessageContent(
                    fmt.leaderboard_plain(rows2, g["name"])
                ),
            ))

        # Propose card per group
        for g in memberships:
            results.append(InlineQueryResultArticle(
                id=f"propose_{g['group_id']}",
                title=f"+ Propose a market in {g['name']}",
                description="Tap to start a new market proposal",
                input_message_content=InputTextMessageContent(
                    f"Use /propose in {g['name']} to propose a new market!"
                ),
            ))

    # Market cards (filtered by search query)
    for g in memberships:
        for m in logic.get_markets(g["group_id"], "open"):
            if q and q not in m["question"].lower() and q != str(m["id"]):
                continue
            # Post a plain placeholder with ONE "load" button
            # The load callback replaces this with the full interactive card
            results.append(InlineQueryResultArticle(
                id=f"m_{g['group_id']}_{m['id']}",
                title=f"#{m['id']} {m['question'][:55]}",
                description=f"Tap to post market card with bet buttons",
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

async def _show_market(q, market: dict, group_id: int, extra: str = ""):
    """Edit message to full market card + action buttons."""
    text = fmt.market_card(market)
    if extra:
        text += f"\n\n{extra}"
    await q.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_market(market["id"], group_id),
    )


# ── Load / Refresh  (load_<market_id>_<group_id>) ────────────────────────────

async def cb_load(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    await q.answer()
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[1])
    market    = logic.get_market(market_id, group_id)
    if not market:
        await q.answer("Market not found.", show_alert=True); return
    await _show_market(q, market, group_id)


# ── Bet: pick side  (side_<YES|NO>_<market_id>_<group_id>) ──────────────────

async def cb_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")          # side YES 7 -1001234
    side, market_id = parts[1], int(parts[2])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join in this group first!", show_alert=True); return

    market = logic.get_market(market_id, group_id)
    if not market or market["status"] != "open":
        await q.answer("This market is no longer open.", show_alert=True); return

    mem         = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    emoji       = "✅" if side == "YES" else "❌"

    await q.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"{emoji} *Betting {side}* at *{price:.0%}*\n"
        f"Your balance: *{mem['balance']:.1f} pts*\n\n"
        f"How many points?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_amounts(side, market_id, group_id),
    )


# ── Bet: pick amount  (amt_<YES|NO>_<market_id>_<pts>_<group_id>) ───────────

async def cb_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")          # amt YES 7 100 -1001234
    side, market_id, points = parts[1], int(parts[2]), float(parts[3])

    try:
        market, shares = logic.place_bet(user["telegram_id"], group_id, market_id, side, points)
    except logic.BetError as e:
        await q.answer(str(e), show_alert=True); return

    mem         = logic.get_membership(user["telegram_id"], group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    emoji       = "✅" if side == "YES" else "❌"

    await _show_market(q, market, group_id,
        f"{emoji} *{fmt._safe(user['username'])}* bet *{side}* "
        f"\\- {shares:.2f} shares for {points:.0f} pts \\@ {price:.0%}\n"
        f"Balance: *{mem['balance']:.1f} pts*"
    )


# ── My position  (mypos_<market_id>_<group_id>) ──────────────────────────────

async def cb_mypos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    user      = _uinfo(q)
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[1])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join first!", show_alert=True); return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_here  = [p for p in positions if p["market_id"] == market_id]

    if not pos_here:
        await q.answer("You have no position in this market yet.", show_alert=True); return

    text = "\n\n".join(fmt.position_text(p) for p in pos_here)
    await q.answer(text[:200], show_alert=True)


# ── Sell: pick side  (sell_pick_<market_id>_<group_id>) ─────────────────────

async def cb_sell_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q         = update.callback_query
    await q.answer()
    user      = _uinfo(q)
    group_id  = _gid(q.data)
    market_id = int(q.data.split("_")[2])

    if not logic.is_member(user["telegram_id"], group_id):
        await q.answer("Use /join first!", show_alert=True); return

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos_map   = {p["side"]: p for p in positions if p["market_id"] == market_id}

    if not pos_map:
        await q.answer("You have no position in this market.", show_alert=True); return

    market = logic.get_market(market_id, group_id)
    await q.edit_message_text(
        f"{fmt.market_card(market)}\n\n*Which side do you want to sell?*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_sell_sides(market_id, group_id, "YES" in pos_map, "NO" in pos_map),
    )


# ── Sell: pick amount  (sellside_<YES|NO>_<market_id>_<group_id>) ───────────

async def cb_sell_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")          # sellside YES 7 -1001234
    side, market_id = parts[1], int(parts[2])

    positions = logic.get_user_positions(user["telegram_id"], group_id)
    pos       = next((p for p in positions if p["market_id"] == market_id and p["side"] == side), None)

    if not pos or pos["net_shares"] <= 0:
        await q.answer("No shares to sell.", show_alert=True); return

    market      = logic.get_market(market_id, group_id)
    yes_p, no_p = logic.get_odds(market)
    price       = yes_p if side == "YES" else no_p
    value       = pos["net_shares"] * price

    await q.edit_message_text(
        f"{fmt.market_card(market)}\n\n"
        f"*Selling {side} shares*\n"
        f"You hold: {pos['net_shares']:.3f} shares \\= {value:.1f} pts at {price:.0%}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=fmt.kb_sell_amounts(side, market_id, group_id, pos["net_shares"]),
    )


# ── Sell: execute  (sellamt_<YES|NO>_<market_id>_<shares>_<group_id>) ───────

async def cb_sell_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = _uinfo(q)
    group_id = _gid(q.data)
    parts    = q.data.split("_")          # sellamt YES 7 12.345 -1001234
    side, market_id, shares = parts[1], int(parts[2]), float(parts[3])

    try:
        market, payout = logic.sell_position(user["telegram_id"], group_id, market_id, side, shares)
    except logic.SellError as e:
        await q.answer(str(e), show_alert=True); return

    mem = logic.get_membership(user["telegram_id"], group_id)
    await _show_market(q, market, group_id,
        f"*{fmt._safe(user['username'])}* sold {shares} *{side}* shares \\+{payout:.1f} pts\n"
        f"Balance: *{mem['balance']:.1f} pts*"
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
    for m in [dict(r) for r in markets]:
        if datetime.fromisoformat(m["deadline"]) < now:
            logic.close_market(m["id"])
            logger.info(f"Auto-closed market #{m['id']}")
            await _notify_admins(ctx.application, m["group_id"],
                f"Market *#{m['id']}* has closed\\.\n_{fmt._safe(m['question'])}_\n\n"
                f"Resolve with: /resolve {m['id']} YES or NO"
            )

async def job_weekly_refill(ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        groups = conn.execute("SELECT group_id FROM groups").fetchall()
    for g in groups:
        affected = logic.do_weekly_refill(g["group_id"])
        if affected:
            try:
                await ctx.bot.send_message(
                    g["group_id"],
                    fmt.refill_text(affected),
                )
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# App wiring
# ─────────────────────────────────────────────────────────────────────────────

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # Propose conversation (group only)
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
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("join",        cmd_join))
    app.add_handler(CommandHandler("addadmin",    cmd_addadmin))
    app.add_handler(CommandHandler("approve",     cmd_approve))
    app.add_handler(CommandHandler("reject",      cmd_reject))
    app.add_handler(CommandHandler("resolve",     cmd_resolve))
    app.add_handler(CommandHandler("pending",     cmd_pending))
    app.add_handler(CommandHandler("givepoints",  cmd_givepoints))
    app.add_handler(propose_conv)

    # Inline query
    app.add_handler(InlineQueryHandler(inline_query))

    # Callbacks — patterns match data format exactly
    app.add_handler(CallbackQueryHandler(cb_load,        pattern=r"^load_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_side,        pattern=r"^side_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_amount,      pattern=r"^amt_(YES|NO)_\d+_[\d.]+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mypos,       pattern=r"^mypos_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_pick,   pattern=r"^sell_pick_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_side,   pattern=r"^sellside_(YES|NO)_\d+_-?\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sell_amount, pattern=r"^sellamt_(YES|NO)_\d+_[\d.]+_-?\d+$"))

    # Scheduler
    app.job_queue.run_repeating(job_close_expired, interval=60,     first=15)
    app.job_queue.run_repeating(job_weekly_refill, interval=604800, first=30)

    logger.info("PolyFriends v4 started!")

    import sys
    if sys.version_info >= (3, 12):
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
