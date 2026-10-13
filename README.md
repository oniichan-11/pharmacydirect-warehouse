# PharmacyDirect GH: Warehouse Inventory Manager

A Streamlit app for the central warehouse that feeds the Adenta and Atomic branches.

Every stock movement is recorded as a dated, signed transaction:

- opening count
- receipt from a supplier
- transfer to a branch
- return from a branch
- write-off
- count adjustment

Each transaction carries who entered it and a reference number. Stock on hand is always *calculated* from these entries and never typed in, so any quantity can be traced back to where it came from. Nothing is ever deleted. To correct a mistake, you **void** it, which posts an equal and opposite entry and leaves the original visible as VOIDED.

## What it does

| Page | Purpose |
|---|---|
| Dashboard | Units on hand, expired and short-dated batches, items below reorder level, units sent to each branch this month, recent activity |
| Stock on Hand | Search and filter by product, category and expiry status. The **as-of date** shows the stock position on any past day |
| Receive Stock | Record arrivals from overseas or local suppliers: invoice no., one line per product and expiry. Expired stock is blocked and short-dated stock triggers a warning |
| Transfer to Branch | Issue stock to a branch, earliest expiry first (FEFO). The app won't send more than is available or send expired stock. It produces a signable **Transfer Note** (Excel) |
| Returns & Adjustments | Returns from branches, write-offs (one-click for all expired batches), and physical-count corrections (enter the counted qty and the app posts the difference) |
| Transaction History | Filter, inspect and void transactions, and re-download transfer notes |
| Products & Batches | Edit categories, units and reorder levels. Correct a misread expiry or batch no. (logged). Change log |
| Reports & Export | One-click Excel snapshot (Summary, Stock by Product, Stock by Batch, Expiry Watch, Transfers by Branch, Transactions, Ledger), a statement per branch, and CSVs |
| Setup & Import | Load the opening count, add branches, view suppliers |

## Deploy (about 15 minutes)

### 1. Create the database (Supabase, free tier)

1. Sign up at supabase.com and create a **New project**. Pick region *West EU (London)* and save the database password.
2. Open **Connect** (top bar) and choose **Session pooler**. Copy the URI. It looks like
   `postgresql://postgres.abcd1234:[YOUR-PASSWORD]@aws-0-eu-west-2.pooler.supabase.com:5432/postgres`
   and you replace `[YOUR-PASSWORD]` with the password you saved.
   Use the *pooler* string, not the "Direct connection" one. Streamlit Cloud can't reach the direct one.

The app creates its own tables the first time it connects. There's no SQL to run.

### 2. Put the code on GitHub

Create a **private** repository and upload everything in this folder except `.streamlit/secrets.toml` (the `.gitignore` already excludes it).

### 3. Deploy on Streamlit Community Cloud

1. On share.streamlit.io, click **Create app**, pick the repo, and set the main file to `app.py`.
2. Under **Advanced settings → Secrets**, paste the contents of `.streamlit/secrets.toml.example` with your real `DATABASE_URL` and one name/password per staff member.
3. Click Deploy, sign in, go to **Setup & Import**, set the count date, and press **Import as opening stock**.

## Before you import the opening stock

`data/opening_stock.csv` holds the verified opening count: **210 lines, 4,900 units**, checked against the shelves (Oct 2026). Product names carry the manufacturer in brackets, e.g. *Amlodipine 10mg Tablets (Teva)*. Categories were assigned for filtering and can be changed under Products & Batches.

26 lines have no expiry date recorded, including Canesten, Anusol, Betnovate, Daktarin, Epiderm, Aciclovir cream and Aqueous Calamine. They import as "No expiry" and won't trigger expiry alerts. Add their dates with Products & Batches → *Correct a batch* at the next shelf check.

## Run locally (optional)

```bash
pip install -r requirements.txt
streamlit run app.py
```

With no `DATABASE_URL` set, the app runs in **local test mode** on a SQLite file (`warehouse_local.db`). That's fine for trying it out, but don't use it for real records on Streamlit Cloud, because the file is wiped whenever the app restarts.

## Day-to-day rules that keep the numbers right

1. Every box that leaves goes through **Transfer to Branch**, and the printed note is signed by both sides.
2. Enter receipts against the supplier invoice *after* physically counting the goods.
3. Do a monthly cycle count of a few shelves using **Stock count correction**. Unexplained differences show up in the ledger.
4. Write off expired stock as soon as it shows red on the dashboard.
5. Never share logins. The name on each entry is the audit trail.

## Files

```
app.py          Streamlit UI (all pages)
db.py           Database schema, ledger logic, stock calculations, validation
reports.py      Excel snapshot and transfer-note builders
data/opening_stock.csv   Transcribed opening count
requirements.txt
.streamlit/config.toml, secrets.toml.example
```
