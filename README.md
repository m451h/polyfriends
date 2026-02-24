# 🎯 PolyFriends v2

Prediction market bot for Telegram friend groups — everything happens through inline market cards.

---

## How it works for players

1. Someone adds the bot to the group
2. Admin runs `/addadmin`, then `/approve` markets
3. Players run `/join` in the group
4. Type `@YourBot` in the group → pick a market → card appears in chat
5. Tap **Bet YES / Bet NO** → pick amount → done
6. Tap **Sell** → pick side → pick amount → done
7. Everything is visible to the whole group

That's the entire player experience. No slash commands needed after `/join`.

---

## Setup

```bash
# 1. Get a token from @BotFather
# 2. Enable inline mode: /setinline in BotFather → set a placeholder like "Search markets..."

pip install -r requirements.txt
export BOT_TOKEN="your_token_here"
python bot.py
```

Then in your group:
- `/addadmin` — make yourself admin (first run only, no auth needed)
- Tell friends to `/join`
- `/propose` a market → admin `/approve`s it
- Type `@YourBot` to start betting

---

## Slash commands (minimal)

| Command | Who | Description |
|---|---|---|
| `/join` | Players | Join this group's market (get 1000 pts) |
| `/propose` | Players | Propose a new market |
| `/addadmin` | First admin | Bootstrap admin (works once when no admins exist) |
| `/approve <id>` | Admin | Open a market for betting |
| `/reject <id>` | Admin | Reject a proposed market |
| `/resolve <id> YES\|NO\|CANCELLED` | Admin | Settle a market + pay winners |
| `/pending` | Admin | List markets awaiting approval |
| `/givepoints @user <amount>` | Admin | Give points to a player |

## Inline UI (the main interface)

Type `@YourBot` in any group to get:
- **💰 My Balances** — your balance across all groups
- **🏆 Leaderboard** — per group
- **Market cards** — one per open market, searchable by name or ID

Each market card has buttons:
- **✅ Bet YES / ❌ Bet NO** → amount picker (50 / 100 / 200 / 500 pts)
- **💸 Sell** → side picker → 25% / 50% / All
- **📊 My position** → private popup showing your shares + estimated value
- **🔄 Refresh odds** → updates the card with latest pool sizes

---

## Economics

- Everyone starts with **1000 pts** per group
- **Weekly refill**: anyone below 200 pts gets topped to 200 pts (winners unaffected)
- **Selling**: exit at `shares × current price` — partial sells supported
- **Payout**: winners split the entire pool proportional to shares held
- **Cancelled markets**: full refund of points spent

---

## Files

```
bot.py          — All handlers (slash + inline + callbacks) + scheduler
logic.py        — Business logic (unchanged from v1)
database.py     — SQLite schema (unchanged from v1)
formatting.py   — Cards, keyboards, display helpers
requirements.txt
```
