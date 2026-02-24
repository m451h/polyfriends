from __future__ import annotations
from datetime import datetime, timedelta
from database import get_db

SEED        = 10.0    # points seeded into each pool at market open
STARTING_PTS = 1000.0
REFILL_FLOOR = 200.0  # weekly refill brings anyone below this back up to it


# ── Exceptions ────────────────────────────────────────────────────────────────

class BetError(Exception):  pass
class SellError(Exception): pass


# ── Groups ────────────────────────────────────────────────────────────────────

def get_or_create_group(group_id: int, name: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM groups WHERE group_id = ?", (group_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO groups (group_id, name) VALUES (?, ?)", (group_id, name)
            )
            row = conn.execute(
                "SELECT * FROM groups WHERE group_id = ?", (group_id,)
            ).fetchone()
    return dict(row)

def get_group(group_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM groups WHERE group_id = ?", (group_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Users & Memberships ───────────────────────────────────────────────────────

def get_or_create_user(telegram_id: int, username: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (telegram_id, username) VALUES (?, ?)",
                (telegram_id, username)
            )
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
    return dict(row)

def get_user(telegram_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return dict(row) if row else None

def join_group(user_id: int, group_id: int) -> bool:
    """Returns True if newly joined, False if already a member."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM memberships WHERE group_id=? AND user_id=?",
            (group_id, user_id)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO memberships (group_id, user_id) VALUES (?, ?)",
            (group_id, user_id)
        )
    return True

def is_member(user_id: int, group_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM memberships WHERE group_id=? AND user_id=?",
            (group_id, user_id)
        ).fetchone()
    return row is not None

def get_membership(user_id: int, group_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT m.*, u.username FROM memberships m
               JOIN users u ON u.telegram_id = m.user_id
               WHERE m.group_id=? AND m.user_id=?""",
            (group_id, user_id)
        ).fetchone()
    return dict(row) if row else None

def get_leaderboard(group_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT u.username, m.balance, m.user_id
               FROM memberships m
               JOIN users u ON u.telegram_id = m.user_id
               WHERE m.group_id = ?
               ORDER BY m.balance DESC LIMIT 20""",
            (group_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Admins ────────────────────────────────────────────────────────────────────

def is_admin(user_id: int, group_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE group_id=? AND user_id=?",
            (group_id, user_id)
        ).fetchone()
    return row is not None

def add_admin(user_id: int, group_id: int):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (group_id, user_id) VALUES (?, ?)",
            (group_id, user_id)
        )

def get_admin_ids(group_id: int) -> list[int]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM admins WHERE group_id=?", (group_id,)
        ).fetchall()
    return [r["user_id"] for r in rows]


# ── Odds ──────────────────────────────────────────────────────────────────────

def get_odds(market: dict) -> tuple[float, float]:
    total = market["yes_pool"] + market["no_pool"]
    return market["yes_pool"] / total, market["no_pool"] / total

def price_for_side(side: str, market: dict) -> float:
    yes_p, no_p = get_odds(market)
    return yes_p if side == "YES" else no_p

def shares_for_points(points: float, side: str, market: dict) -> float:
    return points / price_for_side(side, market)

def points_for_shares(shares: float, side: str, market: dict) -> float:
    """Current market value of `shares` on `side`."""
    return shares * price_for_side(side, market)


# ── Markets ───────────────────────────────────────────────────────────────────

def propose_market(question: str, created_by: int, group_id: int, deadline: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO markets (group_id, question, created_by, deadline) VALUES (?,?,?,?)",
            (group_id, question, created_by, deadline)
        )
    return cur.lastrowid

def approve_market(market_id: int, admin_id: int, group_id: int):
    with get_db() as conn:
        conn.execute(
            """UPDATE markets SET status='open', approved_by=?
               WHERE id=? AND group_id=? AND status='pending'""",
            (admin_id, market_id, group_id)
        )

def reject_market(market_id: int, admin_id: int, group_id: int):
    with get_db() as conn:
        conn.execute(
            """UPDATE markets SET status='rejected', approved_by=?
               WHERE id=? AND group_id=? AND status='pending'""",
            (admin_id, market_id, group_id)
        )

def get_market(market_id: int, group_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM markets WHERE id=? AND group_id=?",
            (market_id, group_id)
        ).fetchone()
    return dict(row) if row else None

def get_markets(group_id: int, status: str = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM markets WHERE group_id=? AND status=? ORDER BY id DESC",
                (group_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM markets WHERE group_id=? ORDER BY id DESC",
                (group_id,)
            ).fetchall()
    return [dict(r) for r in rows]

def close_market(market_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE markets SET status='closed' WHERE id=? AND status='open'",
            (market_id,)
        )


# ── Betting ───────────────────────────────────────────────────────────────────

def place_bet(user_id: int, group_id: int, market_id: int, side: str, points: float):
    if points <= 0:
        raise BetError("Bet must be greater than 0 points.")
    if side not in ("YES", "NO"):
        raise BetError("Side must be YES or NO.")

    with get_db() as conn:
        market = conn.execute(
            "SELECT * FROM markets WHERE id=? AND group_id=?", (market_id, group_id)
        ).fetchone()
        if not market:
            raise BetError("Market not found in this group.")
        market = dict(market)

        if market["status"] != "open":
            raise BetError(f"Market is not open (status: {market['status']}).")

        if market["deadline"]:
            if datetime.utcnow() > datetime.fromisoformat(market["deadline"]):
                raise BetError("Betting deadline has passed.")

        membership = conn.execute(
            "SELECT * FROM memberships WHERE group_id=? AND user_id=?",
            (group_id, user_id)
        ).fetchone()
        if not membership:
            raise BetError("You haven't joined this group. Use /join first.")

        if membership["balance"] < points:
            raise BetError(
                f"Not enough points. You have {membership['balance']:.1f} pts."
            )

        shares = shares_for_points(points, side, market)

        if side == "YES":
            conn.execute(
                "UPDATE markets SET yes_pool=yes_pool+?, yes_shares=yes_shares+? WHERE id=?",
                (points, shares, market_id)
            )
        else:
            conn.execute(
                "UPDATE markets SET no_pool=no_pool+?, no_shares=no_shares+? WHERE id=?",
                (points, shares, market_id)
            )

        conn.execute(
            "UPDATE memberships SET balance=balance-? WHERE group_id=? AND user_id=?",
            (points, group_id, user_id)
        )

        conn.execute(
            "INSERT INTO positions (user_id, market_id, group_id, side, shares, points_spent) VALUES (?,?,?,?,?,?)",
            (user_id, market_id, group_id, side, shares, points)
        )

    return get_market(market_id, group_id), shares


# ── Selling ───────────────────────────────────────────────────────────────────

def get_net_position(user_id: int, market_id: int, side: str) -> tuple[float, float]:
    """Returns (net_shares, avg_cost_per_share) across all buy/sell events."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT shares, points_spent FROM positions
               WHERE user_id=? AND market_id=? AND side=?""",
            (user_id, market_id, side)
        ).fetchall()
    total_shares = sum(r["shares"] for r in rows)
    total_spent  = sum(r["points_spent"] for r in rows)
    return total_shares, total_spent

def sell_position(user_id: int, group_id: int, market_id: int, side: str, shares_to_sell: float):
    if shares_to_sell <= 0:
        raise SellError("Shares to sell must be greater than 0.")

    with get_db() as conn:
        market = conn.execute(
            "SELECT * FROM markets WHERE id=? AND group_id=?", (market_id, group_id)
        ).fetchone()
        if not market:
            raise SellError("Market not found.")
        market = dict(market)

        if market["status"] != "open":
            raise SellError("You can only sell positions in open markets.")

        if market["deadline"]:
            if datetime.utcnow() > datetime.fromisoformat(market["deadline"]):
                raise SellError("Market deadline has passed — cannot sell.")

        # Sum up all position rows for this user/market/side
        rows = conn.execute(
            "SELECT shares FROM positions WHERE user_id=? AND market_id=? AND side=?",
            (user_id, market_id, side)
        ).fetchall()
        net_shares = sum(r["shares"] for r in rows)

        if net_shares <= 0:
            raise SellError(f"You have no {side} shares in this market.")
        if shares_to_sell > net_shares:
            raise SellError(
                f"You only have {net_shares:.2f} {side} shares. Cannot sell {shares_to_sell:.2f}."
            )

        # Payout = shares × current side price (shrinks the pool)
        payout = points_for_shares(shares_to_sell, side, market)

        # Remove shares + points from the pool
        if side == "YES":
            conn.execute(
                "UPDATE markets SET yes_pool=yes_pool-?, yes_shares=yes_shares-? WHERE id=?",
                (payout, shares_to_sell, market_id)
            )
        else:
            conn.execute(
                "UPDATE markets SET no_pool=no_pool-?, no_shares=no_shares-? WHERE id=?",
                (payout, shares_to_sell, market_id)
            )

        # Credit user
        conn.execute(
            "UPDATE memberships SET balance=balance+? WHERE group_id=? AND user_id=?",
            (payout, group_id, user_id)
        )

        # Record as a negative-shares position event
        conn.execute(
            """INSERT INTO positions (user_id, market_id, group_id, side, shares, points_spent)
               VALUES (?,?,?,?,?,?)""",
            (user_id, market_id, group_id, side, -shares_to_sell, -payout)
        )

    return get_market(market_id, group_id), payout


# ── Resolution & Payouts ──────────────────────────────────────────────────────

def resolve_market(market_id: int, group_id: int, resolution: str, admin_id: int):
    if resolution not in ("YES", "NO", "CANCELLED"):
        raise ValueError("Resolution must be YES, NO, or CANCELLED.")

    with get_db() as conn:
        market = conn.execute(
            "SELECT * FROM markets WHERE id=? AND group_id=?", (market_id, group_id)
        ).fetchone()
        if not market:
            raise ValueError("Market not found.")
        market = dict(market)

        if market["status"] not in ("open", "closed"):
            raise ValueError(f"Cannot resolve a market with status '{market['status']}'.")

        if resolution == "CANCELLED":
            _refund_all(conn, market)
        else:
            _pay_winners(conn, market, resolution)

        conn.execute(
            """UPDATE markets
               SET status='resolved', resolution=?, resolved_at=datetime('now'), approved_by=?
               WHERE id=?""",
            (resolution, admin_id, market_id)
        )

def _pay_winners(conn, market: dict, winning_side: str):
    total_pool = (market["yes_pool"] + market["no_pool"]) - SEED * 2

    winning_shares_col = "yes_shares" if winning_side == "YES" else "no_shares"
    total_winning_shares = market[winning_shares_col]

    if total_winning_shares <= 0:
        _refund_all(conn, market)
        return

    # Aggregate net shares per user on winning side
    rows = conn.execute(
        """SELECT user_id, SUM(shares) as net_shares
           FROM positions
           WHERE market_id=? AND side=?
           GROUP BY user_id
           HAVING net_shares > 0""",
        (market["id"], winning_side)
    ).fetchall()

    for row in rows:
        payout = (row["net_shares"] / total_winning_shares) * total_pool
        conn.execute(
            "UPDATE memberships SET balance=balance+? WHERE group_id=? AND user_id=?",
            (payout, market["group_id"], row["user_id"])
        )

def _refund_all(conn, market: dict):
    # Refund net points spent per user (buy cost minus any sell proceeds already returned)
    rows = conn.execute(
        """SELECT user_id, SUM(points_spent) as net_spent
           FROM positions WHERE market_id=?
           GROUP BY user_id
           HAVING net_spent > 0""",
        (market["id"],)
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE memberships SET balance=balance+? WHERE group_id=? AND user_id=?",
            (row["net_spent"], market["group_id"], row["user_id"])
        )


# ── User Positions ────────────────────────────────────────────────────────────

def get_user_positions(user_id: int, group_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT
                 p.market_id,
                 p.side,
                 SUM(p.shares)       AS net_shares,
                 SUM(p.points_spent) AS net_spent,
                 m.question,
                 m.status,
                 m.yes_pool,
                 m.no_pool,
                 m.yes_shares,
                 m.no_shares,
                 m.resolution
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.user_id=? AND p.group_id=?
               GROUP BY p.market_id, p.side
               HAVING net_shares > 0
               ORDER BY p.market_id DESC""",
            (user_id, group_id)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Weekly Refill ─────────────────────────────────────────────────────────────

def do_weekly_refill(group_id: int) -> list[dict]:
    """
    Top up any member with balance < REFILL_FLOOR back to REFILL_FLOOR.
    Returns list of affected members.
    """
    with get_db() as conn:
        affected = conn.execute(
            """SELECT m.user_id, u.username, m.balance
               FROM memberships m
               JOIN users u ON u.telegram_id = m.user_id
               WHERE m.group_id=? AND m.balance < ?""",
            (group_id, REFILL_FLOOR)
        ).fetchall()

        for row in affected:
            top_up = REFILL_FLOOR - row["balance"]
            conn.execute(
                """UPDATE memberships
                   SET balance=balance+?, last_refill_at=datetime('now')
                   WHERE group_id=? AND user_id=?""",
                (top_up, group_id, row["user_id"])
            )

    return [dict(r) for r in affected]

def give_points(user_id: int, group_id: int, amount: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE memberships SET balance=balance+? WHERE group_id=? AND user_id=?",
            (amount, group_id, user_id)
        )
