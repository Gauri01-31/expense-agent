import json
from datetime import date, datetime

import pandas as pd
import streamlit as st

import agent
import db
import extractor

st.set_page_config(page_title="Expense & Bill Agent", page_icon="💸", layout="wide")
db.init()
CATS = ["Food", "Utilities", "Rent", "Transport", "Shopping", "Health", "Entertainment", "Education", "Other"]


def pdate(s, default=None):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default or date.today()


# ---------- sidebar: proactive alerts ----------
with st.sidebar:
    st.header("Alerts")
    alerts = db.alerts()
    if not alerts:
        st.success("Nothing needs attention.")
    for a in alerts:
        st.warning(a)

st.title("💸 Expense & Bill Agent")
tab_chat, tab_upload, tab_dash = st.tabs(["Chat", "Upload bill", "Dashboard"])

# ---------- chat ----------
with tab_chat:
    st.session_state.setdefault("api_msgs", [])
    st.session_state.setdefault("shown", [])
    for role, text, trace in st.session_state.shown:
        with st.chat_message(role):
            st.write(text)
            if trace:
                with st.expander("Agent actions"):
                    st.json(trace)
    if prompt := st.chat_input("e.g. 'Spent 450 on dinner yesterday' or 'How much did I spend on food this month?'"):
        st.session_state.shown.append(("user", prompt, None))
        st.session_state.api_msgs.append(agent.user_message(prompt))
        try:
            with st.spinner("Thinking..."):
                reply, trace = agent.run(st.session_state.api_msgs)
        except Exception as e:
            reply, trace = f"Error: {e}", None
        st.session_state.shown.append(("assistant", reply, trace))
        st.rerun()

# ---------- upload ----------
with tab_upload:
    f = st.file_uploader("Receipt or bill (image or PDF)", type=["png", "jpg", "jpeg", "webp", "pdf"])
    if f and st.button("Extract details"):
        try:
            with st.spinner("Reading document..."):
                st.session_state.extracted = extractor.extract(f.getvalue(), f.type)
        except Exception as e:
            st.error(f"Extraction failed: {e}")
    ex = st.session_state.get("extracted")
    if ex:
        conf = float(ex.get("confidence") or 0)
        (st.warning if conf < 0.7 else st.success)(f"Extraction confidence: {conf:.0%}. Please review before saving.")
        c1, c2, c3 = st.columns(3)
        vendor = c1.text_input("Vendor", ex.get("vendor") or "")
        amount = c2.number_input("Amount", value=float(ex.get("amount") or 0), min_value=0.0)
        cat_default = ex.get("category") if ex.get("category") in CATS else "Other"
        category = c3.selectbox("Category", CATS, index=CATS.index(cat_default))
        d1, d2, d3 = st.columns(3)
        exp_date = d1.date_input("Date", pdate(ex.get("expense_date")))
        is_bill = d2.checkbox("This is an unpaid bill", value=bool(ex.get("is_bill")))
        due = d3.date_input("Due date", pdate(ex.get("due_date"))) if is_bill else None
        dups = db.find_duplicates(vendor, amount, exp_date.isoformat()) if vendor else []
        force = False
        if dups:
            st.warning(f"Possible duplicate of expense #{dups[0]['id']} ({dups[0]['expense_date']}).")
            force = st.checkbox("Save anyway")
        if st.button("Save", disabled=bool(dups) and not force, type="primary"):
            res = db.add_expense(vendor, amount, category, exp_date.isoformat(), ex.get("notes") or "",
                                 is_bill, due.isoformat() if due else None,
                                 "unpaid" if is_bill else "paid", ex.get("currency") or "INR", force=True)
            st.success(f"Saved as #{res['id']}")
            st.session_state.extracted = None
            st.rerun()

# ---------- dashboard ----------
with tab_dash:
    month = st.text_input("Month (YYYY-MM)", date.today().strftime("%Y-%m"))
    start, end = f"{month}-01", f"{month}-31"
    summ = db.summarize_spending(start, end, "category")
    m1, m2 = st.columns([1, 2])
    m1.metric("Total this month", f"₹{summ['grand_total']:,.0f}")
    if summ["rows"]:
        df = pd.DataFrame(summ["rows"]).set_index("grp")
        m2.bar_chart(df["total"])
    st.subheader("Budgets")
    bs = db.budget_status(month)["budgets"]
    if bs:
        st.dataframe(pd.DataFrame(bs), use_container_width=True, hide_index=True)
    with st.expander("Set a budget"):
        bc, bl = st.columns(2)
        cat = bc.selectbox("Category", CATS, key="bcat")
        lim = bl.number_input("Monthly limit", min_value=0.0, step=500.0)
        if st.button("Save budget"):
            db.set_budget(cat, lim)
            st.rerun()
    st.subheader("Upcoming / overdue bills")
    dues = db.upcoming_dues(30)["dues"]
    st.dataframe(pd.DataFrame(dues)[["id", "vendor", "amount", "due_date", "overdue"]] if dues
                 else pd.DataFrame(), use_container_width=True, hide_index=True)
    st.subheader("Recent expenses")
    rec = db.query_expenses(limit=30)
    if rec:
        st.dataframe(pd.DataFrame(rec).drop(columns=["created_at"]), use_container_width=True, hide_index=True)
