# PharmacyDirect GH: Warehouse Inventory App, Handoff Notes

> Put this file in the repo root as `CLAUDE.md` (or reference it from there) so Claude Code picks it up automatically.
> Status as of **13 Oct 2026**.

## 1. Context

- **Owner:** Nii (Adotey Nii Acquaye), General Manager, PharmacyDirect GH, Ghana.
- **Business:** multi-branch pharmacy. A new **central warehouse** supplies the retail branches **Adenta** and **Atomic** (more may follow).
- **Goal:** an all-in-one warehouse inventory manager with a **traceable history** of medication quantities. It must cover stock received from overseas and local distributors, transfers out to branches, returns, write-offs and count corrections, and it must be able to **export or snapshot** stock to share with anyone who asks.
- **Starting point:** staff counted the warehouse by hand on 7 paper sheets. Claude transcribed the sheets and Nii checked the result against the shelves. This verified count is the opening stock.

## 2. Key decisions (already agreed)

| Decision | Choice | Why |
|---|---|---|
| App vs spreadsheet | **Streamlit app** | An append-only ledger gives traceability; a spreadsheet can be overwritten silently |
| Data store | **Supabase (PostgreSQL)** through SQLAlchemy | The Streamlit Cloud filesystem is wiped on restart; Postgres gives row locking and concurrent users |
| Hosting | Streamlit Community Cloud, from a **private** GitHub repo | Nii already deploys Streamlit apps there |
| Stock model | **Calculated from movements**, never stored | Every unit can be traced to the transaction that moved it |
| Corrections | **Void**, which posts a reversing transaction; history is never edited or deleted | Audit trail |
| Expiry convention | MM/YY means the **last day of that month** | Pharmacy convention |
| Issuing rule | **FEFO** (first-expiry, first-out) is suggested and warned on, not enforced | Gives flexibility |
| Product naming | Manufacturer kept **inside the product name in brackets**, e.g. `Amlodipine 10mg Tablets (Teva)`; `manufacturer` column left blank | Nii declined splitting it out (it can be revisited) |
| Local dev | No `DATABASE_URL` set means SQLite file `warehouse_local.db` (test mode banner shown) | |

## 3. Codebase

```
warehouse-app/
├── app.py                  Streamlit UI, all pages (~700 lines)
├── db.py                   Schema (SQLAlchemy Core), ledger posting/validation, stock queries, import
├── reports.py              Excel snapshot (xlsxwriter) and transfer-note builder
├── theme.py                Brand CSS (navy/amber, Inter + Sora), shared look with walk-in-demand-tracker
├── assets/logo.png         Pharmacy Direct logo
├── data/opening_stock.csv  Verified opening count (210 lines, 4,900 units)
├── requirements.txt        streamlit>=1.50, pandas, SQLAlchemy>=2, psycopg2-binary, XlsxWriter, openpyxl
├── README.md               Deployment guide (Supabase → GitHub → Streamlit Cloud) and operating rules
├── .gitignore              Excludes secrets.toml, *.db, __pycache__
└── .streamlit/
    ├── config.toml         Theme (brand navy #01528A, same as the walk-in tracker)
    └── secrets.toml.example  DATABASE_URL and a [users] name = "password" table
```

### Database schema (`db.py`, created automatically by `init_db`)

- `branches(id, name UNIQUE, active)`, seeded with Adenta and Atomic
- `suppliers(id, name UNIQUE, kind Local|Overseas, active)`, added automatically on first receipt
- `products(id, name, manufacturer, code, category, unit, reorder_level, active, created_at)`, UNIQUE(name, manufacturer)
- `batches(id, product_id, expiry_date NULLABLE, batch_no, created_at)`, identified by product + expiry + batch_no. NULL expiry is handled in code, because a NULL in a Postgres unique constraint never matches another NULL.
- `transactions(id, txn_no UNIQUE, txn_type, txn_date, counterparty, reference, entered_by, note, created_at, reverses_id, voided_by_id)`
- `movements(id, txn_id, batch_id, qty SIGNED, line_note)`. `qty` is the effect on **warehouse** stock: + means in, − means out.
- `audit_log(id, ts, username, action, details JSON)` records master-data edits, batch corrections and voids.

Transaction types (`db.TXN_TYPES`), with their number prefix and the direction stock moves:

| Type | Prefix | Direction |
|---|---|---|
| OPENING | `OPN` | + |
| RECEIPT | `RCV` | + |
| TRANSFER | `TRF` | − (counterparty = branch) |
| RETURN | `RET` | + |
| WRITE_OFF | `WOF` | − |
| SALE | `WSL` | − (counterparty = wholesale client) |
| SUPPLIER_RETURN | `RTS` | − (counterparty = supplier) |
| ADJUSTMENT | `ADJ` | ± |
| REVERSAL | `REV` | ± |

Transaction numbers look like `TRF-000123`.

### Core logic

- `post_transaction()` is atomic:
  - It locks the affected batch rows (`SELECT … FOR UPDATE` on Postgres).
  - It refuses to let any batch go below zero, raising `StockError` with a readable shortfall message.
  - It checks that the sign of each line matches the transaction type.
- `void_transaction()` posts a REVERSAL dated today, links both ways, and is blocked if:
  - the transaction is already voided;
  - it is itself a reversal;
  - voiding would make stock negative (e.g. voiding a receipt whose stock has already been transferred).
- `stock_by_batch(as_of=…)` sums the movements up to a date, which gives historical snapshots for free. Expiry status bands are Expired, ≤ 3 months, ≤ 6 months, OK and No expiry.
- `import_opening_stock(df)` takes columns `product, qty` plus optional `manufacturer, category, unit, expiry_date|expiry, batch_no, note`. It collects every row error and saves nothing if any row fails.
- `parse_expiry()` accepts MM/YY, MM/YYYY, YYYY-MM, YYYY-MM-DD and DD/MM/YYYY.

### UI pages (`app.py`, sidebar radio)

1. **Dashboard:** KPIs, net units per branch this month, expiry watch, recent activity, items below reorder level.
2. **Stock on Hand:** by product or by batch; search, category and status filters; as-of date; CSV export.
3. **Receive Stock:** supplier (new or existing, Local or Overseas), invoice number, and a `st.data_editor` for lines (product select, expiry typed as MM/YY, batch number, qty). Expired stock is blocked, short-dated stock warned. A confirmation checkbox is required.
4. **Transfer to Branch:** batch select (FEFO-sorted, expired batches excluded, availability shown), overdraw validation, FEFO warning, and an Excel transfer note to download.
5. **Returns & Adjustments:** return from branch, write-off (one click for all expired stock), count correction (enter the counted qty and the app posts the difference).
6. **Transaction History:** filters, detail view, void with a reason, transfer-note re-download.
7. **Products & Batches:** catalogue editor (category, unit, reorder level, active), correct a batch's expiry or batch number (logged), change log.
8. **Reports & Export:** Excel snapshot with these sheets: Summary, Stock by Product, Stock by Batch, Expiry Watch, Transfers by Branch, Transactions, Ledger. Also a branch statement and quick CSVs.
9. **Setup & Import:** opening-stock import (bundled CSV or an uploaded CSV/XLSX; it finds the header row even with a title block above it), add branches, list suppliers, DB status.

**Auth:** if a `[users]` table is in secrets, each person signs in with their own name and password. Otherwise `APP_PASSWORD` gives a shared password plus a typed name. With neither, there is no password (local dev only). The signed-in name is stamped on every transaction.

## 4. Opening stock data

- **Source:** 7 handwritten count sheets. Claude transcribed them; Nii verified against the shelves and edited the Excel file. The final file is `Warehouse_Opening_Stock_Clean.xlsx`, converted to `data/opening_stock.csv`.
- **Totals:** 210 lines, **4,900 units**, 209 products. Donepezil (Jubilant) has two batches, 09/28 × 110 and 06/27 × 2.
- **Nothing is expired** as of 13 Oct 2026. Coartem was confirmed as 09/28 and Evening Primrose 1000mg as 12/28.
- **Nii's verified changes to the first transcription:**
  - Tamsulosin 17 → 22
  - Benylin Cold & Flu 61 → 6
  - Oilatum Junior Bath Additive 110 → 10
  - Oilatum Moisture Rich Bath Soak 300ml expiry 06/29 → 06/28
  - New lines: Furosemide 20mg (50), Furosemide 40mg (58), Tranexamic Acid 500mg (25), Donepezil 06/27 (2), Gliclazide 80mg (8)
- **Clean-ups applied:**
  - Coartem date fixed (was the invalid text "31-Sep-2028")
  - Total formula fixed (it skipped 39 rows)
  - Desloratadine 5mg (Mylan) 09/28 merged into one line of 24
  - Manufacturer spellings standardised (Manx, Milpharm, Hillcross, Brown & Burk)
- **26 lines have no expiry recorded.** Several are medicines, not toiletries: Canesten, Anusol, Betnovate, Daktarin, Epiderm, Aciclovir cream and Aqueous Calamine. They import as "No expiry"; add the dates later through *Correct a batch*.
- **Categories** were assigned by Claude for filtering (19 categories, listed in `db.CATEGORIES`). Nii can edit them in the app.
- Units are `pcs`, except Galvus Met and Flucloxacillin (Fluxacin) 500mg (ECL), which are counted in `strips`.

### Stock-take workbook (added 21 Nov 2026)

- On 17 Nov 2026 the warehouse was recounted. The result is `Wholesale_Warehouse_Inventory_to_Observe.xlsx` (sheets *Stock*, *Movements*, *Movement Types*): 462 products with codes `WH-0001…`, 10,178 counted units, 236 logged movements on 17–18 Nov, **8,580 units current**. It is a different catalogue from the October list (only 12 names match), and it has no categories.
- `db.apply_stocktake()` (Setup & Import → *Apply a stock-take workbook*) switches to it **without deleting history**: it posts one ADJUSTMENT that zeroes the old list and deactivates the old products, then an OPENING of the counted stock dated at the stock check, then replays the Movements sheet as RCV/TRF/WSL/RET/WOF/ADJ transactions. It is all-or-nothing, checks every product against the sheet's *Current Stock*, and refuses a second run with the same reference.
- `products.code` was added by an additive migration in `init_db` (ALTER TABLE … ADD COLUMN).
- The October list in `data/opening_stock.csv` is now historical.

## 5. Testing done

- Data layer tested on SQLite and on **PostgreSQL 16**: import, receipt, transfer, overdraw blocked, void, double-void blocked, void that would go negative blocked, batch correction, Excel snapshot and transfer note.
- Streamlit `AppTest` smoke test: all 9 pages render with no exceptions, the opening-stock import works through the UI, and the snapshot builds.
- Visual check with Playwright screenshots of the Dashboard and Transfer pages.
- **Not yet tested:**
  - entering lines in the `st.data_editor` grids (Receive and Transfer) by real clicks, since AppTest can't drive them;
  - concurrent multi-user posting against a live Supabase database;
  - the real Supabase pooler connection.

## 6. Deployment status

**Not deployed yet.** Steps (full detail in README):

1. Create a Supabase project and copy the **Session pooler** URI (port 5432). Don't use the direct connection, which is IPv6-only and fails on Streamlit Cloud.
2. Push to a private GitHub repo.
3. Create the app on Streamlit Cloud with main file `app.py`.
4. In Secrets, set `DATABASE_URL` and a `[users]` table.
5. In the app, go to Setup & Import, set the count date and click **Import as opening stock**. The import runs once; to redo it, void the OPN transaction first.

## 7. Suggested next steps / backlog

**Priority**
- [ ] Deploy and run the first real receipt and transfer end to end.
- [ ] Hands-on test of the data-editor flows (Receive and Transfer). Check the NA and dtype handling of the `Qty` (Int64) and selectbox columns.
- [ ] Decide whether to split the manufacturer out of product names (better search and grouping). It would need a data migration if done after go-live.

**Nice to have**
- [ ] Branch-side confirmation of receipt, so a transfer is "in transit" until the branch acknowledges it.
- [ ] PDF transfer note (currently Excel only).
- [ ] Reorder suggestions based on transfer velocity per branch.
- [ ] Barcode/GTIN field on products for scanning.
- [ ] Cost or price per batch, for stock valuation.
- [ ] Role-based permissions (e.g. only managers can void or write off).
- [ ] Scheduled snapshot emails.
- [ ] Unit/pack conversions (pcs vs strips vs packs).
- [ ] Tests folder with pytest for `db.py`. The ad-hoc test scripts used so far were not kept in the repo.

## 8. Conventions for further work

- Keep **all stock changes going through `post_transaction()`**. Never write to `movements` directly and never UPDATE a quantity.
- Never delete transactions. Corrections are always reversals.
- Log master-data edits through `db.log()` into `audit_log`.
- Keep SQL portable between SQLite (local) and Postgres (prod) by using SQLAlchemy Core only.
- Use `width="stretch"` instead of the deprecated `use_container_width`.
- Avoid Streamlit "magic": a bare expression statement, such as a one-line ternary that calls `st.*`, breaks it.

## 9. Related but separate task (paused)

The Adenta branch stock-taking spreadsheets are a separate job, waiting to resume:

- **Folder:** `…\PharmacyDirect GH Docs\Adenta Stock-taking 28 April List` (13 xlsx files, including `Master_Inventory_List.xlsx`).
- **Task:** once Nii uploads the recent invoice images (not uploaded yet; the 7 photos in this session were the warehouse count), use them to:
  - void expired entries;
  - add the new pen-written expiry dates;
  - add missing items to the right category file and to the Master list;
  - flag duplicates.
- **Agreed defaults:**
  - void means delete the row;
  - save updated copies beside the originals;
  - produce a change log;
  - flag illegible entries instead of guessing.

This is unrelated to the warehouse app codebase.
