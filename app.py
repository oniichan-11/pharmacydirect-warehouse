"""PharmacyDirect GH — Central Warehouse Inventory Manager (Streamlit)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import db
import reports

st.set_page_config(page_title="PDGH Warehouse", page_icon="📦", layout="wide")

APP_DIR = Path(__file__).parent
SEED_FILE = APP_DIR / "data" / "opening_stock.csv"
STATUS_COLORS = {"Expired": "background-color:#f4c7c3", "≤ 3 months": "background-color:#fce8b2",
                 "≤ 6 months": "background-color:#fff5d6"}


# --------------------------------------------------------------------------- config / connection
def secret(key, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


@st.cache_resource(show_spinner="Connecting to database…")
def get_engine(url: str):
    eng = db.make_engine(url)
    db.init_db(eng)
    return eng


def show_db_error(url: str, err: Exception):
    info = db.describe_db_url(url)
    msg = str(getattr(err, "orig", None) or err)
    try:
        pw = db.parse_pg_url(url).password
        if pw:
            msg = msg.replace(pw, "****")
    except Exception:
        pass
    st.error("**Could not connect to the database.** Check `DATABASE_URL` in the app's Secrets.")
    for h in info.pop("hints", []):
        st.warning(h)
    st.write("Connection details being used (password hidden):")
    st.json(info)
    st.code(msg.strip()[:1500], language="text")
    st.stop()


DB_URL = secret("DATABASE_URL")
LOCAL_MODE = not DB_URL
try:
    engine = get_engine(DB_URL or f"sqlite:///{APP_DIR / 'warehouse_local.db'}")
except Exception as e:
    show_db_error(DB_URL or "", e)


def bump(key: str):
    """Reset a widget (e.g. clear a line editor) by changing its key."""
    st.session_state[key] = st.session_state.get(key, 0) + 1


def flash(msg: str, kind="success"):
    st.session_state["_flash"] = (kind, msg)


def show_flash():
    if "_flash" in st.session_state:
        kind, msg = st.session_state.pop("_flash")
        getattr(st, kind)(msg)


def style_status(df: pd.DataFrame, col="Status"):
    if col not in df.columns or df.empty:
        return df
    return df.style.apply(lambda r: [STATUS_COLORS.get(r[col], "")] * len(r), axis=1)


# --------------------------------------------------------------------------- login
def login() -> str | None:
    users = secret("users")          # optional [users] table in secrets: name = "password"
    shared_pw = secret("APP_PASSWORD")
    if st.session_state.get("user"):
        return st.session_state["user"]
    st.title("📦 PharmacyDirect GH — Warehouse")
    with st.form("login"):
        name = st.text_input("Your name", help="Recorded against every transaction you enter")
        pw = st.text_input("Password", type="password") if (users or shared_pw) else ""
        ok = st.form_submit_button("Sign in", type="primary")
    if ok:
        name = name.strip()
        if users:
            match = {k.lower(): (k, v) for k, v in dict(users).items()}.get(name.lower())
            if not match or str(match[1]) != pw:
                st.error("Name or password not recognised")
                return None
            name = match[0]
        elif shared_pw and pw != str(shared_pw):
            st.error("Wrong password")
            return None
        if not name:
            st.error("Enter your name")
            return None
        st.session_state["user"] = name
        st.rerun()
    if LOCAL_MODE:
        st.caption("Local test mode: no DATABASE_URL set, data is kept in a local SQLite file.")
    return None


USER = login()
if not USER:
    st.stop()

# --------------------------------------------------------------------------- sidebar
PAGES = ["Dashboard", "Stock on Hand", "Receive Stock", "Transfer to Branch", "Returns & Adjustments",
         "Transaction History", "Products & Batches", "Reports & Export", "Setup & Import"]
with st.sidebar:
    st.markdown("### 📦 PDGH Warehouse")
    page = st.radio("Go to", PAGES, label_visibility="collapsed")
    st.divider()
    st.caption(f"Signed in as **{USER}**")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    if LOCAL_MODE:
        st.warning("Local test mode. Data is saved to a local file only. Set DATABASE_URL (Supabase) "
                   "in secrets before real use.")

TODAY = dt.date.today()


def batch_options(df: pd.DataFrame) -> dict[str, int]:
    """Label -> batch_id for selectboxes, sorted product then expiry (FEFO)."""
    out = {}
    for _, r in df.iterrows():
        exp = r["expiry_date"].strftime("%m/%Y") if r["expiry_date"] is not None and not pd.isna(r["expiry_date"]) \
            else "no exp"
        bn = f" · B{r['batch_no']}" if r["batch_no"] else ""
        flag = " ⚠EXPIRED" if r["status"] == "Expired" else ""
        out[f"{r['label']} | exp {exp}{bn} | avail {int(r['on_hand'])}{flag} #{int(r['batch_id'])}"] = \
            int(r["batch_id"])
    return out


def batch_table(df: pd.DataFrame) -> pd.DataFrame:
    return df[["name", "manufacturer", "category", "expiry_date", "days_left", "status", "batch_no", "on_hand",
               "unit"]].rename(columns={"name": "Product", "manufacturer": "Manufacturer", "category": "Category",
                                        "expiry_date": "Expiry", "days_left": "Days left", "status": "Status",
                                        "batch_no": "Batch no.", "on_hand": "On hand", "unit": "Unit"})


# =========================================================================== pages
def page_dashboard():
    st.title("Dashboard")
    show_flash()
    bdf = db.stock_by_batch(engine)
    pdf = db.stock_by_product(bdf)
    if bdf.empty:
        st.info("No stock recorded yet. Go to **Setup & Import** to load the opening count.")
        return
    month_start = TODAY.replace(day=1)
    led = db.ledger(engine, month_start, TODAY)
    tb = db.transfers_by_branch(led)

    c = st.columns(5)
    c[0].metric("Products in stock", int((pdf["on_hand"] > 0).sum()))
    c[1].metric("Units on hand", f"{int(bdf['on_hand'].sum()):,}")
    exp = bdf[bdf.status == "Expired"]
    c[2].metric("Expired batches", len(exp), f"{int(exp['on_hand'].sum())} units", delta_color="inverse")
    soon = bdf[bdf.status == "≤ 3 months"]
    c[3].metric("Expiring ≤ 3 months", len(soon), f"{int(soon['on_hand'].sum())} units", delta_color="off")
    c[4].metric("Below reorder level", int(pdf["low_stock"].sum()))

    st.subheader(f"Transfers this month ({month_start:%B})")
    branches = db.list_branches(engine)
    cols = st.columns(max(len(branches), 1))
    for i, b in enumerate(branches):
        n = int(tb.loc[tb["branch"] == b, "net_to_branch"].sum()) if len(tb) else 0
        cols[i].metric(f"Net units to {b}", f"{n:,}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Expiry watch")
        w = bdf[bdf.status.isin(["Expired", "≤ 3 months", "≤ 6 months"])].sort_values("expiry_date")
        if w.empty:
            st.success("Nothing expired or expiring within 6 months.")
        else:
            st.dataframe(style_status(batch_table(w)), hide_index=True, width="stretch",
                         height=min(380, 38 + 35 * len(w)))
            if len(exp):
                st.caption("Expired stock should be written off in **Returns & Adjustments → Write-off**.")
    with right:
        st.subheader("Recent activity")
        s = db.transactions_summary(db.ledger(engine, TODAY - dt.timedelta(days=30), TODAY)).head(12)
        if s.empty:
            st.caption("No transactions in the last 30 days.")
        else:
            s["voided"] = s["voided"].map({True: "VOIDED", False: ""})
            st.dataframe(s[["txn_no", "txn_date", "txn_type", "counterparty", "units", "entered_by", "voided"]],
                         hide_index=True, width="stretch", height=min(380, 38 + 35 * len(s)))
    low = pdf[pdf["low_stock"]]
    if len(low):
        st.subheader("Below reorder level")
        st.dataframe(low[["name", "manufacturer", "on_hand", "expired_qty", "reorder_level", "unit"]],
                     hide_index=True, width="stretch")


def page_stock():
    st.title("Stock on Hand")
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    q = c1.text_input("Search product / manufacturer", placeholder="e.g. amlodipine, teva")
    cats = c2.multiselect("Category", db.CATEGORIES)
    status = c3.multiselect("Expiry status", ["Expired", "≤ 3 months", "≤ 6 months", "OK", "No expiry"])
    as_of = c4.date_input("As of date", TODAY, help="See the stock position on any past date")
    bdf = db.stock_by_batch(engine, as_of=as_of)
    if q:
        m = bdf["name"].str.contains(q, case=False, na=False) | bdf["manufacturer"].str.contains(q, case=False,
                                                                                                    na=False)
        bdf = bdf[m]
    if cats:
        bdf = bdf[bdf["category"].isin(cats)]
    if status:
        bdf = bdf[bdf["status"].isin(status)]
    t1, t2 = st.tabs(["By product", "By batch / expiry"])
    with t1:
        p = db.stock_by_product(bdf)
        st.caption(f"{len(p)} products · {int(p['on_hand'].sum()) if len(p) else 0:,} units")
        st.dataframe(p.drop(columns=["product_id"], errors="ignore"), hide_index=True, width="stretch",
                     height=560, column_config={"low_stock": st.column_config.CheckboxColumn("Low stock")})
    with t2:
        st.caption(f"{len(bdf)} batches")
        st.dataframe(style_status(batch_table(bdf)), hide_index=True, width="stretch", height=560)
    st.download_button("Download this view (CSV)", batch_table(bdf).to_csv(index=False).encode(),
                       f"stock_{as_of}.csv", "text/csv")


def new_product_form(key: str):
    with st.expander("➕ Product not in the list? Add it here first"):
        with st.form(f"newprod_{key}", clear_on_submit=True):
            a, b = st.columns(2)
            name = a.text_input("Product name incl. strength & form", placeholder="Amlodipine 5mg Tablets")
            mfr = b.text_input("Manufacturer", placeholder="Teva")
            c, d, e = st.columns(3)
            cat = c.selectbox("Category", db.CATEGORIES)
            unit = d.selectbox("Unit", db.UNITS)
            rl = e.number_input("Reorder level", 0, step=1)
            if st.form_submit_button("Add product"):
                try:
                    db.add_product(engine, USER, name=name, manufacturer=mfr, category=cat, unit=unit,
                                   reorder_level=rl)
                    flash(f"Added {db.product_label(name.strip(), mfr.strip())}")
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))


def page_receive():
    st.title("Receive Stock")
    st.caption("New arrivals from overseas or local distributors. Each line becomes a batch (product + expiry).")
    show_flash()
    new_product_form("rcv")
    prods = db.list_products(engine, active_only=True)
    if prods.empty:
        st.info("Add products first (above), or import the opening stock in Setup & Import.")
        return
    labels = prods["label"].tolist()
    lab2id = dict(zip(prods["label"], prods["id"]))
    sup = db.list_suppliers(engine)

    h1, h2, h3, h4 = st.columns([2, 3, 2, 2])
    rdate = h1.date_input("Date received", TODAY, max_value=TODAY)
    known = sup["name"].tolist()
    sname = h2.selectbox("Supplier", ["— new supplier —"] + known, index=1 if known else 0)
    if sname == "— new supplier —":
        sname = h2.text_input("New supplier name")
        skind = h3.radio("Source", ["Local", "Overseas"], horizontal=True)
    else:
        skind = sup.set_index("name").loc[sname, "kind"]
        h3.text_input("Source", skind, disabled=True)
    ref = h4.text_input("Invoice / waybill no.")
    note = st.text_input("Note (optional)")

    k = f"rcv_editor_{st.session_state.get('rcv_v', 0)}"
    blank = pd.DataFrame({"Product": pd.Series([None] * 5, dtype="object"), "Expiry (MM/YY)": [""] * 5,
                          "Batch no.": [""] * 5, "Qty": pd.Series([None] * 5, dtype="Int64")})
    lines = st.data_editor(blank, key=k, num_rows="dynamic", width="stretch", hide_index=True,
                           column_config={
                               "Product": st.column_config.SelectboxColumn(options=labels, width="large"),
                               "Expiry (MM/YY)": st.column_config.TextColumn(help="e.g. 09/28"),
                               "Qty": st.column_config.NumberColumn(min_value=1, step=1)})
    lines = lines[lines["Product"].notna() | lines["Qty"].notna()]
    items, problems, warns = [], [], []
    for i, r in lines.iterrows():
        n = i + 1
        if pd.isna(r["Product"]):
            problems.append(f"Line {n}: choose a product"); continue
        if pd.isna(r["Qty"]) or int(r["Qty"]) <= 0:
            problems.append(f"Line {n}: enter a quantity"); continue
        try:
            exp = db.parse_expiry(r["Expiry (MM/YY)"])
        except ValueError as e:
            problems.append(f"Line {n}: {e}"); continue
        if exp is None:
            warns.append(f"Line {n} ({r['Product']}): no expiry entered")
        elif exp < TODAY:
            problems.append(f"Line {n} ({r['Product']}): expiry {exp:%m/%Y} has already passed. Don't receive "
                            "expired stock"); continue
        elif (exp - TODAY).days <= 180:
            warns.append(f"Line {n} ({r['Product']}): short-dated (expires {exp:%m/%Y})")
        items.append({"product_id": lab2id[r["Product"]], "expiry": exp, "batch_no": r["Batch no."] or "",
                      "qty": int(r["Qty"]), "label": r["Product"]})
    for p in problems:
        st.error(p)
    for w in warns:
        st.warning(w)
    if items:
        st.markdown(f"**{len(items)} line(s), {sum(i['qty'] for i in items):,} units** ready to post.")
    confirm = st.checkbox("I have checked these quantities and expiry dates against the invoice / physical goods")
    if st.button("Post receipt", type="primary", disabled=not (items and confirm and not problems)):
        if not sname or not str(sname).strip():
            st.error("Enter the supplier"); return
        try:
            no = db.receive_stock(engine, rdate, str(sname).strip(), skind, ref, note, items, USER)
            bump("rcv_v")
            flash(f"Receipt {no} posted: {len(items)} lines, {sum(i['qty'] for i in items):,} units added.")
            st.rerun()
        except Exception as e:
            st.error(str(e))


def page_transfer():
    st.title("Transfer to Branch")
    st.caption("Issue stock from the warehouse to a retail branch. Batches are listed earliest-expiry first "
               "(FEFO); send the earliest dates first.")
    show_flash()
    if st.session_state.get("last_trf"):
        no = st.session_state["last_trf"]
        st.download_button(f"⬇ Download transfer note {no} (Excel)", reports.transfer_note_xlsx(engine, no),
                           f"Transfer_Note_{no}.xlsx", key="dl_last_trf")
    bdf = db.stock_by_batch(engine)
    bdf = bdf[(bdf["on_hand"] > 0) & (bdf["status"] != "Expired")]
    if bdf.empty:
        st.info("No transferable stock in the warehouse.")
        return
    opts = batch_options(bdf)
    branches = db.list_branches(engine)
    h1, h2, h3, h4 = st.columns(4)
    br = h1.selectbox("To branch", branches)
    tdate = h2.date_input("Transfer date", TODAY, max_value=TODAY)
    ref = h3.text_input("Requisition / reference no.")
    recv = h4.text_input("Collected / received by")
    note = st.text_input("Note (optional)")

    k = f"trf_editor_{st.session_state.get('trf_v', 0)}"
    blank = pd.DataFrame({"Batch": pd.Series([None] * 5, dtype="object"),
                          "Qty": pd.Series([None] * 5, dtype="Int64")})
    lines = st.data_editor(blank, key=k, num_rows="dynamic", width="stretch", hide_index=True,
                           column_config={"Batch": st.column_config.SelectboxColumn(
                               "Product | expiry | available", options=list(opts), width="large"),
                               "Qty": st.column_config.NumberColumn(min_value=1, step=1)})
    lines = lines[lines["Batch"].notna() & lines["Qty"].notna()]
    items, problems = [], []
    want: dict[int, int] = {}
    for _, r in lines.iterrows():
        bid = opts[r["Batch"]]
        want[bid] = want.get(bid, 0) + int(r["Qty"])
        items.append({"batch_id": bid, "qty": -int(r["Qty"])})
    by_id = bdf.set_index("batch_id")
    for bid, q in want.items():
        row = by_id.loc[bid]
        if q > row["on_hand"]:
            problems.append(f"{row['label']} exp {row['expiry_date']}: only {row['on_hand']} available, {q} entered")
        earlier = bdf[(bdf["product_id"] == row["product_id"]) & (bdf["batch_id"] != bid) &
                      (bdf["expiry_date"].notna()) & (~bdf["batch_id"].isin(want.keys()))]
        if row["expiry_date"] is not None and not pd.isna(row["expiry_date"]):
            earlier = earlier[earlier["expiry_date"] < row["expiry_date"]]
            if len(earlier):
                st.warning(f"FEFO: {row['label']} has an earlier-expiring batch "
                           f"({earlier.iloc[0]['expiry_date']:%m/%Y}, {earlier.iloc[0]['on_hand']} available). "
                           "Consider sending that first.")
    for p in problems:
        st.error(p)
    if items:
        st.markdown(f"**{len(items)} line(s), {sum(-i['qty'] for i in items):,} units → {br}**")
    confirm = st.checkbox("Goods counted and packed; quantities above are correct")
    if st.button("Post transfer", type="primary", disabled=not (items and confirm and not problems)):
        try:
            full_note = "; ".join(x for x in [f"Received by: {recv}" if recv else "", note] if x)
            no = db.post_transaction(engine, "TRANSFER", tdate, items, USER, br, ref, full_note)
            bump("trf_v")
            st.session_state["last_trf"] = no
            flash(f"Transfer {no} to {br} posted: {sum(-i['qty'] for i in items):,} units. Download the "
                  "transfer note above for signatures.")
            st.rerun()
        except Exception as e:
            st.error(str(e))


def page_adjust():
    st.title("Returns & Adjustments")
    show_flash()
    t1, t2, t3 = st.tabs(["↩ Return from branch", "🗑 Write-off (expired / damaged)", "🔢 Stock count correction"])
    all_b = db.stock_by_batch(engine, include_zero=True)

    with t1:
        st.caption("Stock coming back from a branch, e.g. short-dated items or wrong items sent.")
        if all_b.empty:
            st.info("No batches yet.")
        else:
            opts = batch_options(all_b)
            with st.form("ret", clear_on_submit=True):
                a, b, c = st.columns([4, 2, 1])
                lab = a.selectbox("Batch returned", list(opts))
                br = b.selectbox("From branch", db.list_branches(engine))
                q = c.number_input("Qty", 1, step=1)
                d1, d2 = st.columns([1, 3])
                rd = d1.date_input("Date", TODAY, max_value=TODAY)
                reason = d2.text_input("Reason (required)")
                if st.form_submit_button("Post return", type="primary"):
                    if not reason.strip():
                        st.error("Give a reason")
                    else:
                        try:
                            no = db.post_transaction(engine, "RETURN", rd, [{"batch_id": opts[lab], "qty": int(q)}],
                                                     USER, br, "", reason)
                            flash(f"Return {no} posted.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

    with t2:
        st.caption("Remove stock that is expired, damaged, lost or recalled. This cannot be sold or transferred.")
        instock = all_b[all_b["on_hand"] > 0]
        expired = instock[instock["status"] == "Expired"]
        if len(expired):
            st.error(f"{len(expired)} expired batch(es) still in stock ({int(expired['on_hand'].sum())} units).")
            st.dataframe(batch_table(expired), hide_index=True, width="stretch")
            if st.button(f"Write off ALL {len(expired)} expired batches"):
                st.session_state["wo_all"] = True
            if st.session_state.get("wo_all"):
                st.warning("Confirm: this posts one write-off removing all expired units shown above.")
                if st.button("Yes, write them off", type="primary"):
                    try:
                        no = db.post_transaction(engine, "WRITE_OFF", TODAY,
                                                 [{"batch_id": int(r.batch_id), "qty": -int(r.on_hand),
                                                   "line_note": "Expired"} for r in expired.itertuples()],
                                                 USER, "", "", "Expired stock write-off")
                        st.session_state.pop("wo_all", None)
                        flash(f"Write-off {no} posted.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
        if instock.empty:
            st.info("No stock to write off.")
        else:
            opts = batch_options(instock)
            with st.form("wo", clear_on_submit=True):
                a, c = st.columns([5, 1])
                lab = a.selectbox("Batch", list(opts))
                q = c.number_input("Qty", 1, step=1)
                d1, d2, d3 = st.columns([1, 1, 3])
                rd = d1.date_input("Date", TODAY, max_value=TODAY)
                why = d2.selectbox("Reason", ["Expired", "Damaged", "Lost / missing", "Recalled", "Other"])
                det = d3.text_input("Details")
                if st.form_submit_button("Post write-off", type="primary"):
                    try:
                        no = db.post_transaction(engine, "WRITE_OFF", rd, [{"batch_id": opts[lab], "qty": -int(q),
                                                                             "line_note": why}], USER, "", "",
                                                 f"{why}: {det}".strip(": "))
                        flash(f"Write-off {no} posted.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    with t3:
        st.caption("After a physical count, enter what is actually on the shelf. The app posts the difference "
                   "as an adjustment, with your reason.")
        if all_b.empty:
            st.info("No batches yet.")
        else:
            opts = batch_options(all_b)
            lab = st.selectbox("Batch counted", list(opts), key="cnt_batch")
            bid = opts[lab]
            sys_q = int(all_b.set_index("batch_id").loc[bid, "on_hand"])
            with st.form("cnt", clear_on_submit=True):
                a, b, c = st.columns(3)
                a.metric("System quantity", sys_q)
                counted = b.number_input("Physically counted", 0, value=sys_q, step=1)
                reason = c.text_input("Reason (required)")
                if st.form_submit_button("Post correction", type="primary"):
                    diff = int(counted) - sys_q
                    if diff == 0:
                        st.info("No difference, nothing posted.")
                    elif not reason.strip():
                        st.error("Give a reason")
                    else:
                        try:
                            no = db.post_transaction(engine, "ADJUSTMENT", TODAY, [{"batch_id": bid, "qty": diff,
                                                     "line_note": f"system {sys_q} → counted {counted}"}],
                                                     USER, "", "", reason)
                            flash(f"Adjustment {no} posted ({diff:+d}).")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))


def page_history():
    st.title("Transaction History")
    show_flash()
    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
    rng = c1.date_input("Date range", (TODAY - dt.timedelta(days=30), TODAY))
    rng = list(rng) if isinstance(rng, (list, tuple)) else [rng]
    start, end = rng[0], rng[-1]
    types = c2.multiselect("Type", list(db.TXN_TYPES))
    br = c3.multiselect("Branch", db.list_branches(engine, False))
    prod = c4.text_input("Product contains")
    led = db.ledger(engine, start, end)
    if types:
        led = led[led["txn_type"].isin(types)]
    if br:
        led = led[led["counterparty"].isin(br)]
    if prod:
        led = led[led["label"].str.contains(prod, case=False, na=False)]
    summ = db.transactions_summary(led)
    st.caption(f"{len(summ)} transactions · {len(led)} lines")
    view = summ.drop(columns=["txn_id"]).copy()
    view["voided"] = view["voided"].map({True: "VOIDED", False: ""})
    st.dataframe(view, hide_index=True, width="stretch", height=320)
    st.download_button("Download lines (CSV)", led.drop(columns=["txn_id", "batch_id", "voided_by_id",
                                                               "reverses_id"]).to_csv(index=False).encode(),
                       f"ledger_{start}_{end}.csv", "text/csv")

    if summ.empty:
        return
    st.subheader("Transaction detail")
    pick = st.selectbox("Open transaction", summ["txn_no"].tolist())
    d = led[led["txn_no"] == pick]
    h = d.iloc[0]
    st.markdown(f"**{pick}** · {db.TXN_TYPES[h['txn_type']][1]} · {h['txn_date']} · "
                f"{h['counterparty'] or '—'} · ref {h['reference'] or '—'} · entered by **{h['entered_by']}** "
                f"at {pd.Timestamp(h['created_at']):%d %b %Y %H:%M} UTC")
    if h["note"]:
        st.caption(h["note"])
    st.dataframe(d[["name", "manufacturer", "expiry_date", "batch_no", "qty", "unit", "line_note"]],
                 hide_index=True, width="stretch")
    if h["txn_type"] == "TRANSFER":
        st.download_button("Transfer note (Excel)", reports.transfer_note_xlsx(engine, pick),
                           f"Transfer_Note_{pick}.xlsx")
    if h["voided"]:
        st.error("This transaction has been voided (a reversing entry was posted).")
    elif h["txn_type"] != "REVERSAL":
        with st.expander("Void this transaction"):
            st.caption("Voiding posts an equal and opposite entry. The original stays in history, marked VOIDED. "
                       "Use this for entries made in error, then re-enter them correctly.")
            reason = st.text_input("Reason for voiding (required)", key=f"void_{pick}")
            if st.button("Void transaction", type="primary", disabled=not reason.strip()):
                try:
                    no = db.void_transaction(engine, int(h["txn_id"]), reason, USER)
                    flash(f"{pick} voided by {no}.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))


def page_products():
    st.title("Products & Batches")
    show_flash()
    t1, t2, t3 = st.tabs(["Product catalogue", "Correct a batch (expiry / batch no.)", "Change log"])
    with t1:
        new_product_form("cat")
        prods = db.list_products(engine)
        if prods.empty:
            st.info("No products yet.")
        else:
            st.caption("Edit category, unit, reorder level or active status, then press Save. Every change is "
                       "logged. Untick *active* to hide a discontinued product from receipts.")
            cols = ["id", "name", "manufacturer", "category", "unit", "reorder_level", "active"]
            edited = st.data_editor(prods[cols], key=f"prod_ed_{st.session_state.get('prod_v', 0)}",
                                    hide_index=True, width="stretch", height=520, disabled=["id"],
                                    column_config={
                                        "category": st.column_config.SelectboxColumn(options=db.CATEGORIES),
                                        "unit": st.column_config.SelectboxColumn(options=db.UNITS),
                                        "reorder_level": st.column_config.NumberColumn(min_value=0, step=1)})
            if st.button("Save changes", type="primary"):
                orig = prods[cols].set_index("id")
                n = 0
                try:
                    for _, r in edited.set_index("id").iterrows():
                        o = orig.loc[r.name]
                        ch = {c: (int(r[c]) if c == "reorder_level" else (bool(r[c]) if c == "active"
                                                                           else str(r[c]).strip()))
                              for c in cols[1:] if r[c] != o[c]}
                        if ch:
                            db.update_product(engine, int(r.name), ch, USER)
                            n += 1
                    bump("prod_v")
                    flash(f"Saved {n} product change(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save: {e}")
    with t2:
        st.caption("Use this when an expiry date or batch number was recorded wrongly (e.g. a misread "
                   "handwritten date). Quantities are not changed; the correction is logged.")
        allb = db.stock_by_batch(engine, include_zero=True)
        if allb.empty:
            st.info("No batches yet.")
        else:
            opts = batch_options(allb)
            lab = st.selectbox("Batch", list(opts), key="fix_batch")
            row = allb.set_index("batch_id").loc[opts[lab]]
            with st.form("fixb"):
                a, b, c = st.columns(3)
                cur = row["expiry_date"].strftime("%m/%y") if row["expiry_date"] is not None and \
                    not pd.isna(row["expiry_date"]) else ""
                newexp = a.text_input("Correct expiry (MM/YY)", cur)
                newbn = b.text_input("Batch no.", row["batch_no"])
                why = c.text_input("Reason (required)")
                if st.form_submit_button("Save correction", type="primary"):
                    try:
                        db.correct_batch(engine, opts[lab], db.parse_expiry(newexp), newbn, why, USER)
                        flash("Batch corrected.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
    with t3:
        st.dataframe(db.audit_df(engine), hide_index=True, width="stretch", height=500)


def page_reports():
    st.title("Reports & Export")
    st.caption("Download a snapshot to share with management, auditors or the branches.")
    c1, c2 = st.columns(2)
    as_of = c1.date_input("Stock position as of", TODAY, max_value=TODAY)
    rng = c2.date_input("Activity period (transactions, transfers)", (TODAY.replace(day=1), TODAY))
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        st.info("Pick a start and end date."); return
    start, end = rng
    if st.button("Build Excel snapshot", type="primary"):
        with st.spinner("Building…"):
            st.session_state["snap"] = (reports.snapshot_xlsx(engine, as_of, start, end, USER), as_of)
    if "snap" in st.session_state:
        data, a = st.session_state["snap"]
        st.download_button("⬇ Download snapshot (.xlsx)", data, f"PDGH_Warehouse_Snapshot_{a}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("Sheets: Summary · Stock by Product · Stock by Batch · Expiry Watch · Transfers by Branch · "
                   "Transactions · Ledger")
    st.divider()
    st.subheader("Branch statement")
    st.caption("What each branch received from the warehouse in the period, net of returns.")
    tb = db.transfers_by_branch(db.ledger(engine, start, end))
    if tb.empty:
        st.caption("No transfers in this period.")
    else:
        b = st.selectbox("Branch", sorted(tb["branch"].unique()))
        t = tb[tb["branch"] == b]
        st.dataframe(t, hide_index=True, width="stretch")
        st.download_button(f"Download {b} statement (CSV)", t.to_csv(index=False).encode(),
                           f"{b}_statement_{start}_{end}.csv", "text/csv")
    st.divider()
    st.subheader("Quick CSV exports")
    bdf = db.stock_by_batch(engine, as_of=as_of)
    a, b = st.columns(2)
    a.download_button("Stock by batch (CSV)", batch_table(bdf).to_csv(index=False).encode(),
                      f"stock_by_batch_{as_of}.csv", "text/csv")
    b.download_button("Stock by product (CSV)", db.stock_by_product(bdf).to_csv(index=False).encode(),
                      f"stock_by_product_{as_of}.csv", "text/csv")


def page_setup():
    st.title("Setup & Import")
    show_flash()
    st.subheader("1 · Opening stock")
    if db.has_opening(engine):
        st.success("Opening stock has been loaded. To redo it, void the OPN transaction in Transaction History "
                   "first.")
    else:
        st.write("Load the initial warehouse count. Either use the bundled transcription of the handwritten "
                 "count sheets, or upload your own CSV/Excel with columns: `product, manufacturer, category, "
                 "unit, expiry_date (or expiry as MM/YY), qty` and optionally `batch_no, note`.")
        cnt_date = st.date_input("Date of the physical count", TODAY, max_value=TODAY)
        src = st.radio("Source", ["Bundled count sheets (data/opening_stock.csv)", "Upload a file"],
                       horizontal=True)
        df = None
        if src.startswith("Bundled"):
            if SEED_FILE.exists():
                df = pd.read_csv(SEED_FILE, dtype=str, keep_default_na=False)
            else:
                st.error("data/opening_stock.csv not found in the app folder.")
        else:
            up = st.file_uploader("CSV or Excel", type=["csv", "xlsx"])
            if up:
                raw = pd.read_csv(up, dtype=str, header=None, keep_default_na=False) \
                    if up.name.endswith(".csv") else pd.read_excel(up, dtype=str, header=None).fillna("")
                # find the header row (files may have a title block above the table)
                hdr = next((i for i, row in raw.iterrows()
                            if {"product", "qty"} <= {str(v).strip().lower() for v in row}), None)
                if hdr is None:
                    st.error("Couldn't find a header row with 'Product' and 'Qty' columns.")
                else:
                    df = raw.iloc[hdr + 1:].copy()
                    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
                    df = df[df["Product" if "Product" in df.columns else df.columns[
                        [c.lower() for c in df.columns].index("product")]].astype(str).str.strip() != ""]
        if df is not None:
            st.caption(f"{len(df)} rows")
            if "needs_check" in df.columns:
                flagged = df[df["needs_check"].str.upper() == "YES"]
                if len(flagged):
                    st.warning(f"{len(flagged)} rows are flagged for checking (unclear handwriting / missing "
                               "expiry). They will still be imported; fix them later under Products & Batches → "
                               "Correct a batch, or with a count correction.")
            st.dataframe(df, hide_index=True, width="stretch", height=300)
            if st.button("Import as opening stock", type="primary"):
                try:
                    no, n = db.import_opening_stock(engine, df, cnt_date, USER, "Initial warehouse count")
                    flash(f"Opening stock {no} posted: {n} lines.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    st.divider()
    st.subheader("2 · Branches")
    st.write(", ".join(db.list_branches(engine)))
    with st.form("addbr", clear_on_submit=True):
        nb = st.text_input("Add a branch")
        if st.form_submit_button("Add"):
            try:
                db.add_branch(engine, nb, USER)
                flash(f"Branch {nb} added")
                st.rerun()
            except Exception as e:
                st.error(str(e))
    st.divider()
    st.subheader("3 · Suppliers")
    sup = db.list_suppliers(engine)
    if len(sup):
        st.dataframe(sup, hide_index=True, width="stretch")
    else:
        st.caption("Suppliers are added automatically the first time you receive from them.")
    st.divider()
    st.subheader("4 · Database")
    st.write("Connected to:", "Local SQLite (testing only)" if LOCAL_MODE else "Supabase / PostgreSQL")


{"Dashboard": page_dashboard, "Stock on Hand": page_stock, "Receive Stock": page_receive,
 "Transfer to Branch": page_transfer, "Returns & Adjustments": page_adjust,
 "Transaction History": page_history, "Products & Batches": page_products,
 "Reports & Export": page_reports, "Setup & Import": page_setup}[page]()
