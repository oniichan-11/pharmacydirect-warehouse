"""Excel exports: full inventory snapshot and printable transfer notes."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd

import db

COMPANY = "PharmacyDirect GH — Central Warehouse"

STATUS_FILL = {"Expired": "#F4C7C3", "≤ 3 months": "#FCE8B2", "≤ 6 months": "#FFF5D6"}


def _write(ws_name, df, writer, title, subtitle, widths=None, status_col=None):
    df = df.copy()
    df.to_excel(writer, sheet_name=ws_name, startrow=3, index=False)
    wb, ws = writer.book, writer.sheets[ws_name]
    bold = wb.add_format({"bold": True, "font_size": 13, "font_name": "Arial"})
    small = wb.add_format({"italic": True, "font_color": "#555555", "font_name": "Arial", "font_size": 9})
    head = wb.add_format({"bold": True, "bg_color": "#1F4E5A", "font_color": "white", "border": 1,
                          "font_name": "Arial", "text_wrap": True, "valign": "vcenter"})
    ws.write(0, 0, title, bold)
    ws.write(1, 0, subtitle, small)
    for i, col in enumerate(df.columns):
        ws.write(3, i, col, head)
        w = (widths or {}).get(col) or min(max(len(str(col)), *(len(str(v)) for v in df[col].head(300))) + 2
                                            if len(df) else len(str(col)) + 2, 60)
        ws.set_column(i, i, w)
    ws.freeze_panes(4, 0)
    if len(df):
        ws.autofilter(3, 0, 3 + len(df), len(df.columns) - 1)
    if status_col and status_col in df.columns and len(df):
        ci = list(df.columns).index(status_col)
        for status, color in STATUS_FILL.items():
            ws.conditional_format(4, 0, 3 + len(df), len(df.columns) - 1, {
                "type": "formula",
                "criteria": f'=${chr(65 + ci)}5="{status}"',
                "format": wb.add_format({"bg_color": color})})


def snapshot_xlsx(engine, as_of: dt.date, start: dt.date, end: dt.date, user: str) -> bytes:
    stamp = f"Generated {dt.datetime.now():%d %b %Y %H:%M} by {user}"
    bdf = db.stock_by_batch(engine, as_of=as_of)
    pdf = db.stock_by_product(bdf)
    led = db.ledger(engine, start, end)
    summ = db.transactions_summary(led)
    tb = db.transfers_by_branch(led)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", date_format="dd-mmm-yyyy",
                        datetime_format="dd-mmm-yyyy hh:mm") as w:
        # Summary
        s = pd.DataFrame([
            ("Stock position as of", as_of.strftime("%d %b %Y")),
            ("Products in stock", int((pdf["on_hand"] > 0).sum()) if len(pdf) else 0),
            ("Batches in stock", int((bdf["on_hand"] > 0).sum()) if len(bdf) else 0),
            ("Total units on hand", int(bdf["on_hand"].sum()) if len(bdf) else 0),
            ("Units already expired (to write off)", int(bdf.loc[bdf.status == "Expired", "on_hand"].sum())),
            ("Units expiring within 3 months", int(bdf.loc[bdf.status == "≤ 3 months", "on_hand"].sum())),
            ("Units expiring in 3–6 months", int(bdf.loc[bdf.status == "≤ 6 months", "on_hand"].sum())),
            ("Activity period", f"{start:%d %b %Y} – {end:%d %b %Y}"),
            ("Transactions in period", len(summ)),
        ] + [(f"Net units sent to {b} (period)", int(g["net_to_branch"].sum()))
             for b, g in (tb.groupby("branch") if len(tb) else [])], columns=["Item", "Value"])
        _write("Summary", s, w, COMPANY, stamp, widths={"Item": 42, "Value": 26})

        p = pdf.rename(columns={"code": "Code", "name": "Product", "manufacturer": "Manufacturer",
                                "category": "Category", "unit": "Unit", "on_hand": "On hand", "batches": "Batches",
                                "earliest_expiry": "Earliest expiry", "expired_qty": "Of which expired",
                                "reorder_level": "Reorder level", "low_stock": "Low stock"})
        p = p.drop(columns=["product_id"], errors="ignore")
        if "Low stock" in p:
            p["Low stock"] = p["Low stock"].map({True: "YES", False: ""})
        _write("Stock by Product", p, w, "Stock on hand by product", f"As of {as_of:%d %b %Y}. {stamp}",
               widths={"Product": 46})

        b = bdf[["code", "name", "manufacturer", "category", "expiry_date", "days_left", "status", "batch_no",
                 "on_hand", "unit"]].rename(columns={"code": "Code", "name": "Product",
                                                     "manufacturer": "Manufacturer", "category": "Category",
                                          "expiry_date": "Expiry", "days_left": "Days left", "status": "Status",
                                          "batch_no": "Batch no.", "on_hand": "On hand", "unit": "Unit"})
        _write("Stock by Batch", b, w, "Stock on hand by batch (expiry)", f"As of {as_of:%d %b %Y}. {stamp}",
               widths={"Product": 46}, status_col="Status")

        e = b[b["Status"].isin(["Expired", "≤ 3 months", "≤ 6 months"])].sort_values("Expiry")
        _write("Expiry Watch", e, w, "Expired and short-dated stock", "Expired = write off; ≤ 3 months = push "
               "to branches first (FEFO).", widths={"Product": 46}, status_col="Status")

        if len(tb):
            t = tb.rename(columns={"branch": "Branch", "product": "Product", "unit": "Unit", "sent": "Sent",
                                   "returned": "Returned", "net_to_branch": "Net to branch"})
        else:
            t = pd.DataFrame(columns=["Branch", "Product", "Unit", "Sent", "Returned", "Net to branch"])
        _write("Transfers by Branch", t, w, "Units sent to branches", f"{start:%d %b %Y} – {end:%d %b %Y}, "
               "voided transfers excluded", widths={"Product": 46})

        ts = summ.drop(columns=["txn_id"]).rename(columns={
            "txn_no": "Txn no.", "txn_date": "Date", "txn_type": "Type", "counterparty": "Supplier / branch",
            "reference": "Reference", "entered_by": "Entered by", "voided": "Voided", "note": "Note",
            "lines": "Lines", "units": "Net units"})
        if len(ts):
            ts["Voided"] = ts["Voided"].map({True: "VOIDED", False: ""})
        _write("Transactions", ts, w, "Transaction register", f"{start:%d %b %Y} – {end:%d %b %Y}")

        L = led[["txn_no", "txn_date", "txn_type", "counterparty", "reference", "entered_by", "name",
                 "manufacturer", "expiry_date", "batch_no", "qty", "unit", "voided", "line_note"]].rename(columns={
            "txn_no": "Txn no.", "txn_date": "Date", "txn_type": "Type", "counterparty": "Supplier / branch",
            "reference": "Reference", "entered_by": "Entered by", "name": "Product", "manufacturer": "Manufacturer",
            "expiry_date": "Expiry", "batch_no": "Batch no.", "qty": "Qty (+in / −out)", "unit": "Unit",
            "voided": "Voided", "line_note": "Line note"})
        if len(L):
            L["Voided"] = L["Voided"].map({True: "VOIDED", False: ""})
        _write("Ledger", L, w, "Full movement ledger", f"{start:%d %b %Y} – {end:%d %b %Y}. Positive = into "
               "warehouse, negative = out of warehouse.", widths={"Product": 40})
    return buf.getvalue()


def transfer_note_xlsx(engine, txn_no: str) -> bytes:
    led = db.ledger(engine)
    d = led[led["txn_no"] == txn_no]
    if d.empty:
        raise ValueError("Transfer not found")
    h = d.iloc[0]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        wb = w.book
        ws = wb.add_worksheet("Transfer Note")
        f = lambda **k: wb.add_format({"font_name": "Arial", **k})
        title, lab, val = f(bold=True, font_size=14), f(bold=True), f()
        head = f(bold=True, bg_color="#1F4E5A", font_color="white", border=1)
        cell, num = f(border=1), f(border=1, align="center")
        datef = f(border=1, num_format="dd-mmm-yyyy", align="center")
        ws.set_column(0, 0, 5); ws.set_column(1, 1, 48); ws.set_column(2, 2, 18)
        ws.set_column(3, 4, 14); ws.set_column(5, 6, 12)
        ws.write(0, 1, COMPANY, title)
        ws.write(1, 1, "STOCK TRANSFER NOTE", lab)
        meta = [("Transfer no.", h["txn_no"]), ("Date", pd.Timestamp(h["txn_date"]).strftime("%d %b %Y")),
                ("To branch", h["counterparty"]), ("Reference", h["reference"] or "—"),
                ("Dispatched by", h["entered_by"]), ("Note", h["note"] or "—")]
        for i, (k, v) in enumerate(meta):
            ws.write(3 + i, 1, k, lab); ws.write(3 + i, 2, v, val)
        r0 = 10
        for j, c in enumerate(["#", "Product", "Manufacturer", "Expiry", "Batch no.", "Qty", "Unit"]):
            ws.write(r0, j, c, head)
        for i, (_, r) in enumerate(d.iterrows(), start=1):
            ws.write(r0 + i, 0, i, num)
            ws.write(r0 + i, 1, r["name"], cell)
            ws.write(r0 + i, 2, r["manufacturer"], cell)
            if r["expiry_date"] is not None and not pd.isna(r["expiry_date"]):
                ws.write_datetime(r0 + i, 3, pd.Timestamp(r["expiry_date"]).to_pydatetime(), datef)
            else:
                ws.write(r0 + i, 3, "—", num)
            ws.write(r0 + i, 4, r["batch_no"] or "", cell)
            ws.write(r0 + i, 5, abs(int(r["qty"])), num)
            ws.write(r0 + i, 6, r["unit"], cell)
        end = r0 + len(d)
        ws.write(end + 1, 4, "Total units", lab)
        ws.write_formula(end + 1, 5, f"=SUM(F{r0 + 2}:F{end + 1})", num, int(d["qty"].abs().sum()))
        for i, k in enumerate(["Dispatched by (warehouse)", "Received by (branch)", "Checked by"]):
            ws.write(end + 4 + i * 2, 1, f"{k}: ______________________   Signature: __________   Date: ________", val)
        ws.fit_to_pages(1, 0)
        ws.set_landscape()
    return buf.getvalue()
