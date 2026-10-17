"""Data layer for the PharmacyDirect GH warehouse inventory app.

Design: an append-only ledger.
  * products  -> what an item is
  * batches   -> a product + expiry date (+ optional batch number)
  * transactions + movements -> every stock change, with who / when / why
Stock on hand is never stored; it is always the sum of movements, so every
unit can be traced back to the receipt, transfer or adjustment that moved it.
Mistakes are corrected by voiding (a reversing transaction), never by editing
or deleting history.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from urllib.parse import unquote

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, func, select)

metadata = MetaData()

branches = Table(
    "branches", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("active", Boolean, nullable=False, default=True),
)

suppliers = Table(
    "suppliers", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(150), nullable=False, unique=True),
    Column("kind", String(20), nullable=False, default="Local"),  # Local / Overseas
    Column("active", Boolean, nullable=False, default=True),
)

products = Table(
    "products", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("manufacturer", String(120), nullable=False, default=""),
    Column("category", String(80), nullable=False, default="Other"),
    Column("unit", String(30), nullable=False, default="pcs"),
    Column("reorder_level", Integer, nullable=False, default=0),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False, default=dt.datetime.utcnow),
    UniqueConstraint("name", "manufacturer", name="uq_product_name_mfr"),
)

batches = Table(
    "batches", metadata,
    Column("id", Integer, primary_key=True),
    Column("product_id", Integer, ForeignKey("products.id"), nullable=False, index=True),
    Column("expiry_date", Date, nullable=True),
    Column("batch_no", String(60), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=dt.datetime.utcnow),
)

transactions = Table(
    "transactions", metadata,
    Column("id", Integer, primary_key=True),
    Column("txn_no", String(30), unique=True),
    Column("txn_type", String(20), nullable=False, index=True),
    Column("txn_date", Date, nullable=False, index=True),
    Column("counterparty", String(150), nullable=False, default=""),  # supplier or branch
    Column("reference", String(100), nullable=False, default=""),     # invoice / transfer note no.
    Column("entered_by", String(80), nullable=False, default=""),
    Column("note", Text, nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=dt.datetime.utcnow),
    Column("reverses_id", Integer, ForeignKey("transactions.id"), nullable=True),
    Column("voided_by_id", Integer, ForeignKey("transactions.id"), nullable=True),
)

movements = Table(
    "movements", metadata,
    Column("id", Integer, primary_key=True),
    Column("txn_id", Integer, ForeignKey("transactions.id"), nullable=False, index=True),
    Column("batch_id", Integer, ForeignKey("batches.id"), nullable=False, index=True),
    Column("qty", Integer, nullable=False),  # signed effect on WAREHOUSE stock
    Column("line_note", String(200), nullable=False, default=""),
)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", DateTime, nullable=False, default=dt.datetime.utcnow),
    Column("username", String(80), nullable=False, default=""),
    Column("action", String(60), nullable=False),
    Column("details", Text, nullable=False, default=""),
)

# txn_type -> (prefix, label, direction). direction +1 adds to warehouse, -1 removes, 0 either.
TXN_TYPES = {
    "OPENING": ("OPN", "Opening stock", +1),
    "RECEIPT": ("RCV", "Receipt from supplier", +1),
    "TRANSFER": ("TRF", "Transfer to branch", -1),
    "RETURN": ("RET", "Return from branch", +1),
    "WRITE_OFF": ("WOF", "Write-off", -1),
    "ADJUSTMENT": ("ADJ", "Count adjustment", 0),
    "REVERSAL": ("REV", "Void / reversal", 0),
}

DEFAULT_BRANCHES = ["Adenta", "Atomic"]
CATEGORIES = [
    "Anti-infectives", "Antimalarials", "Cardiovascular", "CNS & Psychiatry", "Diabetes", "Endocrine",
    "Gastrointestinal", "Cough, Cold & Respiratory", "Allergy", "Eye, Ear & Nose", "Pain & Musculoskeletal",
    "Vitamins & Supplements", "Dermatology & Topicals", "Men's Health & Urology", "Women's Health",
    "Oncology", "Personal Care", "Devices & Dressings", "Baby & Mother", "Other",
]
UNITS = ["pcs", "packs", "strips", "bottles", "tubes", "boxes", "tins", "sachets"]


class StockError(ValueError):
    """Raised when a transaction would make stock negative or is otherwise invalid."""


# --------------------------------------------------------------------------- setup
def parse_pg_url(url: str) -> sa.URL:
    """Parse a postgres URL tolerantly: the password may contain unescaped @ : / # ? characters."""
    url = url.strip().strip('"').strip("'")
    scheme, sep, rest = url.partition("://")
    if not sep or not scheme.startswith("postgres"):
        raise ValueError("DATABASE_URL must start with postgresql://")
    creds, at, hostpart = rest.rpartition("@")
    if not at:
        raise ValueError("DATABASE_URL has no user:password@ part")
    user, _, password = creds.partition(":")
    hostport, _, dbname = hostpart.partition("/")
    dbname = dbname.split("?")[0] or "postgres"
    host, _, port = hostport.partition(":")
    return sa.URL.create("postgresql+psycopg2", username=unquote(user), password=unquote(password),
                         host=host, port=int(port) if port else 5432, database=dbname)


def describe_db_url(url: str) -> dict:
    """Connection details without the password, plus likely-misconfiguration hints."""
    try:
        u = parse_pg_url(url)
    except Exception as e:
        return {"problem": str(e)}
    hints = []
    if u.host and u.host.startswith("db.") and u.host.endswith(".supabase.co"):
        hints.append("This is Supabase's *Direct connection* host (IPv6-only). Use the **Session pooler** URI "
                     "(host like aws-0-<region>.pooler.supabase.com).")
    if "pooler.supabase.com" in (u.host or "") and "." not in (u.username or ""):
        hints.append("Pooler usernames look like `postgres.<project-ref>`, not just `postgres`.")
    if u.port == 6543:
        hints.append("Port 6543 is the Transaction pooler; use the Session pooler (port 5432).")
    ref = (u.username or "").partition(".")[2]
    if ref.lower() in {"xxxx", "abcd1234", "your-project-ref", "project-ref"} or ref.startswith("["):
        hints.append(f"`{u.username}` is a placeholder. Use the username from Supabase → Connect → Session pooler: "
                     "`postgres.` followed by your ~20-character project ref.")
    if (not u.password or u.password.upper() in {"PASSWORD", "YOURPASSWORD", "YOUR-DB-PASSWORD"}
            or "YOUR-PASSWORD" in u.password.upper() or u.password.startswith("[")):
        hints.append("The password still looks like the placeholder; replace it with your database password "
                     "(no brackets).")
    return {"host": u.host, "port": u.port, "user": u.username, "database": u.database,
            "password_length": len(u.password or ""), "hints": hints}


def make_engine(url: str) -> sa.Engine:
    kw = {"pool_pre_ping": True}
    if url.startswith("postgres"):
        url = parse_pg_url(url)
        kw.update(pool_size=3, max_overflow=2, pool_recycle=300,
                  connect_args={"sslmode": "require", "connect_timeout": 10})
    return sa.create_engine(url, **kw)


def init_db(engine: sa.Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as c:
        if c.execute(select(func.count()).select_from(branches)).scalar() == 0:
            c.execute(branches.insert(), [{"name": b, "active": True} for b in DEFAULT_BRANCHES])


# --------------------------------------------------------------------------- helpers
def parse_expiry(value) -> dt.date | None:
    """Accepts MM/YY, MM/YYYY, YYYY-MM, YYYY-MM-DD, DD/MM/YYYY, dates. Month-only -> last day of month."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "nat", "-", "n/a"}:
        return None

    def month_end(y, m):
        if y < 100:
            y += 2000
        if not (1 <= m <= 12) or not (2000 <= y <= 2100):
            raise ValueError
        return dt.date(y, m, calendar.monthrange(y, m)[1])

    try:
        if m := re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{2}|\d{4})", s):
            return month_end(int(m.group(2)), int(m.group(1)))
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})", s):
            return month_end(int(m.group(1)), int(m.group(2)))
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s):
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        pass
    raise ValueError(f"Can't read expiry '{s}'. Use MM/YY, e.g. 09/28")


def expiry_status(exp, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    if exp is None or pd.isna(exp):
        return "No expiry"
    exp = pd.Timestamp(exp).date()
    days = (exp - today).days
    if days < 0:
        return "Expired"
    if days <= 90:
        return "≤ 3 months"
    if days <= 180:
        return "≤ 6 months"
    return "OK"


def product_label(name: str, manufacturer: str) -> str:
    return f"{name} — {manufacturer}" if manufacturer else name


def log(conn, user: str, action: str, details) -> None:
    if not isinstance(details, str):
        details = json.dumps(details, default=str)
    conn.execute(audit_log.insert().values(ts=dt.datetime.utcnow(), username=user, action=action, details=details))


# --------------------------------------------------------------------------- master data
def list_branches(engine, active_only=True) -> list[str]:
    q = select(branches.c.name).order_by(branches.c.name)
    if active_only:
        q = q.where(branches.c.active.is_(True))
    with engine.connect() as c:
        return [r[0] for r in c.execute(q)]


def add_branch(engine, name: str, user: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Branch name is empty")
    with engine.begin() as c:
        if c.execute(select(branches.c.id).where(func.lower(branches.c.name) == name.lower())).first():
            raise ValueError(f"Branch '{name}' already exists")
        c.execute(branches.insert().values(name=name, active=True))
        log(c, user, "ADD_BRANCH", {"name": name})


def list_suppliers(engine) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(select(suppliers).order_by(suppliers.c.name), c)


def ensure_supplier(conn, name: str, kind: str) -> None:
    name = name.strip()
    if not name:
        return
    if not conn.execute(select(suppliers.c.id).where(func.lower(suppliers.c.name) == name.lower())).first():
        conn.execute(suppliers.insert().values(name=name, kind=kind or "Local", active=True))


def list_products(engine, active_only=False) -> pd.DataFrame:
    q = select(products).order_by(products.c.name, products.c.manufacturer)
    if active_only:
        q = q.where(products.c.active.is_(True))
    with engine.connect() as c:
        df = pd.read_sql(q, c)
    df["label"] = [product_label(n, m) for n, m in zip(df["name"], df["manufacturer"])]
    return df


def _find_product(conn, name: str, manufacturer: str):
    return conn.execute(
        select(products.c.id).where(func.lower(products.c.name) == name.strip().lower(),
                                    func.lower(products.c.manufacturer) == (manufacturer or "").strip().lower())
    ).scalar()


def get_or_create_product(conn, name, manufacturer="", category="Other", unit="pcs", reorder_level=0,
                          user="") -> int:
    name = (name or "").strip()
    manufacturer = (manufacturer or "").strip()
    if not name:
        raise ValueError("Product name is empty")
    pid = _find_product(conn, name, manufacturer)
    if pid:
        return pid
    pid = conn.execute(products.insert().values(
        name=name, manufacturer=manufacturer, category=category or "Other", unit=unit or "pcs",
        reorder_level=int(reorder_level or 0), active=True, created_at=dt.datetime.utcnow(),
    )).inserted_primary_key[0]
    log(conn, user, "ADD_PRODUCT", {"id": pid, "name": name, "manufacturer": manufacturer})
    return pid


def add_product(engine, user, **kw) -> int:
    with engine.begin() as c:
        if _find_product(c, kw.get("name", ""), kw.get("manufacturer", "")):
            raise ValueError("That product (same name and manufacturer) already exists")
        return get_or_create_product(c, user=user, **kw)


def update_product(engine, pid: int, changes: dict, user: str) -> None:
    allowed = {"name", "manufacturer", "category", "unit", "reorder_level", "active"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if not changes:
        return
    with engine.begin() as c:
        old = c.execute(select(products).where(products.c.id == pid)).mappings().first()
        c.execute(products.update().where(products.c.id == pid).values(**changes))
        log(c, user, "EDIT_PRODUCT", {"id": pid, "before": {k: old[k] for k in changes}, "after": changes})


def get_or_create_batch(conn, product_id: int, expiry: dt.date | None, batch_no: str = "") -> int:
    batch_no = (batch_no or "").strip()
    cond = [batches.c.product_id == product_id, batches.c.batch_no == batch_no]
    cond.append(batches.c.expiry_date.is_(None) if expiry is None else batches.c.expiry_date == expiry)
    bid = conn.execute(select(batches.c.id).where(*cond)).scalar()
    if bid:
        return bid
    return conn.execute(batches.insert().values(
        product_id=product_id, expiry_date=expiry, batch_no=batch_no, created_at=dt.datetime.utcnow()
    )).inserted_primary_key[0]


def correct_batch(engine, batch_id: int, expiry: dt.date | None, batch_no: str, reason: str, user: str) -> None:
    """Fix a mis-recorded expiry / batch number. Logged with before/after values."""
    if not reason.strip():
        raise ValueError("A reason is required")
    with engine.begin() as c:
        old = c.execute(select(batches).where(batches.c.id == batch_id)).mappings().first()
        clash = c.execute(select(batches.c.id).where(
            batches.c.product_id == old["product_id"], batches.c.id != batch_id,
            batches.c.batch_no == (batch_no or "").strip(),
            batches.c.expiry_date.is_(None) if expiry is None else batches.c.expiry_date == expiry)).first()
        if clash:
            raise ValueError("Another batch of this product already has that expiry and batch number")
        c.execute(batches.update().where(batches.c.id == batch_id).values(expiry_date=expiry,
                                                                           batch_no=(batch_no or "").strip()))
        log(c, user, "CORRECT_BATCH", {"batch_id": batch_id, "reason": reason,
                                       "before": {"expiry": old["expiry_date"], "batch_no": old["batch_no"]},
                                       "after": {"expiry": expiry, "batch_no": batch_no}})


# --------------------------------------------------------------------------- stock queries
def stock_by_batch(engine, as_of: dt.date | None = None, include_zero=False) -> pd.DataFrame:
    j = movements.join(transactions, movements.c.txn_id == transactions.c.id)
    sub = select(movements.c.batch_id, func.sum(movements.c.qty).label("on_hand")).select_from(j)
    if as_of:
        sub = sub.where(transactions.c.txn_date <= as_of)
    sub = sub.group_by(movements.c.batch_id).subquery()
    q = (select(batches.c.id.label("batch_id"), products.c.id.label("product_id"), products.c.name,
                products.c.manufacturer, products.c.category, products.c.unit, products.c.reorder_level,
                batches.c.expiry_date, batches.c.batch_no, func.coalesce(sub.c.on_hand, 0).label("on_hand"))
         .select_from(batches.join(products, batches.c.product_id == products.c.id)
                      .outerjoin(sub, sub.c.batch_id == batches.c.id)))
    with engine.connect() as c:
        df = pd.read_sql(q, c)
    df["on_hand"] = df["on_hand"].astype(int)
    if not include_zero:
        df = df[df["on_hand"] != 0]
    df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date
    today = as_of or dt.date.today()
    df["status"] = [expiry_status(e, today) for e in df["expiry_date"]]
    df["days_left"] = [None if (e is None or pd.isna(e)) else (e - today).days for e in df["expiry_date"]]
    df["label"] = [product_label(n, m) for n, m in zip(df["name"], df["manufacturer"])]
    return df.sort_values(["name", "manufacturer", "expiry_date"], na_position="last").reset_index(drop=True)


def stock_by_product(batch_df: pd.DataFrame) -> pd.DataFrame:
    if batch_df.empty:
        return pd.DataFrame(columns=["name", "manufacturer", "category", "unit", "on_hand", "batches",
                                     "earliest_expiry", "expired_qty", "reorder_level", "low_stock"])
    d = batch_df.copy()
    d["expired_qty"] = d["on_hand"].where(d["status"] == "Expired", 0)
    g = (d.groupby(["product_id", "name", "manufacturer", "category", "unit", "reorder_level"], dropna=False)
         .agg(on_hand=("on_hand", "sum"), batches=("batch_id", "count"),
              earliest_expiry=("expiry_date", lambda s: min([x for x in s if x is not None and not pd.isna(x)],
                                                            default=None)),
              expired_qty=("expired_qty", "sum"))
         .reset_index())
    g["low_stock"] = (g["reorder_level"] > 0) & (g["on_hand"] - g["expired_qty"] <= g["reorder_level"])
    return g.sort_values(["name", "manufacturer"]).reset_index(drop=True)


def batch_available(conn, batch_ids) -> dict[int, int]:
    if not batch_ids:
        return {}
    rows = conn.execute(select(movements.c.batch_id, func.coalesce(func.sum(movements.c.qty), 0))
                        .where(movements.c.batch_id.in_(list(batch_ids))).group_by(movements.c.batch_id))
    out = {int(b): 0 for b in batch_ids}
    out.update({int(b): int(q) for b, q in rows})
    return out


# --------------------------------------------------------------------------- posting
def post_transaction(engine, txn_type: str, txn_date: dt.date, lines: list[dict], user: str,
                     counterparty: str = "", reference: str = "", note: str = "", reverses_id: int | None = None,
                     conn=None) -> str:
    """lines: [{"batch_id": int, "qty": signed int, "line_note": str}]. Returns txn_no.
    Validates that no batch goes below zero. Runs atomically."""
    if txn_type not in TXN_TYPES:
        raise ValueError(f"Unknown transaction type {txn_type}")
    lines = [l for l in lines if int(l.get("qty") or 0) != 0]
    if not lines:
        raise StockError("No lines with a quantity to post")
    if not user:
        raise ValueError("User name missing")
    direction = TXN_TYPES[txn_type][2]
    for l in lines:
        if direction and (l["qty"] > 0) != (direction > 0):
            raise StockError(f"{txn_type} lines must be {'positive' if direction > 0 else 'negative'}")

    def _do(c):
        ids = sorted({int(l["batch_id"]) for l in lines})
        # lock the batch rows (Postgres) so two users can't over-issue the same batch concurrently
        c.execute(select(batches.c.id).where(batches.c.id.in_(ids)).with_for_update())
        avail = batch_available(c, ids)
        delta: dict[int, int] = {}
        for l in lines:
            delta[int(l["batch_id"])] = delta.get(int(l["batch_id"]), 0) + int(l["qty"])
        short = []
        for b, d in delta.items():
            if avail.get(b, 0) + d < 0:
                r = c.execute(select(products.c.name, products.c.manufacturer, batches.c.expiry_date)
                              .select_from(batches.join(products)).where(batches.c.id == b)).first()
                short.append(f"{product_label(r[0], r[1])} (exp {r[2] or 'n/a'}): available {avail.get(b, 0)}, "
                             f"requested {-d}")
        if short:
            raise StockError("Not enough stock:\n" + "\n".join(short))
        tid = c.execute(transactions.insert().values(
            txn_type=txn_type, txn_date=txn_date, counterparty=counterparty or "", reference=reference or "",
            entered_by=user, note=note or "", created_at=dt.datetime.utcnow(), reverses_id=reverses_id,
        )).inserted_primary_key[0]
        txn_no = f"{TXN_TYPES[txn_type][0]}-{tid:06d}"
        c.execute(transactions.update().where(transactions.c.id == tid).values(txn_no=txn_no))
        c.execute(movements.insert(), [{"txn_id": tid, "batch_id": int(l["batch_id"]), "qty": int(l["qty"]),
                                        "line_note": (l.get("line_note") or "")[:200]} for l in lines])
        return txn_no, tid

    if conn is not None:
        return _do(conn)[0]
    with engine.begin() as c:
        return _do(c)[0]


def receive_stock(engine, txn_date, supplier, supplier_kind, reference, note, items: list[dict], user,
                  txn_type="RECEIPT") -> str:
    """items: [{"product_id", "expiry" (date|None), "batch_no", "qty" (positive)}]"""
    with engine.begin() as c:
        if txn_type == "RECEIPT":
            ensure_supplier(c, supplier, supplier_kind)
        lines = [{"batch_id": get_or_create_batch(c, int(i["product_id"]), i.get("expiry"), i.get("batch_no", "")),
                  "qty": abs(int(i["qty"])), "line_note": i.get("line_note", "")} for i in items]
        cp = f"{supplier} ({supplier_kind})" if (txn_type == "RECEIPT" and supplier_kind) else supplier
        return post_transaction(engine, txn_type, txn_date, lines, user, cp, reference, note, conn=c)


def void_transaction(engine, txn_id: int, reason: str, user: str) -> str:
    if not reason.strip():
        raise ValueError("A reason is required to void a transaction")
    with engine.begin() as c:
        t = c.execute(select(transactions).where(transactions.c.id == txn_id)).mappings().first()
        if t is None:
            raise ValueError("Transaction not found")
        if t["voided_by_id"]:
            raise ValueError("This transaction has already been voided")
        if t["txn_type"] == "REVERSAL":
            raise ValueError("A reversal can't itself be voided. Re-enter the original transaction instead")
        lines = [{"batch_id": b, "qty": -q, "line_note": "reversal"} for b, q in
                 c.execute(select(movements.c.batch_id, movements.c.qty).where(movements.c.txn_id == txn_id))]
        rev_no = post_transaction(engine, "REVERSAL", dt.date.today(), lines, user, t["counterparty"],
                                  t["txn_no"], f"Void of {t['txn_no']}: {reason}", reverses_id=txn_id, conn=c)
        rev_id = c.execute(select(transactions.c.id).where(transactions.c.txn_no == rev_no)).scalar()
        c.execute(transactions.update().where(transactions.c.id == txn_id).values(voided_by_id=rev_id))
        log(c, user, "VOID", {"txn": t["txn_no"], "reversal": rev_no, "reason": reason})
        return rev_no


def import_opening_stock(engine, df: pd.DataFrame, txn_date: dt.date, user: str, note: str = "") -> tuple[str, int]:
    """df columns: product, manufacturer, category, unit, expiry_date|expiry, qty, [batch_no]."""
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "product" not in df.columns and "name" in df.columns:
        df["product"] = df["name"]
    exp_col = "expiry_date" if "expiry_date" in df.columns else ("expiry" if "expiry" in df.columns else None)
    missing = [c for c in ["product", "qty"] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(missing)}")
    def val(r, col, default=""):
        v = r.get(col)
        return default if v is None or pd.isna(v) or not str(v).strip() else str(v).strip()

    # 1. validate every row in Python first, so problems are reported plainly and nothing touches the DB
    rows, errors = [], []
    for i, r in df.iterrows():
        try:
            if not val(r, "product"):
                continue
            qty = int(float(r["qty"]))
            if qty <= 0:
                continue
            rows.append(dict(name=val(r, "product"), mfr=val(r, "manufacturer"), cat=val(r, "category", "Other"),
                             unit=val(r, "unit", "pcs"), exp=parse_expiry(r[exp_col]) if exp_col else None,
                             bno=val(r, "batch_no"), note=val(r, "note"), qty=qty))
        except Exception as e:  # collect all problems, then abort
            errors.append(f"Row {i + 2} ({val(r, 'product') or 'no product'}): {e}")
    if errors:
        raise ValueError("Import stopped, nothing was saved:\n" + "\n".join(errors[:30]))
    if not rows:
        raise ValueError("No rows with a product and a quantity above zero")

    # 2. post atomically; the lock + re-check stops a double-click or a second tab importing twice
    with engine.begin() as c:
        if c.dialect.name == "postgresql":
            c.execute(sa.text("SELECT pg_advisory_xact_lock(4242001)"))
        existing = c.execute(select(transactions.c.txn_no).where(
            transactions.c.txn_type == "OPENING", transactions.c.voided_by_id.is_(None))).scalar()
        if existing:
            raise ValueError(f"Opening stock is already loaded ({existing}); nothing was imported. "
                             "To redo it, void that transaction in Transaction History first.")
        items = []
        for r in rows:
            pid = get_or_create_product(c, r["name"], r["mfr"], r["cat"], r["unit"], user=user)
            items.append({"batch_id": get_or_create_batch(c, pid, r["exp"], r["bno"]), "qty": r["qty"],
                          "line_note": r["note"]})
        txn_no = post_transaction(engine, "OPENING", txn_date, items, user, "Warehouse count", "", note, conn=c)
        return txn_no, len(items)


# --------------------------------------------------------------------------- history / reporting
def ledger(engine, start: dt.date | None = None, end: dt.date | None = None) -> pd.DataFrame:
    q = (select(transactions.c.id.label("txn_id"), transactions.c.txn_no, transactions.c.txn_type,
                transactions.c.txn_date, transactions.c.counterparty, transactions.c.reference,
                transactions.c.entered_by, transactions.c.note, transactions.c.created_at,
                transactions.c.voided_by_id, transactions.c.reverses_id,
                products.c.name, products.c.manufacturer, products.c.category, products.c.unit,
                batches.c.id.label("batch_id"), batches.c.expiry_date, batches.c.batch_no,
                movements.c.qty, movements.c.line_note)
         .select_from(movements.join(transactions, movements.c.txn_id == transactions.c.id)
                      .join(batches, movements.c.batch_id == batches.c.id)
                      .join(products, batches.c.product_id == products.c.id))
         .order_by(transactions.c.txn_date.desc(), transactions.c.id.desc(), movements.c.id))
    if start:
        q = q.where(transactions.c.txn_date >= start)
    if end:
        q = q.where(transactions.c.txn_date <= end)
    with engine.connect() as c:
        df = pd.read_sql(q, c)
    df["voided"] = df["voided_by_id"].notna()
    df["label"] = [product_label(n, m) for n, m in zip(df["name"], df["manufacturer"])]
    return df


def transactions_summary(led: pd.DataFrame) -> pd.DataFrame:
    if led.empty:
        return pd.DataFrame(columns=["txn_id", "txn_no", "txn_date", "txn_type", "counterparty", "reference",
                                     "entered_by", "lines", "units", "voided", "note"])
    g = (led.groupby(["txn_id", "txn_no", "txn_date", "txn_type", "counterparty", "reference", "entered_by",
                      "voided", "note"], dropna=False)
         .agg(lines=("qty", "size"), units=("qty", "sum")).reset_index())
    return g.sort_values(["txn_date", "txn_id"], ascending=False).reset_index(drop=True)


def transfers_by_branch(led: pd.DataFrame) -> pd.DataFrame:
    """Net units sent to each branch per product (transfers − returns − voided transfers)."""
    if led.empty:
        return pd.DataFrame()
    d = led[led["txn_type"].isin(["TRANSFER", "RETURN"]) & ~led["voided"]].copy()
    if d.empty:
        return pd.DataFrame()
    d["sent"] = d["qty"].where(d["txn_type"] == "TRANSFER", 0) * -1
    d["returned"] = d["qty"].where(d["txn_type"] == "RETURN", 0)
    g = (d.groupby(["counterparty", "label", "unit"]).agg(sent=("sent", "sum"), returned=("returned", "sum"))
         .reset_index().rename(columns={"counterparty": "branch", "label": "product"}))
    g["net_to_branch"] = g["sent"] - g["returned"]
    return g.sort_values(["branch", "product"]).reset_index(drop=True)


def audit_df(engine, limit=500) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(select(audit_log).order_by(audit_log.c.id.desc()).limit(limit), c)


def has_opening(engine) -> bool:
    with engine.connect() as c:
        return bool(c.execute(select(func.count()).select_from(transactions)
                              .where(transactions.c.txn_type == "OPENING",
                                     transactions.c.voided_by_id.is_(None))).scalar())
