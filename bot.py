import logging
import os
from datetime import datetime, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    InlineQueryHandler, ConversationHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ParseMode

import logic
import formatting
from database import init_db

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]

# Conversation states
PROPOSE_QUESTION, PROPOSE_DEADLINE = range(2)
SELL_MARKET, SELL_SIDE, SELL_SHARES = range(3)


# ── Helpers ───────────────────────────────────────────────────────────────────

def group_only(func):
    """Decorator: reject command if used outside a group."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == "private":
            await update.message.reply_text(
                "⚠️ This command only works inside a group chat."
            )
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

async def register(update: Update) -> dict:
    u = update.effective_user
    return logic.get_or_create_user(u.id, u.username or u.first_name)

async def ensure_group(update: Update) -> dict:
    chat = update.effective_chat
    return logic.get_or_create_group(chat.id, chat.title or str(chat.id))

async def require_member(update: Update) -> dict | None:
    """Returns membership dict or sends error and returns None."""
    user  = update.effective_user
    chat  = update.effective_chat
    m = logic.get_membership(user.id, chat.id)
    if not m:
        await update.message.reply_text(
            "⚠️ You haven't joined this group yet. Use /join to play!"
        )
    return m

async def admin_only(update: Update) -> bool:
    if not logic.is_admin(update.effective_user.id, update.effective_chat.id):
        await update.message.reply_text("⛔ Admin only.")
        return False
    return True

async def notify_admins(app, group_id: int, text: str):
    for uid in logic.get_admin_ids(group_id):
        try:
            await app.bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

async def send(update: Update, text: str, **kwargs):
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **kwargs)


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    await send(update,
        "👋 *Welcome to PolyFriends!*\n\n"
        "A prediction market for your group.\n\n"
        "Add me to a group, then use */join* in that group to get your *1000 starting points*.\n\n"
        "*/help* — full command list"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update,
        "*PolyFriends Commands*\n\n"
        "*Player:*\n"
        "/join — Join the prediction market in this group\n"
        "/balance — Your points balance\n"
        "/markets — Open markets\n"
        "/market <id> — Market detail + bet buttons\n"
        "/bet <id> YES|NO <pts> — Place a bet\n"
        "/sell <id> YES|NO <shares> — Sell shares back\n"
        "/positions — Your open positions\n"
        "/leaderboard — Group rankings\n"
        "/propose — Propose a new market\n\n"
        "*Admin:*\n"
        "/pending — Markets awaiting approval\n"
        "/approve <id> — Open a market\n"
        "/reject <id> — Reject a market\n"
        "/resolve <id> YES|NO|CANCELLED — Settle a market\n"
        "/addadmin — Make yourself admin (first run only)\n"
        "/givepoints @user <amount> — Grant points\n\n"
        "*Inline:*\n"
        "Type `@YourBotName` in any chat to see your positions!"
    )


# ── /join ─────────────────────────────────────────────────────────────────────

@group_only
async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = await register(update)
    group = await ensure_group(update)

    newly_joined = logic.join_group(user["telegram_id"], group["group_id"])

    if newly_joined:
        await send(update,
            f"🎉 Welcome to *{group['name']}*, *{user['username']}*!\n"
            f"You've been given *1000 points* to start betting.\n\n"
            f"Check open markets with /markets"
        )
    else:
        m = logic.get_membership(user["telegram_id"], group["group_id"])
        await send(update,
            f"You're already a member of *{group['name']}*.\n"
            f"Balance: *{m['balance']:.1f} pts*"
        )


# ── /balance ──────────────────────────────────────────────────────────────────

@group_only
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    await ensure_group(update)
    m = await require_member(update)
    if not m:
        return
    await send(update, f"💰 *{m['username']}* — *{m['balance']:.1f} pts*")


# ── /markets ──────────────────────────────────────────────────────────────────

@group_only
async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    group = await ensure_group(update)
    markets = logic.get_markets(group["group_id"], "open")

    if not markets:
        await send(update, "No open markets right now. Propose one with /propose!")
        return

    lines = [f"🟢 *Open Markets* ({len(markets)})\n"]
    for m in markets:
        lines.append(formatting.fmt_market(m))
        lines.append(f"👉 /market {m['id']}\n")

    await send(update, "\n".join(lines))


# ── /market <id> ──────────────────────────────────────────────────────────────

@group_only
async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    group = await ensure_group(update)

    if not context.args:
        await send(update, "Usage: /market <id>")
        return
    try:
        market_id = int(context.args[0])
    except ValueError:
        await send(update, "Market ID must be a number.")
        return

    market = logic.get_market(market_id, group["group_id"])
    if not market:
        await send(update, "Market not found.")
        return

    text = formatting.fmt_market(market)

    if market["status"] == "open":
        keyboard = [[
            InlineKeyboardButton("✅ YES", callback_data=f"bet_YES_{market_id}"),
            InlineKeyboardButton("❌ NO",  callback_data=f"bet_NO_{market_id}"),
        ]]
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await send(update, text)


# ── /bet ──────────────────────────────────────────────────────────────────────

@group_only
async def cmd_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    group = await ensure_group(update)
    m = await require_member(update)
    if not m:
        return

    args = context.args
    if len(args) != 3:
        await send(update, "Usage: /bet <market\\_id> YES|NO <points>\nExample: /bet 3 YES 100")
        return

    try:
        market_id = int(args[0])
        side      = args[1].upper()
        points    = float(args[2])
    except ValueError:
        await send(update, "Invalid format. Example: /bet 3 YES 100")
        return

    try:
        market, shares = logic.place_bet(
            update.effective_user.id, group["group_id"], market_id, side, points
        )
    except logic.BetError as e:
        await send(update, f"❌ {e}")
        return

    yes_p, no_p = logic.get_odds(market)
    price = yes_p if side == "YES" else no_p
    await send(update,
        f"✅ *Bet placed!*\n\n"
        f"{formatting.fmt_market(market)}\n\n"
        f"You bought *{shares:.3f} {side} shares* for *{points:.1f} pts*\n"
        f"Price: {price:.2%}"
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

@group_only
async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /sell <market_id> YES|NO <shares>"""
    await register(update)
    group = await ensure_group(update)
    m = await require_member(update)
    if not m:
        return

    args = context.args
    if len(args) != 3:
        await send(update, "Usage: /sell <market\\_id> YES|NO <shares>\nExample: /sell 3 YES 5.5")
        return

    try:
        market_id     = int(args[0])
        side          = args[1].upper()
        shares_to_sell = float(args[2])
    except ValueError:
        await send(update, "Invalid format. Example: /sell 3 YES 5.5")
        return

    try:
        market, payout = logic.sell_position(
            update.effective_user.id, group["group_id"], market_id, side, shares_to_sell
        )
    except logic.SellError as e:
        await send(update, f"❌ {e}")
        return

    updated_m = logic.get_membership(update.effective_user.id, group["group_id"])
    await send(update,
        f"💸 *Sold!*\n\n"
        f"{formatting.fmt_market(market)}\n\n"
        f"Sold *{shares_to_sell:.3f} {side} shares* → received *{payout:.1f} pts*\n"
        f"New balance: *{updated_m['balance']:.1f} pts*"
    )


# ── /positions ────────────────────────────────────────────────────────────────

@group_only
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    group = await ensure_group(update)
    m = await require_member(update)
    if not m:
        return

    positions = logic.get_user_positions(update.effective_user.id, group["group_id"])
    if not positions:
        await send(update, "You have no open positions. Use /markets to find something to bet on!")
        return

    open_pos   = [p for p in positions if p["status"] == "open"]
    closed_pos = [p for p in positions if p["status"] != "open"]

    lines = [f"📊 *Your Positions in {group['name']}*\n"]
    if open_pos:
        lines.append("*Open:*")
        for p in open_pos:
            lines.append(formatting.fmt_position(p))
            lines.append(f"  Sell: /sell {p['market_id']} {p['side']} <shares>\n")
    if closed_pos:
        lines.append("*Closed / Resolved:*")
        for p in closed_pos[:5]:
            lines.append(formatting.fmt_position(p))
            lines.append("")

    await send(update, "\n".join(lines))


# ── /leaderboard ──────────────────────────────────────────────────────────────

@group_only
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    group = await ensure_group(update)
    rows = logic.get_leaderboard(group["group_id"])
    await send(update, formatting.fmt_leaderboard(rows, group["name"]))


# ── /propose (conversation) ───────────────────────────────────────────────────

@group_only
async def cmd_propose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register(update)
    await ensure_group(update)
    m = await require_member(update)
    if not m:
        return ConversationHandler.END

    context.user_data["propose_group_id"]   = update.effective_chat.id
    context.user_data["propose_group_name"] = update.effective_chat.title

    await send(update,
        "📝 *Propose a Market*\n\n"
        "Write a clear YES/NO question.\n"
        "_Example: Will it rain in London on Friday?_\n\n"
        "/cancel to abort."
    )
    return PROPOSE_QUESTION

async def propose_got_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["question"] = update.message.text
    await send(update,
        "⏰ *When should betting close?*\n\n"
        "Use `7d`, `24h` — or full datetime `2025-06-01 20:00` (UTC)."
    )
    return PROPOSE_DEADLINE

async def propose_got_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = await register(update)
    text  = update.message.text.strip()

    deadline = _parse_deadline(text)
    if not deadline:
        await send(update, "❌ Couldn't parse that. Try `7d`, `48h`, or `2025-06-01 20:00`.")
        return PROPOSE_DEADLINE

    question = context.user_data["question"]
    group_id = context.user_data["propose_group_id"]

    market_id = logic.propose_market(question, user["telegram_id"], group_id, deadline)

    await send(update,
        f"✅ Market *#{market_id}* proposed!\n\n"
        f"_{question}_\n"
        f"Deadline: {deadline} UTC\n\n"
        f"Waiting for admin approval…"
    )

    await notify_admins(context.application, group_id,
        f"📢 *New market proposal* from @{user['username']}\n\n"
        f"*#{market_id}:* {question}\n"
        f"Deadline: {deadline} UTC\n\n"
        f"/approve {market_id} | /reject {market_id}"
    )
    return ConversationHandler.END

async def propose_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(update, "❌ Proposal cancelled.")
    return ConversationHandler.END

def _parse_deadline(text: str) -> str | None:
    try:
        if text.endswith("d"):
            return (datetime.utcnow() + timedelta(days=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        if text.endswith("h"):
            return (datetime.utcnow() + timedelta(hours=int(text[:-1]))).strftime("%Y-%m-%d %H:%M")
        return datetime.strptime(text, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


# ── Admin Commands ────────────────────────────────────────────────────────────

@group_only
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    group   = await ensure_group(update)
    markets = logic.get_markets(group["group_id"], "pending")

    if not markets:
        await send(update, "No pending markets.")
        return

    lines = [f"⏳ *Pending Markets* ({len(markets)})\n"]
    for m in markets:
        lines.append(formatting.fmt_market(m))
        lines.append(f"/approve {m['id']} | /reject {m['id']}\n")
    await send(update, "\n".join(lines))

@group_only
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    group = await ensure_group(update)
    if not context.args:
        await send(update, "Usage: /approve <id>")
        return

    market_id = int(context.args[0])
    logic.approve_market(market_id, update.effective_user.id, group["group_id"])
    market = logic.get_market(market_id, group["group_id"])
    await send(update, f"✅ Market approved!\n\n{formatting.fmt_market(market)}")

@group_only
async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    group = await ensure_group(update)
    if not context.args:
        await send(update, "Usage: /reject <id>")
        return

    market_id = int(context.args[0])
    logic.reject_market(market_id, update.effective_user.id, group["group_id"])
    await send(update, f"❌ Market #{market_id} rejected.")

@group_only
async def cmd_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    group = await ensure_group(update)
    args  = context.args
    if len(args) < 2:
        await send(update, "Usage: /resolve <id> YES|NO|CANCELLED")
        return

    market_id  = int(args[0])
    resolution = args[1].upper()

    try:
        logic.resolve_market(market_id, group["group_id"], resolution, update.effective_user.id)
    except ValueError as e:
        await send(update, f"❌ {e}")
        return

    market = logic.get_market(market_id, group["group_id"])
    await send(update,
        f"🏁 *Market #{market_id} resolved: {resolution}*\n\n"
        f"{formatting.fmt_market(market)}"
    )

async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group    = await ensure_group(update)
    group_id = group["group_id"]
    caller   = update.effective_user.id

    with logic.get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM admins WHERE group_id=?", (group_id,)
        ).fetchone()[0]

    if count > 0 and not logic.is_admin(caller, group_id):
        await send(update, "⛔ Only existing admins can add admins.")
        return

    logic.add_admin(caller, group_id)
    await send(update, "✅ You are now an admin in this group.")

@group_only
async def cmd_givepoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    group = await ensure_group(update)
    args  = context.args

    if len(args) < 2:
        await send(update, "Usage: /givepoints @username <amount>")
        return

    try:
        amount = float(args[-1])
    except ValueError:
        await send(update, "Amount must be a number.")
        return

    # Resolve @username mention
    target_username = args[0].lstrip("@")
    with logic.get_db() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE username=?", (target_username,)
        ).fetchone()

    if not target:
        await send(update, f"User @{target_username} not found.")
        return

    logic.give_points(target["telegram_id"], group["group_id"], amount)
    await send(update, f"✅ Gave *{amount:.0f} pts* to @{target_username}.")


# ── Inline Button Callbacks ───────────────────────────────────────────────────

async def callback_bet_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fired when user taps YES or NO button on a market."""
    query     = update.callback_query
    await query.answer()

    _, side, market_id_str = query.data.split("_")
    market_id = int(market_id_str)
    group_id  = query.message.chat.id

    user = logic.get_or_create_user(
        query.from_user.id,
        query.from_user.username or query.from_user.first_name
    )
    if not logic.is_member(user["telegram_id"], group_id):
        await query.answer("Use /join first to become a player!", show_alert=True)
        return

    market = logic.get_market(market_id, group_id)
    if not market or market["status"] != "open":
        await query.edit_message_text("Market is no longer open.")
        return

    yes_p, no_p = logic.get_odds(market)
    price = yes_p if side == "YES" else no_p
    m     = logic.get_membership(user["telegram_id"], group_id)

    keyboard = [
        [
            InlineKeyboardButton("50",  callback_data=f"amt_{side}_{market_id}_50"),
            InlineKeyboardButton("100", callback_data=f"amt_{side}_{market_id}_100"),
            InlineKeyboardButton("200", callback_data=f"amt_{side}_{market_id}_200"),
            InlineKeyboardButton("500", callback_data=f"amt_{side}_{market_id}_500"),
        ],
        [InlineKeyboardButton("« Back", callback_data=f"back_{market_id}")]
    ]

    emoji = "✅" if side == "YES" else "❌"
    await query.edit_message_text(
        f"{emoji} Betting *{side}* on:\n_{market['question']}_\n\n"
        f"Current price: *{price:.2%}*\n"
        f"Your balance: *{m['balance']:.1f} pts*\n\n"
        f"How many points?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def callback_bet_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts     = query.data.split("_")   # amt_YES_3_100
    side      = parts[1]
    market_id = int(parts[2])
    points    = float(parts[3])
    group_id  = query.message.chat.id

    user = logic.get_or_create_user(
        query.from_user.id,
        query.from_user.username or query.from_user.first_name
    )

    try:
        market, shares = logic.place_bet(
            user["telegram_id"], group_id, market_id, side, points
        )
    except logic.BetError as e:
        await query.answer(str(e), show_alert=True)
        return

    yes_p, no_p = logic.get_odds(market)
    price = yes_p if side == "YES" else no_p
    updated_m = logic.get_membership(user["telegram_id"], group_id)

    await query.edit_message_text(
        f"✅ *Bet placed!*\n\n"
        f"{formatting.fmt_market(market)}\n\n"
        f"Bought *{shares:.3f} {side} shares* for *{points:.1f} pts*\n"
        f"Price: {price:.2%} | Balance: *{updated_m['balance']:.1f} pts*",
        parse_mode=ParseMode.MARKDOWN
    )

async def callback_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    market_id = int(query.data.split("_")[1])
    group_id  = query.message.chat.id
    market    = logic.get_market(market_id, group_id)

    keyboard = [[
        InlineKeyboardButton("✅ YES", callback_data=f"bet_YES_{market_id}"),
        InlineKeyboardButton("❌ NO",  callback_data=f"bet_NO_{market_id}"),
    ]]
    await query.edit_message_text(
        formatting.fmt_market(market),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Inline Query (type @botname anywhere) ─────────────────────────────────────

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.strip().lower()
    user_id    = update.inline_query.from_user.id
    results    = []

    # Gather all positions across all groups this user has joined
    with logic.get_db() as conn:
        groups = conn.execute(
            """SELECT g.group_id, g.name, m.balance
               FROM memberships m
               JOIN groups g ON g.group_id = m.group_id
               WHERE m.user_id = ?""",
            (user_id,)
        ).fetchall()

    for g in groups:
        group_id   = g["group_id"]
        group_name = g["name"]
        balance    = g["balance"]

        # Open markets in this group (filtered by query if provided)
        markets = logic.get_markets(group_id, "open")
        for m in markets:
            if query_text and query_text not in m["question"].lower():
                continue

            yes_p, no_p = logic.get_odds(m)
            text = (
                f"📊 *{m['question']}*\n"
                f"Group: {group_name}\n"
                f"YES {yes_p:.0%} — NO {no_p:.0%}\n"
                f"Use /market {m['id']} in the group to bet."
            )
            results.append(
                InlineQueryResultArticle(
                    id=f"market_{group_id}_{m['id']}",
                    title=m["question"][:60],
                    description=f"{group_name} | YES {yes_p:.0%} / NO {no_p:.0%}",
                    input_message_content=InputTextMessageContent(
                        text, parse_mode=ParseMode.MARKDOWN
                    ),
                )
            )

    # If no query, also show a summary card per group
    if not query_text:
        for g in groups:
            results.insert(0, InlineQueryResultArticle(
                id=f"balance_{g['group_id']}",
                title=f"💰 {g['name']}",
                description=f"Your balance: {g['balance']:.1f} pts",
                input_message_content=InputTextMessageContent(
                    f"💰 Balance in *{g['name']}*: *{g['balance']:.1f} pts*",
                    parse_mode=ParseMode.MARKDOWN
                ),
            ))

    await update.inline_query.answer(results[:50], cache_time=10)


# ── Scheduler Jobs ────────────────────────────────────────────────────────────

async def job_close_expired_markets(context: ContextTypes.DEFAULT_TYPE):
    with logic.get_db() as conn:
        open_markets = conn.execute(
            "SELECT * FROM markets WHERE status='open' AND deadline IS NOT NULL"
        ).fetchall()

    now = datetime.utcnow()
    for m in open_markets:
        if datetime.fromisoformat(m["deadline"]) < now:
            logic.close_market(m["id"])
            logger.info(f"Auto-closed market #{m['id']}")
            await notify_admins(
                context.application, m["group_id"],
                f"⏰ Market *#{m['id']}* closed (deadline reached).\n"
                f"_{m['question']}_\n\n"
                f"Resolve: /resolve {m['id']} YES | NO"
            )

async def job_weekly_refill(context: ContextTypes.DEFAULT_TYPE):
    with logic.get_db() as conn:
        groups = conn.execute("SELECT group_id FROM groups").fetchall()

    for g in groups:
        group_id = g["group_id"]
        affected = logic.do_weekly_refill(group_id)
        if affected:
            try:
                await context.bot.send_message(
                    group_id,
                    formatting.fmt_refill_announce(affected),
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    propose_conv = ConversationHandler(
        entry_points=[CommandHandler("propose", cmd_propose)],
        states={
            PROPOSE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, propose_got_question)],
            PROPOSE_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, propose_got_deadline)],
        },
        fallbacks=[CommandHandler("cancel", propose_cancel)],
    )

    # User commands
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("join",        cmd_join))
    app.add_handler(CommandHandler("balance",     cmd_balance))
    app.add_handler(CommandHandler("markets",     cmd_markets))
    app.add_handler(CommandHandler("market",      cmd_market))
    app.add_handler(CommandHandler("bet",         cmd_bet))
    app.add_handler(CommandHandler("sell",        cmd_sell))
    app.add_handler(CommandHandler("positions",   cmd_positions))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(propose_conv)

    # Admin commands
    app.add_handler(CommandHandler("pending",    cmd_pending))
    app.add_handler(CommandHandler("approve",    cmd_approve))
    app.add_handler(CommandHandler("reject",     cmd_reject))
    app.add_handler(CommandHandler("resolve",    cmd_resolve))
    app.add_handler(CommandHandler("addadmin",   cmd_addadmin))
    app.add_handler(CommandHandler("givepoints", cmd_givepoints))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(callback_bet_side,   pattern=r"^bet_(YES|NO)_\d+$"))
    app.add_handler(CallbackQueryHandler(callback_bet_amount, pattern=r"^amt_"))
    app.add_handler(CallbackQueryHandler(callback_back,       pattern=r"^back_\d+$"))

    # Inline query
    app.add_handler(InlineQueryHandler(inline_query))

    # Scheduler
    app.job_queue.run_repeating(job_close_expired_markets, interval=60,   first=15)
    app.job_queue.run_repeating(job_weekly_refill,         interval=604800, first=10)  # every 7 days

    logger.info("🚀 PolyFriends started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
