"""
append_snapshot.py
──────────────────
Run by UPDATE_DATA.bat after copying CSVs, before git commit.
Reads data/latest_it.csv + data/latest_grn.csv, computes summary metrics,
and appends one entry to data/snapshot_history.json (rolling 90-day window).
"""
import json, pathlib, datetime as dt
import pandas as pd

_DIR  = pathlib.Path(__file__).parent
DATA  = _DIR / "data"
IT    = DATA / "latest_it.csv"
GRN   = DATA / "latest_grn.csv"
HIST  = DATA / "snapshot_history.json"
KEEP  = 90  # days of history to retain

BUCKET_ORDER = ["IWIT", "FBA Forward", "FBA Reverse", "1P", "B2C", "BigBasket"]
AGE_BUCKETS  = ["0–7 Days", "8–15 Days", "16–30 Days", "31–60 Days", "60+ Days"]


def _assign_bucket(wh, gp):
    w = str(wh).lower()
    if "wareiq" in w or "ekart" in w:    return "IWIT"
    if "to amazon fba"   in w:           return "FBA Forward"
    if "from amazon fba" in w:           return "FBA Reverse"
    if "bigbasket"       in w:           return "BigBasket"
    if str(gp).upper().startswith("SO"): return "1P"
    return "B2C"


def _age_bucket(days):
    if pd.isna(days) or days < 0: return "Unknown"
    if days <= 7:  return "0–7 Days"
    if days <= 15: return "8–15 Days"
    if days <= 30: return "16–30 Days"
    if days <= 60: return "31–60 Days"
    return "60+ Days"


def load_df():
    df = pd.read_csv(IT, low_memory=False)
    df.columns = df.columns.str.strip()

    _MAP = {
        "brand": "brand", "intransit_quantity": "Intransit_quantity",
        "facility": "Facility", "from_facility": "Facility",
        "to_facility": "To Facility", "gp_po": "GP_PO",
        "warehouse": "warehouse", "sku": "sku", "date": "date",
        "quantity": "quantity", "received_quantity": "received_quantity",
        "reference": "Reference",
    }
    df = df.rename(columns={c: _MAP[c.lower()] for c in df.columns if c.lower() in _MAP})
    if "Facility" not in df.columns:
        df["Facility"] = ""

    df["Intransit_quantity"] = pd.to_numeric(df["Intransit_quantity"], errors="coerce").fillna(0)
    df = df[df["Intransit_quantity"] > 0].copy()

    # Deduplicate GP_PO + SKU
    _meta = [c for c in df.columns if c not in ("Intransit_quantity", "quantity", "received_quantity")]
    _agg  = {c: "first" for c in _meta if c not in ("GP_PO", "sku")}
    _agg["Intransit_quantity"] = "sum"
    if "quantity" in df.columns:          _agg["quantity"]          = "sum"
    if "received_quantity" in df.columns: _agg["received_quantity"] = "sum"
    df = df.groupby(["GP_PO", "sku"], as_index=False).agg(_agg)

    # Bucket
    df["Main Bucket"] = [
        _assign_bucket(w, g)
        for w, g in zip(df["warehouse"].fillna(""), df["GP_PO"].fillna(""))
    ]

    # Date + Age
    _raw = df["date"].copy()
    df["date"] = pd.to_datetime(_raw, dayfirst=True, errors="coerce")
    _nat = df["date"].isna()
    if _nat.any():
        df.loc[_nat, "date"] = pd.to_datetime(_raw[_nat], dayfirst=False, errors="coerce")
    today = pd.Timestamp(dt.date.today())
    df["Age"] = (today - df["date"]).dt.days.clip(lower=0)
    df["Age Bucket"] = df["Age"].apply(_age_bucket)

    # Cost
    try:
        grn = pd.read_csv(GRN, low_memory=False)
        grn.columns = grn.columns.str.strip().str.lower()
        cost_map = grn.groupby("sku")["cost_pu"].mean().to_dict()
        df["Average Cost"]    = df["sku"].map(cost_map)
        df["Open Value (INR)"] = df["Intransit_quantity"] * df["Average Cost"].fillna(0)
    except Exception as e:
        print(f"  WARNING: cost merge failed ({e}) — value metrics will be 0")
        df["Average Cost"]    = 0.0
        df["Open Value (INR)"] = 0.0

    return df


def build_entry(df, label):
    def _sum_vol(grp):
        return {str(k): int(v) for k, v in df.groupby(grp)["Intransit_quantity"].sum().items()}
    def _sum_val(grp):
        return {str(k): float(v) for k, v in df.groupby(grp)["Open Value (INR)"].sum().items()}

    gt30 = df[df["Age"] > 30]
    return {
        "date":          label,
        "timestamp":     dt.datetime.now().isoformat(),
        "total_vol":     int(df["Intransit_quantity"].sum()),
        "total_val":     float(df["Open Value (INR)"].sum()),
        "gt30_vol":      int(gt30["Intransit_quantity"].sum()),
        "gt30_val":      float(gt30["Open Value (INR)"].sum()),
        "by_type_vol":   _sum_vol("Main Bucket"),
        "by_type_val":   _sum_val("Main Bucket"),
        "by_brand_vol":  _sum_vol("brand") if "brand" in df.columns else {},
        "by_brand_val":  _sum_val("brand") if "brand" in df.columns else {},
        "by_bucket_vol": _sum_vol("Age Bucket"),
        "by_bucket_val": _sum_val("Age Bucket"),
    }


def main():
    if not IT.exists():
        print("ERROR: data/latest_it.csv not found — run after copying files.")
        return

    history = []
    if HIST.exists():
        try:
            with open(HIST) as f:
                history = json.load(f)
        except Exception:
            history = []

    df    = load_df()
    mtime = IT.stat().st_mtime
    label = dt.datetime.fromtimestamp(mtime).strftime("%d %b %Y")

    # Remove any existing entry for this date (overwrite)
    history = [h for h in history if h.get("date") != label]

    entry = build_entry(df, label)
    history.append(entry)

    # Sort chronologically
    history.sort(key=lambda h: dt.datetime.strptime(h["date"], "%d %b %Y"))

    # Keep rolling window
    cutoff = dt.date.today() - dt.timedelta(days=KEEP)
    history = [
        h for h in history
        if dt.datetime.strptime(h["date"], "%d %b %Y").date() >= cutoff
    ]

    with open(HIST, "w") as f:
        json.dump(history, f, indent=2)

    print(f"  Snapshot saved: {label}  |  {entry['total_vol']:,} units  |  Rs.{entry['total_val']/1e5:.1f}L")
    print(f"  History: {len(history)} entries  |  oldest: {history[0]['date']}")


if __name__ == "__main__":
    main()
