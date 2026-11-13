"""SQLite storage + the business logic the agent's tools call."""
import os
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = os.getenv("EXPENSE_DB", "expenses.db")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'INR',
                category TEXT DEFAULT 'Other',
                expense_date TEXT NOT NULL,
                notes TEXT,
                is_bill INTEGER DEFAULT 0,
                due_date TEXT,
                status TEXT DEFAULT 'paid',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS budgets(
                category TEXT PRIMARY KEY,
                monthly_limit REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_id INTEGER,
                remind_on TEXT NOT NULL,
                message TEXT,
                done INTEGER DEFAULT 0
            );
            """
        )


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------- expenses ----------
def find_duplicates(vendor, amount, expense_date, window_days=3):
    try:
        d = datetime.strptime(expense_date, "%Y-%m-%d").date()
    except ValueError:
        return []
    lo, hi = d - timedelta(days=window_days), d + timedelta(days=window_days)
    with conn() as c:
        cur = c.execute(
            """SELECT * FROM expenses
               WHERE ABS(amount - ?) < 0.01 AND LOWER(vendor) = LOWER(?)
               AND expense_date BETWEEN ? AND ?""",
            (amount, vendor.strip(), lo.isoformat(), hi.isoformat()),
        )
        return _rows(cur)


def add_expense(vendor, amount, category="Other", expense_date=None, notes="",
                is_bill=False, due_date=None, status="paid", currency="INR", force=False):
    expense_date = expense_date or date.today().isoformat()
    if not force:
        dups = find_duplicates(vendor, amount, expense_date)
        if dups:
            return {"saved": False, "reason": "possible_duplicate", "existing": dups,
                    "hint": "Ask the user whether to save anyway; if yes, call again with force=true."}
    with conn() as c:
        cur = c.execute(
            """INSERT INTO expenses(vendor, amount, currency, category, expense_date,
               notes, is_bill, due_date, status) VALUES (?,?,?,?,?,?,?,?,?)""",
            (vendor.strip(), amount, currency, category, expense_date, notes,
             int(bool(is_bill)), due_date, status),
        )
        return {"saved": True, "id": cur.lastrowid}


def query_expenses(start=None, end=None, category=None, vendor=None, limit=50):
    sql, args = "SELECT * FROM expenses WHERE 1=1", []
    if start: sql += " AND expense_date >= ?"; args.append(start)
    if end: sql += " AND expense_date <= ?"; args.append(end)
    if category: sql += " AND LOWER(category) = LOWER(?)"; args.append(category)
    if vendor: sql += " AND LOWER(vendor) LIKE ?"; args.append(f"%{vendor.lower()}%")
    sql += " ORDER BY expense_date DESC, id DESC LIMIT ?"; args.append(limit)
    with conn() as c:
        return _rows(c.execute(sql, args))


def summarize_spending(start=None, end=None, group_by="category"):
    col = {"category": "category", "vendor": "vendor", "month": "substr(expense_date,1,7)"}[group_by]
    sql = f"SELECT {col} AS grp, ROUND(SUM(amount),2) AS total, COUNT(*) AS n FROM expenses WHERE 1=1"
    args = []
    if start: sql += " AND expense_date >= ?"; args.append(start)
    if end: sql += " AND expense_date <= ?"; args.append(end)
    sql += " GROUP BY grp ORDER BY total DESC"
    with conn() as c:
        rows = _rows(c.execute(sql, args))
    return {"group_by": group_by, "rows": rows, "grand_total": round(sum(r["total"] for r in rows), 2)}


def mark_paid(expense_id):
    with conn() as c:
        c.execute("UPDATE expenses SET status='paid' WHERE id=?", (expense_id,))
    return {"ok": True, "id": expense_id}


# ---------- budgets ----------
def set_budget(category, monthly_limit):
    with conn() as c:
        c.execute("INSERT INTO budgets(category, monthly_limit) VALUES(?,?) "
                  "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit",
                  (category, monthly_limit))
    return {"ok": True, "category": category, "monthly_limit": monthly_limit}


def budget_status(month=None):
    month = month or date.today().strftime("%Y-%m")
    with conn() as c:
        rows = _rows(c.execute(
            """SELECT b.category, b.monthly_limit,
                      ROUND(COALESCE(SUM(e.amount),0),2) AS spent
               FROM budgets b LEFT JOIN expenses e
                 ON LOWER(e.category)=LOWER(b.category) AND substr(e.expense_date,1,7)=?
               GROUP BY b.category""", (month,)))
    for r in rows:
        r["pct_used"] = round(100 * r["spent"] / r["monthly_limit"], 1) if r["monthly_limit"] else 0
    return {"month": month, "budgets": rows}


# ---------- bills & reminders ----------
def upcoming_dues(days=14):
    horizon = (date.today() + timedelta(days=days)).isoformat()
    with conn() as c:
        rows = _rows(c.execute(
            """SELECT * FROM expenses WHERE status='unpaid' AND due_date IS NOT NULL
               AND due_date <= ? ORDER BY due_date""", (horizon,)))
    today = date.today().isoformat()
    for r in rows:
        r["overdue"] = r["due_date"] < today
    return {"dues": rows}


def add_reminder(remind_on, message, expense_id=None):
    with conn() as c:
        cur = c.execute("INSERT INTO reminders(expense_id, remind_on, message) VALUES (?,?,?)",
                        (expense_id, remind_on, message))
    return {"ok": True, "id": cur.lastrowid}


def alerts():
    """Proactive alerts shown in the UI without the user asking."""
    out = []
    for d in upcoming_dues(7)["dues"]:
        tag = "OVERDUE" if d["overdue"] else "Due soon"
        out.append(f"{tag}: {d['vendor']} ₹{d['amount']:.0f} (due {d['due_date']})")
    for b in budget_status()["budgets"]:
        if b["pct_used"] >= 80:
            out.append(f"Budget: {b['category']} at {b['pct_used']}% (₹{b['spent']:.0f}/₹{b['monthly_limit']:.0f})")
    with conn() as c:
        today = date.today().isoformat()
        for r in c.execute("SELECT * FROM reminders WHERE done=0 AND remind_on<=?", (today,)):
            out.append(f"Reminder: {r['message']}")
    return out
