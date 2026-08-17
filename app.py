import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
import datetime as _dt
from pathlib import Path as _Path
import json as _json

st.set_page_config(
    page_title="In-Transit Dashboard | Opptra",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Constants ───────────────────────────────────────────────────────────────
FIXED_BUCKETS = ["IWIT", "FBA Forward", "FBA Reverse", "1P", "B2C"]
AGE_BUCKETS   = ["0–7 Days", "8–15 Days", "16–30 Days", "31–60 Days", "60+ Days"]
BUCKET_COLORS = {
    "IWIT":        "#3B82F6",
    "FBA Forward": "#10B981",
    "FBA Reverse": "#F59E0B",
    "1P":          "#8B5CF6",
    "B2C":         "#EF4444",
}
AGE_COLORS = ["#22c55e", "#84cc16", "#f97316", "#ef4444", "#7c3aed"]

# ─── Bucket logic ────────────────────────────────────────────────────────────
def assign_bucket(wh: str, doc: str) -> str:
    wh_l = wh.lower()
    if "wareiq" in wh_l or "ekart" in wh_l:   return "IWIT"
    if "to amazon fba"   in wh_l:              return "FBA Forward"
    if "from amazon fba" in wh_l:              return "FBA Reverse"
    if "amazon fba"      in wh_l:              return "FBA Forward"
    if "outward-intransit" in wh_l:
        return "1P" if str(doc).upper().startswith("SO") else "B2C"
    if wh_l == "b2c":                          return "B2C"
    _label = str(wh).strip()
    return _label if _label else "Unknown"

def age_bucket(days) -> str:
    if pd.isna(days) or days < 0:
        return "Unknown"
    if days <= 7:   return "0–7 Days"
    if days <= 15:  return "8–15 Days"
    if days <= 30:  return "16–30 Days"
    if days <= 60:  return "31–60 Days"
    return "60+ Days"

def doc_type(gp: str) -> str:
    g = str(gp).upper()
    if g.startswith("SO"):    return "Sales Order"
    if "VR" in g:             return "Vendor Return"
    if g.startswith("PO"):    return "Purchase Order"
    return "Transfer"

def movement_type(wh: str) -> str:
    w = wh.lower()
    if "to amazon fba"   in w: return "Outward – FBA"
    if "from amazon fba" in w: return "Return – FBA"
    if "outward"         in w: return "Outward"
    if "wareiq" in w or "ekart" in w: return "Inter-Warehouse"
    if "bigbasket"       in w: return "Outward – BigBasket"
    return "Outward"

# ─── Processing ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def process(it_bytes: bytes, grn_bytes: bytes):
    # --- In-Transit ---
    df = pd.read_csv(io.BytesIO(it_bytes))
    df.columns = df.columns.str.strip()
    # Normalize key column names — case-insensitive so CSV exports with different
    # capitalisation (Brand vs brand, Facility vs facility, etc.) all work.
    _EXPECTED = {
        "brand": "brand", "intransit_quantity": "Intransit_quantity",
        "facility": "Facility", "from_facility": "Facility",
        "to_facility": "To Facility",
        "gp_po": "GP_PO", "warehouse": "warehouse",
        "sku": "sku", "date": "date", "quantity": "quantity",
        "received_quantity": "received_quantity",
        "reference": "Reference",
        "item_name": "Item Name", "item name": "Item Name",
        "product_name": "Item Name", "description": "Item Name",
    }
    df = df.rename(columns={c: _EXPECTED[c.lower()] for c in df.columns if c.lower() in _EXPECTED})

    # Ensure Facility column exists even if neither from_facility nor facility was present
    if "Facility" not in df.columns:
        df["Facility"] = ""

    df["Intransit_quantity"] = pd.to_numeric(df["Intransit_quantity"], errors="coerce").fillna(0)

    # Filter BEFORE dedup so zero-intransit rows don't corrupt warehouse assignment
    # (e.g. GP_PO+SKU with many "Amazon FBA" qty=0 rows + one "To Amazon FBA" qty>0 row
    #  would otherwise inherit the wrong warehouse after groupby "first")
    df = df[df["Intransit_quantity"] > 0].copy()

    # Deduplicate on GP_PO + SKU — sum quantities, keep first for metadata columns
    _meta_cols = [c for c in df.columns if c not in
                  ("Intransit_quantity", "quantity", "received_quantity")]
    df = (df.groupby(["GP_PO", "sku"], as_index=False)
            .agg({**{c: "first" for c in _meta_cols if c not in ("GP_PO","sku")},
                  "Intransit_quantity": "sum",
                  "quantity":           "sum" if "quantity" in df.columns else "first",
                  "received_quantity":  "sum" if "received_quantity" in df.columns else "first"}))

    _raw_date  = df["date"].copy()
    df["date"] = pd.to_datetime(_raw_date, dayfirst=True, errors="coerce")
    _nat       = df["date"].isna()
    if _nat.any():
        df.loc[_nat, "date"] = pd.to_datetime(_raw_date[_nat], dayfirst=False, errors="coerce")
    df["quantity"]          = pd.to_numeric(df.get("quantity", 0), errors="coerce").fillna(0)
    df["received_quantity"] = pd.to_numeric(df.get("received_quantity", 0), errors="coerce").fillna(0)

    wh_col  = df["warehouse"].fillna("").astype(str)
    doc_col = df["GP_PO"].fillna("").astype(str)

    df["Main Bucket"]   = [assign_bucket(w, d) for w, d in zip(wh_col, doc_col)]
    df["Document Type"] = doc_col.apply(doc_type)
    df["Movement Type"] = wh_col.apply(movement_type)

    today = pd.Timestamp.today().normalize()
    df["Age"] = (today - df["date"]).dt.days
    df["Age Bucket"] = df["Age"].apply(age_bucket)

    df["Month"]   = df["date"].dt.strftime("%b %Y")
    df["Quarter"] = df["date"].dt.to_period("Q").astype(str)
    df["Year"]    = df["date"].dt.year.astype("Int64")

    cur_m = today.to_period("M")
    cur_q = today.to_period("Q")
    df["Current Month Flag"]  = df["date"].dt.to_period("M") == cur_m
    df["Previous Month Flag"] = df["date"].dt.to_period("M") == (cur_m - 1)
    df["Quarter Flag"]        = df["date"].dt.to_period("Q") == cur_q

    # --- GRN ---
    grn = pd.read_csv(io.BytesIO(grn_bytes))
    grn.columns = grn.columns.str.strip()
    grn = grn.rename(columns={c: c.lower() for c in grn.columns})
    grn["cost_pu"] = pd.to_numeric(grn["cost_pu"], errors="coerce")
    avg_cost = (
        grn.groupby("sku")["cost_pu"]
        .mean()
        .reset_index()
        .rename(columns={"cost_pu": "Average Cost"})
    )

    # --- Join ---
    df = df.merge(avg_cost, on="sku", how="left")
    df["Open Value (INR)"] = df["Intransit_quantity"] * df["Average Cost"]
    df["Delta Cost"] = 0.0

    # Rename for output
    df = df.rename(columns={
        "from_facility": "Facility",
        "to_facility":   "To Facility",
    })
    df["Warehouse Bucket"] = df["warehouse"]

    missing_skus = sorted(df.loc[df["Average Cost"].isna(), "sku"].unique().tolist())
    return df, missing_skus, avg_cost

# ─── Formatters ──────────────────────────────────────────────────────────────
fmt_L   = lambda v: f"₹{v/100000:.1f} L" if pd.notna(v) and v != 0 else "₹0"
fmt_qty = lambda v: f"{int(v):,}" if pd.notna(v) else "0"

def add_total_row(df, group_col, vol_col="Volume", val_col="Value"):
    total = {group_col: "TOTAL", vol_col: df[vol_col].sum(), val_col: df[val_col].sum()}
    return pd.concat([pd.DataFrame([total]), df], ignore_index=True)

def styled_metric(label, value, sub=""):
    st.metric(label=label, value=value, delta=sub if sub else None,
              delta_color="off" if sub else "normal")

# ─── Excel builder ───────────────────────────────────────────────────────────
def build_excel(df: pd.DataFrame, avg_cost: pd.DataFrame, upload_date: str) -> bytes:
    output = io.BytesIO()

    _g = lambda col: df[col] if col in df.columns else pd.Series("", index=df.index)

    dl = pd.DataFrame({
        "date":                 (df["date"].dt.strftime("%d-%b-%Y")
                                 if "date" in df.columns else ""),
        "GP_PO":                _g("GP_PO"),
        "sku":                  _g("sku"),
        "from_facility":        _g("Facility"),
        "to_facility":          _g("To Facility"),
        "warehouse":            _g("warehouse"),
        "quantity":             pd.to_numeric(_g("quantity"),           errors="coerce").fillna(0).astype(int),
        "received_quantity":    pd.to_numeric(_g("received_quantity"),  errors="coerce").fillna(0).astype(int),
        "Intransit_quantity":   pd.to_numeric(_g("Intransit_quantity"), errors="coerce").fillna(0).astype(int),
        "Reference":            _g("Reference"),
        "Brand":                _g("brand"),
        "Type":                 _g("Main Bucket"),
    })

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dl.to_excel(writer, sheet_name=f"In-Transit {upload_date}", index=False)
        avg_cost.to_excel(writer, sheet_name="SKU Cost Mapping", index=False)

        # Widen columns
        for sname in writer.sheets:
            ws = writer.sheets[sname]
            for col in ws.columns:
                max_w = max(len(str(cell.value or "")) for cell in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_w, 40)

    return output.getvalue()

# ─── GitHub auto-sync ────────────────────────────────────────────────────────
def _build_snapshot_entry(it_bytes: bytes, grn_bytes: bytes) -> dict:
    """Build snapshot dict from raw CSV bytes (same logic as append_snapshot.py)."""
    _df = pd.read_csv(io.BytesIO(it_bytes), low_memory=False)
    _df.columns = _df.columns.str.strip()
    _M = {"brand":"brand","intransit_quantity":"Intransit_quantity","facility":"Facility",
          "from_facility":"Facility","to_facility":"To Facility","gp_po":"GP_PO",
          "warehouse":"warehouse","sku":"sku","date":"date","quantity":"quantity",
          "received_quantity":"received_quantity","reference":"Reference"}
    _df = _df.rename(columns={c: _M[c.lower()] for c in _df.columns if c.lower() in _M})
    _df["Intransit_quantity"] = pd.to_numeric(_df["Intransit_quantity"], errors="coerce").fillna(0)
    _df = _df[_df["Intransit_quantity"] > 0].copy()
    _meta = [c for c in _df.columns if c not in ("Intransit_quantity","quantity","received_quantity")]
    _agg = {**{c:"first" for c in _meta if c not in ("GP_PO","sku")},"Intransit_quantity":"sum"}
    if "quantity"          in _df.columns: _agg["quantity"]          = "sum"
    if "received_quantity" in _df.columns: _agg["received_quantity"] = "sum"
    _df = _df.groupby(["GP_PO","sku"], as_index=False).agg(_agg)
    _df["Main Bucket"] = [assign_bucket(str(w), str(d))
                          for w, d in zip(_df["warehouse"].fillna(""), _df["GP_PO"].fillna(""))]
    try:
        _grn = pd.read_csv(io.BytesIO(grn_bytes), low_memory=False)
        _grn.columns = _grn.columns.str.strip()
        _grn = _grn.rename(columns={c: c.lower() for c in _grn.columns})
        _grn["cost_pu"] = pd.to_numeric(_grn["cost_pu"], errors="coerce")
        _cmap = _grn.groupby("sku")["cost_pu"].mean().to_dict()
        _df["Average Cost"] = _df["sku"].map(_cmap)
        _df["Open Value (INR)"] = _df["Intransit_quantity"] * _df["Average Cost"].fillna(0)
    except Exception:
        _df["Average Cost"] = 0.0
        _df["Open Value (INR)"] = 0.0
    _raw = _df["date"].copy()
    _df["date"] = pd.to_datetime(_raw, dayfirst=True, errors="coerce")
    _nat = _df["date"].isna()
    if _nat.any():
        _df.loc[_nat, "date"] = pd.to_datetime(_raw[_nat], dayfirst=False, errors="coerce")
    _today = pd.Timestamp(_dt.date.today()).normalize()
    _df["Age"] = (_today - _df["date"]).dt.days
    def _ab(d):
        if pd.isna(d) or d < 0: return "Unknown"
        if d <= 7:  return "0–7 Days"
        if d <= 15: return "8–15 Days"
        if d <= 30: return "16–30 Days"
        if d <= 60: return "31–60 Days"
        return "60+ Days"
    _df["Age Bucket"] = _df["Age"].apply(_ab)
    _gt30 = _df[_df["Age"] > 30]
    def _sv(grp): return {str(k): int(v)   for k, v in _df.groupby(grp)["Intransit_quantity"].sum().items()}
    def _vv(grp): return {str(k): float(v) for k, v in _df.groupby(grp)["Open Value (INR)"].sum().items()}
    return {
        "date":          _dt.date.today().strftime("%d %b %Y"),
        "timestamp":     _dt.datetime.now().isoformat(),
        "total_vol":     int(_df["Intransit_quantity"].sum()),
        "total_val":     float(_df["Open Value (INR)"].sum()),
        "gt30_vol":      int(_gt30["Intransit_quantity"].sum()),
        "gt30_val":      float(_gt30["Open Value (INR)"].sum()),
        "by_type_vol":   _sv("Main Bucket"),
        "by_type_val":   _vv("Main Bucket"),
        "by_brand_vol":  _sv("brand") if "brand" in _df.columns else {},
        "by_brand_val":  _vv("brand") if "brand" in _df.columns else {},
        "by_bucket_vol": _sv("Age Bucket"),
        "by_bucket_val": _vv("Age Bucket"),
    }


def _push_to_github(it_bytes: bytes, grn_bytes: bytes, today_label: str):
    """Push IT + GRN + updated snapshot to GitHub. Returns (ok, message)."""
    try:
        import base64 as _b64
        import traceback as _tb
        import requests as _req
        token = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
        if not token:
            return False, "Add GITHUB_TOKEN to Streamlit secrets to enable auto-sync"
        # Detect corrupted token (non-ASCII chars from bad copy-paste)
        _bad = [i for i, c in enumerate(token) if ord(c) > 127]
        if _bad:
            return False, (
                f"GITHUB_TOKEN is corrupted — {len(_bad)} non-ASCII character(s) at "
                f"position(s) {_bad[:3]}{'…' if len(_bad)>3 else ''}. "
                "Go to Streamlit → Settings → Secrets, delete the token, "
                "generate a fresh GitHub PAT, and paste it again."
            )
        _REPO  = "VaibhavJackie/Opptra-intransit-dashboard"
        _API   = f"https://api.github.com/repos/{_REPO}/contents"
        _hdrs  = {"Authorization": f"token {token}",
                  "Accept": "application/vnd.github+json"}
        _msg   = f"Data update {today_label}"

        def _get_sha(path):
            r = _req.get(f"{_API}/{path}", headers=_hdrs, timeout=30)
            return r.json().get("sha") if r.status_code == 200 else None

        def _upsert(path, content_bytes, known_sha=None):
            sha = known_sha if known_sha is not None else _get_sha(path)
            payload = {"message": _msg,
                       "content": _b64.b64encode(content_bytes).decode("ascii")}
            if sha:
                payload["sha"] = sha
            r = _req.put(f"{_API}/{path}", json=payload, headers=_hdrs, timeout=120)
            r.raise_for_status()

        snap = _build_snapshot_entry(it_bytes, grn_bytes)

        # fetch current snapshot history from GitHub
        _hr = _req.get(f"{_API}/data/snapshot_history.json", headers=_hdrs, timeout=30)
        if _hr.status_code == 200:
            _hdata = _hr.json()
            _hist  = _json.loads(_b64.b64decode(_hdata["content"]).decode())
            _hsha  = _hdata["sha"]
        else:
            _hist = []; _hsha = None

        _hist = [h for h in _hist if h.get("date") != snap["date"]]
        _hist.append(snap)
        _hist.sort(key=lambda h: _dt.datetime.strptime(h["date"], "%d %b %Y"))
        _cut  = _dt.date.today() - _dt.timedelta(days=90)
        _hist = [h for h in _hist if _dt.datetime.strptime(h["date"], "%d %b %Y").date() >= _cut]
        _snap_bytes = _json.dumps(_hist, indent=2).encode()

        _upsert("data/latest_it.csv",         it_bytes)
        _upsert("data/latest_grn.csv",        grn_bytes)
        _upsert("data/snapshot_history.json", _snap_bytes, _hsha)
        return True, f"Synced — {snap['total_vol']:,} units · Rs.{snap['total_val']/1e5:.1f}L"
    except Exception as _e:
        if "_tb" in dir():
            _lines = _tb.format_exc().strip().splitlines()
            # second-to-last line is the code line that raised; last line is the message
            _loc = _lines[-2].strip() if len(_lines) >= 2 else _lines[-1]
        else:
            _loc = type(_e).__name__
        return False, f"GitHub sync failed [{_loc}]: {_e}"


# ─── UI ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"]{background:#F0F4F8}
[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1rem}
[data-testid="metric-container"]{background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:12px 16px}
.filter-bar{background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:12px 18px;margin-bottom:12px}
</style>
""", unsafe_allow_html=True)

# Header
st.markdown(
    '<div style="background:linear-gradient(90deg,#131A48,#1e2a6e);color:white;border-radius:12px;'
    'padding:16px 24px;margin-bottom:16px;display:flex;align-items:center;gap:14px">'
    '<span style="font-size:30px">📦</span>'
    '<div><div style="font-size:20px;font-weight:700;letter-spacing:.3px">In-Transit Visibility Dashboard</div>'
    '<div style="font-size:12px;opacity:.7;margin-top:2px">Opptra Supply Chain</div></div>'
    '</div>',
    unsafe_allow_html=True,
)

_DATA_DIR   = _Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_DEFAULT_IT  = _DATA_DIR / "latest_it.csv"
_DEFAULT_GRN = _DATA_DIR / "latest_grn.csv"
_CURR_SUMM   = _DATA_DIR / "current_summary.json"
_PREV_SUMM   = _DATA_DIR / "prev_summary.json"

with st.sidebar:
    st.markdown("### 📂 Upload Files")
    it_file  = st.file_uploader("In-Transit / Open Transactions (.csv)", type=["csv"])
    grn_file = st.file_uploader("GRN / Inventory Ledger (.csv)", type=["csv"])
    st.markdown("---")
    st.caption("Upload both files to refresh the dashboard for **all viewers**.")
    st.info("⚠️ **IT file > 25 MB?** Streamlit Cloud can't accept it here.\nRun **UPDATE_DATA.bat** on your PC instead — it pushes directly to GitHub and all users see new data in ~2 min.", icon="📂")
    with st.expander("📋 Required column format"):
        st.markdown("**In-Transit file** — required columns:")
        st.code(
            "date\n"
            "Intransit_quantity\n"
            "warehouse\n"
            "GP_PO\n"
            "brand\n"
            "Facility\n"
            "sku",
            language=None,
        )
        st.markdown("**GRN / Inventory Ledger** — required columns:")
        st.code(
            "sku\n"
            "cost_pu",
            language=None,
        )
        st.caption("Column names are case-sensitive. Extra columns are ignored.")

if it_file and grn_file:
    it_bytes  = it_file.read()
    grn_bytes = grn_file.read()
    # Only process a new upload once — file_uploader re-fires on every widget interaction
    _fhash = hash(it_bytes[:2048])
    if st.session_state.get("_last_upload_hash") != _fhash:
        st.session_state["_last_upload_hash"] = _fhash
        try:
            import shutil as _shutil
            if _CURR_SUMM.exists():
                _shutil.copy(_CURR_SUMM, _PREV_SUMM)
        except Exception:
            pass
        with open(_DEFAULT_IT,  "wb") as _f: _f.write(it_bytes)
        with open(_DEFAULT_GRN, "wb") as _f: _f.write(grn_bytes)
        st.cache_data.clear()
        with st.sidebar.spinner("Saving & syncing to GitHub…"):
            _ok, _sync_msg = _push_to_github(it_bytes, grn_bytes, _dt.date.today().strftime("%d %b %Y"))
        if _ok:
            st.sidebar.success(f"✅ {_sync_msg} — all users will see new data in ~2 min")
        else:
            st.sidebar.warning(f"⚠️ {_sync_msg}")
            st.sidebar.info("Data saved locally. Run UPDATE_DATA.bat to persist it.")
elif _DEFAULT_IT.exists() and _DEFAULT_GRN.exists():
    with open(_DEFAULT_IT,  "rb") as _f: it_bytes  = _f.read()
    with open(_DEFAULT_GRN, "rb") as _f: grn_bytes = _f.read()
    _mtime = _DEFAULT_IT.stat().st_mtime
    _last  = _dt.datetime.fromtimestamp(_mtime).strftime("%d %b %Y, %H:%M")
    st.sidebar.info(f"Last upload: **{_last}**")
else:
    st.sidebar.warning("Upload both CSV files above to view the dashboard.")
    st.stop()

with st.spinner("Processing files…"):
    df, missing_skus, avg_cost = process(it_bytes, grn_bytes)

# Fixed buckets first; then any extra warehouse values found in the data
_extra_buckets = sorted([b for b in df["Main Bucket"].dropna().unique()
                          if b not in FIXED_BUCKETS and str(b).strip()])
BUCKET_ORDER = FIXED_BUCKETS + _extra_buckets

_file_mtime  = _DEFAULT_IT.stat().st_mtime if _DEFAULT_IT.exists() else None
upload_label = (_dt.datetime.fromtimestamp(_file_mtime).strftime("%d %b %Y")
                if _file_mtime else _dt.date.today().strftime("%d %b %Y"))
today_ts = pd.Timestamp.today().normalize()

# ── Save current summary (for DoD comparison on next upload) ─────────────────
def _save_summary(df, label):
    gt30 = df[df["Age"] > 30]
    summ = {
        "upload_date": label,
        "total_vol":   int(df["Intransit_quantity"].sum()),
        "total_val":   float(df["Open Value (INR)"].fillna(0).sum()),
        "gt30_vol":    int(gt30["Intransit_quantity"].sum()),
        "gt30_val":    float(gt30["Open Value (INR)"].fillna(0).sum()),
        "by_type_vol": df.groupby("Main Bucket")["Intransit_quantity"].sum().to_dict(),
        "by_type_val": df.groupby("Main Bucket")["Open Value (INR)"].sum().fillna(0).to_dict(),
    }
    try:
        with open(_CURR_SUMM, "w") as _f: _json.dump(summ, _f)
    except Exception:
        pass

_save_summary(df, upload_label)

# ── Load previous summary for DoD delta ──────────────────────────────────────
_prev = None
if _PREV_SUMM.exists():
    try:
        with open(_PREV_SUMM) as _f: _prev = _json.load(_f)
    except Exception:
        pass

# ── Global Filter Bar (above tabs) ───────────────────────────────────────────
_all_brands_list = sorted(df["brand"].dropna().astype(str).unique().tolist())
_all_facs_list   = sorted(df["Facility"].dropna().astype(str).unique().tolist())

_all_gps_list = sorted(df["GP_PO"].dropna().astype(str).unique().tolist())
with st.container():
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        sel_buckets = st.multiselect("🏷️ Type", BUCKET_ORDER,
                                     placeholder="All types", key="g_bucket")
    with fc2:
        sel_brands  = st.multiselect("🏢 Brand", _all_brands_list,
                                     placeholder="All brands", key="g_brand")
    with fc3:
        sel_facs    = st.multiselect("📍 Facility", _all_facs_list,
                                     placeholder="All facilities", key="g_fac")
    with fc4:
        sel_gp      = st.selectbox("📋 Gatepass / Doc", ["All"] + _all_gps_list,
                                   key="g_gp")

fdf = df.copy()
if sel_buckets:          fdf = fdf[fdf["Main Bucket"].isin(sel_buckets)]
if sel_brands:           fdf = fdf[fdf["brand"].isin(sel_brands)]
if sel_facs:             fdf = fdf[fdf["Facility"].isin(sel_facs)]
if sel_gp != "All":      fdf = fdf[fdf["GP_PO"].astype(str) == sel_gp]


# ════════════════════════════════════════════════════════════════════════════
#  TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["📊 Overview", "🏷️ Brand", "📍 Facility", "⏱️ Ageing", "📈 Movements", "⚠️ Validation", "⬇️ Download"])

# ── TAB 1: OVERVIEW ─────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown(f"### Open In-Transit — {upload_label}")

    # ── KPI Row ──────────────────────────────────────────────────────────────
    total_vol = fdf["Intransit_quantity"].sum()
    total_val = fdf["Open Value (INR)"].sum()
    gt30      = fdf[fdf["Age"] > 30]
    gt30_vol  = gt30["Intransit_quantity"].sum()
    gt30_val  = gt30["Open Value (INR)"].sum()
    gt30_pct  = (gt30_val / total_val * 100) if total_val else 0
    missing_cost_units = fdf[fdf["Open Value (INR)"].isna() | (fdf["Open Value (INR)"] == 0)]["Intransit_quantity"].sum()

    _prev_lbl = f"vs {_prev['upload_date']}" if _prev else None

    def _dod_vol(curr, key):
        if not _prev or key not in _prev: return None
        d = int(curr) - int(_prev[key])
        return f"{d:+,} {_prev_lbl}"

    def _dod_val(curr, key):
        if not _prev or key not in _prev: return None
        d = curr - _prev[key]
        sign = "+" if d >= 0 else ""
        return f"{sign}₹{d/100000:.1f}L {_prev_lbl}"

    k = st.columns(5)
    k[0].metric("📦 Total Units",       fmt_qty(total_vol),
                delta=_dod_vol(total_vol, "total_vol"), delta_color="off")
    k[1].metric("💰 Total Value",       fmt_L(total_val),
                delta=_dod_val(total_val, "total_val"), delta_color="off")
    k[2].metric("⚠️ >30 Days Units",    fmt_qty(gt30_vol),
                delta=_dod_vol(gt30_vol, "gt30_vol"), delta_color="inverse")
    k[3].metric("⚠️ >30 Days Value",    fmt_L(gt30_val),
                delta=_dod_val(gt30_val, "gt30_val") or f"{gt30_pct:.1f}% of total",
                delta_color="inverse")
    k[4].metric("❓ Units Missing Cost", fmt_qty(missing_cost_units))

    st.divider()

    # ── By Type (table + chart side by side) ─────────────────────────────────
    st.markdown("#### By Type")
    bucket_df = (
        fdf.groupby("Main Bucket")
        .agg(Volume=("Intransit_quantity","sum"), Value=("Open Value (INR)","sum"))
        .reset_index().sort_values("Value", ascending=False)
    )
    gt30_bucket = (
        fdf[fdf["Age"] > 30].groupby("Main Bucket")
        .agg(Over30_Vol=("Intransit_quantity","sum"), Over30_Val=("Open Value (INR)","sum"))
        .reset_index()
    )
    bucket_df = bucket_df.merge(gt30_bucket, on="Main Bucket", how="left").fillna(0)

    ov_metric = st.segmented_control(
        "By Type — Show", ["Value (₹ L)", "Volume (Units)"],
        default="Value (₹ L)", key="ov_metric",
    ) or "Value (₹ L)"
    _ov_val = ov_metric == "Value (₹ L)"

    tbl_left, chart_right = st.columns([1, 2])
    with tbl_left:
        disp = bucket_df.copy()
        if _ov_val:
            disp[">30d %"] = disp.apply(
                lambda r: f"{r['Over30_Val']/r['Value']*100:.0f}%" if r["Value"] else "—", axis=1)
            disp["Value"]    = disp["Value"].apply(fmt_L)
            disp[">30d Val"] = disp["Over30_Val"].apply(fmt_L)
            tot_row = pd.DataFrame([{
                "Main Bucket": "TOTAL",
                "Value":     fmt_L(bucket_df["Value"].sum()),
                ">30d Val":  fmt_L(bucket_df["Over30_Val"].sum()),
                ">30d %":    f"{gt30_pct:.0f}%",
            }])
            show_cols = ["Main Bucket", "Value", ">30d Val", ">30d %"]
        else:
            tot_vol = bucket_df["Volume"].sum()
            tot_30v = bucket_df["Over30_Vol"].sum()
            disp[">30d %"] = disp.apply(
                lambda r: f"{r['Over30_Vol']/r['Volume']*100:.0f}%" if r["Volume"] else "—", axis=1)
            disp["Volume"]   = disp["Volume"].apply(fmt_qty)
            disp[">30d Vol"] = disp["Over30_Vol"].apply(fmt_qty)
            tot_row = pd.DataFrame([{
                "Main Bucket": "TOTAL",
                "Volume":    fmt_qty(tot_vol),
                ">30d Vol":  fmt_qty(tot_30v),
                ">30d %":    f"{tot_30v/tot_vol*100:.0f}%" if tot_vol else "—",
            }])
            show_cols = ["Main Bucket", "Volume", ">30d Vol", ">30d %"]
        st.dataframe(
            pd.concat([tot_row, disp[show_cols]], ignore_index=True),
            hide_index=True, use_container_width=True, height=290,
        )

    with chart_right:
        bucket_plot = bucket_df.copy()
        fig = go.Figure()
        for _, row in bucket_plot.iterrows():
            bk = row["Main Bucket"]
            fig.add_trace(go.Bar(
                x=[bk], y=[row["Value"]/100000], name=bk,
                marker_color=BUCKET_COLORS.get(bk,"#6B7280"),
                text=[fmt_L(row["Value"])], textposition="outside",
            ))
        fig.update_layout(
            showlegend=False, height=300,
            plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
            yaxis_title="₹ Lakhs", xaxis_title="",
            margin=dict(t=20, b=10),
            font=dict(family="sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Brand × Type treemap ─────────────────────────────────────────────────
    st.markdown("#### Brand × Type — Value Breakdown")
    tree_df = (
        fdf.groupby(["Main Bucket","brand"])["Open Value (INR)"].sum()
        .reset_index().rename(columns={"Open Value (INR)":"Value"})
    )
    tree_df = tree_df[tree_df["Value"] > 0]
    fig_tree = px.treemap(
        tree_df, path=["Main Bucket","brand"], values="Value",
        color="Main Bucket",
        color_discrete_map=BUCKET_COLORS,
        custom_data=["Value"],
    )
    fig_tree.update_traces(
        texttemplate="%{label}<br>%{customdata[0]:,.0f}",
        hovertemplate="%{label}<br>₹%{customdata[0]:,.0f}<extra></extra>",
    )
    fig_tree.update_traces(texttemplate="%{label}")
    fig_tree.update_layout(height=420, margin=dict(t=10, b=5, l=5, r=5),
                           paper_bgcolor="#F8FAFC")
    st.plotly_chart(fig_tree, use_container_width=True)

# ── TAB 2: BRAND ────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("### Brand Summary")

    brand_total = (
        fdf.groupby("brand")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )
    gt30_brand = (
        fdf[fdf["Age"] > 30].groupby("brand")
        .agg(Over30_Vol=("Intransit_quantity", "sum"), Over30_Val=("Open Value (INR)", "sum"))
        .reset_index()
    )
    brand_total = brand_total.merge(gt30_brand, on="brand", how="left").fillna(0)

    br_metric = st.segmented_control(
        "Brand Summary — Show", ["Value (₹ L)", "Volume (Units)"],
        default="Value (₹ L)", key="br_metric",
    ) or "Value (₹ L)"
    _br_val = br_metric == "Value (₹ L)"

    left, right = st.columns([1, 2])
    with left:
        tot = {
            "brand":      "TOTAL",
            "Volume":     brand_total["Volume"].sum(),
            "Value":      brand_total["Value"].sum(),
            "Over30_Vol": brand_total["Over30_Vol"].sum(),
            "Over30_Val": brand_total["Over30_Val"].sum(),
        }
        disp = pd.concat([pd.DataFrame([tot]), brand_total.copy()], ignore_index=True)
        if _br_val:
            disp[">30d %"] = disp.apply(
                lambda r: f"{r['Over30_Val']/r['Value']*100:.0f}%" if r["Value"] else "—", axis=1)
            disp["Value"]    = disp["Value"].apply(fmt_L)
            disp[">30d Val"] = disp["Over30_Val"].apply(fmt_L)
            br_cols = ["brand", "Value", ">30d Val", ">30d %"]
        else:
            disp[">30d %"] = disp.apply(
                lambda r: f"{r['Over30_Vol']/r['Volume']*100:.0f}%" if r["Volume"] else "—", axis=1)
            disp["Volume"]   = disp["Volume"].apply(fmt_qty)
            disp[">30d Vol"] = disp["Over30_Vol"].apply(fmt_qty)
            br_cols = ["brand", "Volume", ">30d Vol", ">30d %"]
        st.dataframe(disp[br_cols], hide_index=True, use_container_width=True, height=380)

    with right:
        top15 = brand_total.head(15).copy()
        fig_b = px.bar(
            top15, y="brand", x=top15["Value"] / 100000,
            orientation="h", title="Top 15 Brands (₹ L)",
            color="Value", color_continuous_scale="Blues",
        )
        fig_b.update_layout(height=420, paper_bgcolor="#F8FAFC",
                            xaxis_title="₹ Lakhs",
                            yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_b, use_container_width=True)

    brand_metric = st.segmented_control("Brand × Bucket", ["Value (₹ L)", "Volume (Units)"],
                                        default="Value (₹ L)", key="brand_metric") or "Value (₹ L)"
    b_vcol = "Open Value (INR)" if brand_metric == "Value (₹ L)" else "Intransit_quantity"
    b_fmt  = fmt_L if brand_metric == "Value (₹ L)" else fmt_qty

    st.markdown(f"**Brand × Bucket ({brand_metric})**")
    bxb = (
        fdf.groupby(["brand", "Main Bucket"])[b_vcol].sum()
        .reset_index()
        .pivot(index="brand", columns="Main Bucket", values=b_vcol)
        .fillna(0)
    )
    for b in BUCKET_ORDER:
        if b not in bxb.columns:
            bxb[b] = 0
    bxb = bxb[BUCKET_ORDER]
    bxb["Total"] = bxb.sum(axis=1)
    bxb = bxb.sort_values("Total", ascending=False)
    st.dataframe(bxb.map(b_fmt), use_container_width=True)

# ── TAB 3: FACILITY ─────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("### Facility Summary")

    fac_total = (
        fdf.groupby("Facility")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    left, right = st.columns([1, 2])
    with left:
        disp = add_total_row(fac_total, "Facility").copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_L)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=380)

    with right:
        top15f = fac_total.head(15).copy()
        fig_f = px.bar(
            top15f, y="Facility", x=top15f["Value"] / 100000,
            orientation="h", title="Top 15 Facilities (₹ L)",
            color="Value", color_continuous_scale="Purples",
        )
        fig_f.update_layout(height=420, paper_bgcolor="#F8FAFC",
                            xaxis_title="₹ Lakhs",
                            yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_f, use_container_width=True)

    fac_metric = st.segmented_control("Facility × Bucket", ["Value (₹ L)", "Volume (Units)"],
                                      default="Value (₹ L)", key="fac_metric") or "Value (₹ L)"
    f_vcol = "Open Value (INR)" if fac_metric == "Value (₹ L)" else "Intransit_quantity"
    f_fmt  = fmt_L if fac_metric == "Value (₹ L)" else fmt_qty

    st.markdown(f"**Facility × Bucket ({fac_metric})**")
    fxb = (
        fdf.groupby(["Facility", "Main Bucket"])[f_vcol].sum()
        .reset_index()
        .pivot(index="Facility", columns="Main Bucket", values=f_vcol)
        .fillna(0)
    )
    for b in BUCKET_ORDER:
        if b not in fxb.columns:
            fxb[b] = 0
    fxb = fxb[BUCKET_ORDER]
    fxb["Total"] = fxb.sum(axis=1)
    fxb = fxb.sort_values("Total", ascending=False)
    st.dataframe(fxb.map(f_fmt), use_container_width=True)

# ── TAB 4: AGEING ───────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("### Ageing Analysis")

    age_df = fdf[fdf["Age"].notna()].copy()

    # Vol / Value toggle (used by all pivot tables in this tab)
    age_metric = st.segmented_control("Show", ["Value (₹ L)", "Volume (Units)"],
                                      default="Value (₹ L)", key="age_metric") or "Value (₹ L)"
    val_col = "Open Value (INR)" if age_metric == "Value (₹ L)" else "Intransit_quantity"
    fmt_fn  = fmt_L if age_metric == "Value (₹ L)" else fmt_qty

    _today_m_str = pd.Timestamp.today().strftime("%b %Y")      # e.g. "Jul 2026"
    _today_q_str = str(pd.Timestamp.today().to_period("Q"))    # e.g. "2026Q3"

    def _piv_dim(src, grp_col, dim_col, vcol):
        t = src[src[grp_col].notna() & (src[grp_col].astype(str) != "NaT")].copy()
        return (t.groupby([grp_col, dim_col])[vcol].sum()
                .reset_index().pivot(index=dim_col, columns=grp_col, values=vcol).fillna(0))

    def _sort_piv(piv, col_type="month"):
        try:
            if col_type == "month":
                _all = sorted(piv.columns,
                               key=lambda x: pd.to_datetime(x, format="%b %Y", errors="coerce"))
                _today_dt = pd.to_datetime(_today_m_str, format="%b %Y")
                _past   = [c for c in _all
                           if pd.to_datetime(c, format="%b %Y", errors="coerce") <= _today_dt]
                _future = [c for c in _all if c not in _past]
                if _future:
                    piv = piv.copy()
                    piv["Upcoming"] = piv[_future].sum(axis=1)
                    piv = piv.drop(columns=_future)
                    _past = _past + ["Upcoming"]
                piv = piv[_past[::-1]]           # most-recent month first; Upcoming at end
            elif col_type == "quarter":
                _all = sorted(piv.columns)       # "2026Q1" etc. sort lexicographically
                _past   = [c for c in _all if c <= _today_q_str]
                _future = [c for c in _all if c > _today_q_str]
                if _future:
                    piv = piv.copy()
                    piv["Upcoming"] = piv[_future].sum(axis=1)
                    piv = piv.drop(columns=_future)
                    _past = _past + ["Upcoming"]
                piv = piv[_past[::-1]]
            else:
                piv = piv[sorted(piv.columns)[::-1]]
        except Exception:
            pass
        piv = piv.copy()
        piv["Total"] = piv.sum(axis=1)
        piv = piv.sort_values("Total", ascending=False)
        tot = piv.sum().rename("TOTAL")
        return pd.concat([tot.to_frame().T, piv])

    # ── Type × Month ──
    st.markdown("---")
    st.markdown(f"#### 🏷️ Type × Month — MoM ({age_metric})")
    tm = _sort_piv(_piv_dim(age_df, "Month", "Main Bucket", val_col), "month")
    st.dataframe(tm.map(fmt_fn), use_container_width=True)

    # ── Type × Quarter ──
    st.markdown("---")
    st.markdown(f"#### 🏷️ Type × Quarter — QoQ ({age_metric})")
    tq = _sort_piv(_piv_dim(age_df, "Quarter", "Main Bucket", val_col), "quarter")
    st.dataframe(tq.map(fmt_fn), use_container_width=True)

    # ── Brand × Month ──
    st.markdown("---")
    st.markdown(f"#### 🏢 Brand × Month — MoM ({age_metric})")
    bm = _sort_piv(_piv_dim(age_df, "Month", "brand", val_col), "month")
    st.dataframe(bm.map(fmt_fn), use_container_width=True)

    # ── Brand × Quarter ──
    st.markdown("---")
    st.markdown(f"#### 🏢 Brand × Quarter — QoQ ({age_metric})")
    bq = _sort_piv(_piv_dim(age_df, "Quarter", "brand", val_col), "quarter")
    st.dataframe(bq.map(fmt_fn), use_container_width=True)

    # ── Facility × Month ──
    st.markdown("---")
    st.markdown(f"#### 📍 Facility × Month — MoM ({age_metric})")
    fm = _sort_piv(_piv_dim(age_df, "Month", "Facility", val_col), "month")
    st.dataframe(fm.map(fmt_fn), use_container_width=True)

    # ── Facility × Quarter ──
    st.markdown("---")
    st.markdown(f"#### 📍 Facility × Quarter — QoQ ({age_metric})")
    fq = _sort_piv(_piv_dim(age_df, "Quarter", "Facility", val_col), "quarter")
    st.dataframe(fq.map(fmt_fn), use_container_width=True)

# ── TAB 5: MOVEMENTS ────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### 📈 Movements — WoW / DoD Tracker")
    st.caption("Tracks how your in-transit position changes each time you run UPDATE_DATA.bat.")

    _HIST_FILE = _DATA_DIR / "snapshot_history.json"
    _hist = []
    if _HIST_FILE.exists():
        try:
            with open(_HIST_FILE) as _f:
                _hist = _json.load(_f)
        except Exception:
            _hist = []

    if len(_hist) < 2:
        st.info(
            "Not enough snapshots yet. "
            "Run **UPDATE_DATA.bat** on at least 2 separate days — "
            "each run automatically adds an entry to the history."
        )
    else:
        # Parse dates once
        for _h in _hist:
            _h["_d"] = _dt.datetime.strptime(_h["date"], "%d %b %Y").date()
        _hist.sort(key=lambda _h: _h["_d"])
        _curr = _hist[-1]

        # ── Controls ──
        mv_c1, mv_c2 = st.columns([1, 3])
        with mv_c1:
            mv_mode   = st.radio("Compare against", ["DoD (prev entry)", "WoW (7 days ago)"],
                                 key="mv_mode")
            mv_metric = st.segmented_control("Show", ["Value (₹ L)", "Volume (Units)"],
                                             default="Value (₹ L)", key="mv_metric") or "Value (₹ L)"

        # Select comparison snapshot
        if mv_mode == "DoD (prev entry)":
            _comp = _hist[-2]
        else:
            _target = _curr["_d"] - _dt.timedelta(days=7)
            _comp   = min(_hist[:-1], key=lambda _h: abs((_h["_d"] - _target).days))

        _is_val   = mv_metric == "Value (₹ L)"
        mv_tkey   = "total_val"   if _is_val else "total_vol"
        mv_tkey30 = "gt30_val"    if _is_val else "gt30_vol"
        mv_bytype = "by_type_val" if _is_val else "by_type_vol"
        mv_fmt    = fmt_L if _is_val else fmt_qty
        def _mv_delta(d):
            if _is_val:
                return f"{'+'if d>=0 else ''}₹{d/1e5:.1f}L"
            return f"{d:+,}"

        # ── KPI summary row ──
        _cv  = _curr[mv_tkey]
        _pv  = _comp[mv_tkey]
        _d   = _cv - _pv
        _cv30 = _curr[mv_tkey30]
        _pv30 = _comp[mv_tkey30]
        _d30  = _cv30 - _pv30

        with mv_c2:
            _mk = st.columns(4)
            _mk[0].metric(f"Current ({_curr['date']})", mv_fmt(_cv))
            _mk[1].metric(f"Comparison ({_comp['date']})", mv_fmt(_pv))
            _mk[2].metric("Net Movement", _mv_delta(_d),
                          delta=_mv_delta(_d), delta_color="off")
            _mk[3].metric(">30d Movement", _mv_delta(_d30),
                          delta=_mv_delta(_d30), delta_color="inverse")

        st.divider()

        # ── By-Type delta table ──
        st.markdown(f"#### Movement by Type — {_curr['date']} vs {_comp['date']}")
        _c_type = _curr.get(mv_bytype, {})
        _p_type = _comp.get(mv_bytype, {})
        _type_rows = []
        for _t in BUCKET_ORDER:
            _cv_t = _c_type.get(_t, 0)
            _pv_t = _p_type.get(_t, 0)
            _dt_t = _cv_t - _pv_t
            _type_rows.append({
                "Type":                            _t,
                f"Current ({_curr['date']})":      mv_fmt(_cv_t),
                f"Comparison ({_comp['date']})":   mv_fmt(_pv_t),
                "Movement":                        _mv_delta(_dt_t),
            })
        _tot_c = sum(_c_type.get(_t, 0) for _t in BUCKET_ORDER)
        _tot_p = sum(_p_type.get(_t, 0) for _t in BUCKET_ORDER)
        _type_rows.insert(0, {
            "Type":                           "TOTAL",
            f"Current ({_curr['date']})":     mv_fmt(_tot_c),
            f"Comparison ({_comp['date']})":  mv_fmt(_tot_p),
            "Movement":                       _mv_delta(_tot_c - _tot_p),
        })
        st.dataframe(pd.DataFrame(_type_rows), hide_index=True, use_container_width=True)

        st.divider()

        # ── Trend chart ──
        st.markdown(f"#### Trend Over Time — {mv_metric}")
        _trend_rows = []
        for _h in _hist:
            _row = {"Date": _h["date"], "_date_parsed": _h["_d"]}
            for _t in BUCKET_ORDER:
                _v = _h.get(mv_bytype, {}).get(_t, 0)
                _row[_t] = (_v / 1e5) if _is_val else _v
            _tv = _h[mv_tkey]
            _row["Total"] = (_tv / 1e5) if _is_val else _tv
            _trend_rows.append(_row)
        _tdf = pd.DataFrame(_trend_rows).sort_values("_date_parsed")

        _active = [_t for _t in BUCKET_ORDER if _tdf[_t].sum() > 0]
        _fig_trend = go.Figure()
        for _t in _active:
            _fig_trend.add_trace(go.Scatter(
                x=_tdf["Date"], y=_tdf[_t],
                mode="lines+markers", name=_t,
                line=dict(color=BUCKET_COLORS.get(_t, "#6B7280"), width=2),
                marker=dict(size=6),
            ))
        _fig_trend.add_trace(go.Scatter(
            x=_tdf["Date"], y=_tdf["Total"],
            mode="lines+markers", name="Total",
            line=dict(color="#131A48", width=3, dash="dot"),
            marker=dict(size=7),
        ))
        _fig_trend.update_layout(
            height=380, paper_bgcolor="#F8FAFC", plot_bgcolor="#F8FAFC",
            yaxis_title="₹ Lakhs" if _is_val else "Units",
            xaxis_title="",
            margin=dict(t=20, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            font=dict(family="sans-serif"),
        )
        st.plotly_chart(_fig_trend, use_container_width=True)

        # ── Full history table (collapsed) ──
        with st.expander(f"📋 Full snapshot history ({len(_hist)} entries)"):
            _hist_rows = []
            for _h in reversed(_hist):
                _hrow = {"Date": _h["date"]}
                _hrow["Total"] = mv_fmt(_h[mv_tkey])
                _hrow[">30d"]  = mv_fmt(_h[mv_tkey30])
                for _t in BUCKET_ORDER:
                    _hrow[_t] = mv_fmt(_h.get(mv_bytype, {}).get(_t, 0))
                _hist_rows.append(_hrow)
            st.dataframe(pd.DataFrame(_hist_rows), hide_index=True, use_container_width=True)


# ── TAB 6: VALIDATION ───────────────────────────────────────────────────────
with tabs[5]:
    st.markdown("### Validation Report")

    checks = {
        "Missing SKU Cost":  len(missing_skus),
        "Blank Warehouse":   int((df["warehouse"].isna() | (df["warehouse"] == "")).sum()),
        "Blank Brand":       int((df["brand"].isna() | (df["brand"] == "")).sum()),
        "Negative Quantity": int((df["Intransit_quantity"] < 0).sum()),
        "Missing Facility":  int((df["Facility"].isna() | (df["Facility"] == "")).sum()),
        "Duplicate Documents (same doc+sku)": int(df.duplicated(["GP_PO", "sku"]).sum()),
    }

    c = st.columns(3)
    for i, (label, val) in enumerate(checks.items()):
        with c[i % 3]:
            color = "🔴" if val > 0 else "✅"
            st.metric(f"{color} {label}", val)

    if missing_skus:
        with st.expander(f"SKUs with missing cost ({len(missing_skus)})"):
            st.dataframe(pd.DataFrame({"SKU": missing_skus}), use_container_width=True)

    # Unknown buckets
    unknown = df[~df["Main Bucket"].isin(BUCKET_ORDER)]
    if len(unknown):
        st.warning(f"{len(unknown)} rows with unknown bucket")
        st.dataframe(unknown[["GP_PO", "sku", "warehouse", "Main Bucket"]].head(20), use_container_width=True)
    else:
        st.success("All records classified into valid buckets.")

    # Bucket distribution sanity check
    st.markdown("**Bucket distribution**")
    bc = df.groupby("Main Bucket").size().reset_index(name="Rows")
    st.dataframe(bc, hide_index=True, use_container_width=True)

# ── TAB 7: DOWNLOAD ─────────────────────────────────────────────────────────
with tabs[6]:
    st.markdown("### Download Output")
    st.markdown(f"""
Each download creates two dated tabs that won't overwrite your previous uploads:
- **`In-Transit - {upload_label}`** — summarised by bucket × SKU × ageing
- **`Raw Data - {upload_label}`** — every open row with all computed columns
- **`SKU Cost Mapping`** — average GRN cost per SKU
""")

    with st.spinner("Building Excel…"):
        excel_bytes = build_excel(df, avg_cost, upload_label)

    st.download_button(
        label=f"⬇️  Download Excel  ({upload_label})",
        data=excel_bytes,
        file_name=f"intransit_{_dt.date.today().strftime('%Y-%m-%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown("---")
    st.markdown("**Quick stats on this upload**")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.metric("Open rows processed", f"{len(df):,}")
    with sc2:
        st.metric("Total in-transit units", fmt_qty(df["Intransit_quantity"].sum()))
    with sc3:
        st.metric("Total open value", fmt_L(df["Open Value (INR)"].sum()))
