import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
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
BUCKET_ORDER = ["IWIT", "FBA Forward", "FBA Reverse", "1P", "B2C", "BigBasket"]
AGE_BUCKETS  = ["0–7 Days", "8–15 Days", "16–30 Days", "31–60 Days", "60+ Days"]
BUCKET_COLORS = {
    "IWIT":        "#3B82F6",
    "FBA Forward": "#10B981",
    "FBA Reverse": "#F59E0B",
    "1P":          "#8B5CF6",
    "B2C":         "#EF4444",
    "BigBasket":   "#EC4899",
}
AGE_COLORS = ["#22c55e", "#84cc16", "#f97316", "#ef4444", "#7c3aed"]

# ─── Bucket logic ────────────────────────────────────────────────────────────
def assign_bucket(wh: str, doc: str) -> str:
    wh_l = wh.lower()
    if "wareiq" in wh_l or "ekart" in wh_l:
        return "IWIT"
    if "to amazon fba" in wh_l:
        return "FBA Forward"
    if "from amazon fba" in wh_l:
        return "FBA Reverse"
    if "bigbasket" in wh_l:
        return "BigBasket"
    if str(doc).upper().startswith("SO"):
        return "1P"
    return "B2C"

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

    df["Intransit_quantity"] = pd.to_numeric(df["Intransit_quantity"], errors="coerce").fillna(0)
    df = df[df["Intransit_quantity"] > 0].copy()

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

    # In-Transit summary
    it_summary = (
        df.groupby(["Main Bucket", "sku", "brand", "Facility", "warehouse", "Age Bucket"])
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"),
             Avg_Cost=("Average Cost", "mean"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    # Raw Data – all columns
    raw_cols = [
        "date", "GP_PO", "sku", "Facility", "To Facility", "warehouse",
        "quantity", "received_quantity", "Intransit_quantity", "brand", "Reference",
        "Main Bucket", "Average Cost", "Open Value (INR)", "Delta Cost", "Age", "Age Bucket",
        "Month", "Quarter", "Year", "Document Type", "Movement Type", "Warehouse Bucket",
        "Current Month Flag", "Previous Month Flag", "Quarter Flag",
    ]
    raw_cols = [c for c in raw_cols if c in df.columns]

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        it_summary.to_excel(writer, sheet_name=f"In-Transit - {upload_date}", index=False)
        df[raw_cols].to_excel(writer, sheet_name=f"Raw Data - {upload_date}", index=False)
        avg_cost.to_excel(writer, sheet_name="SKU Cost Mapping", index=False)

        # Widen columns
        for sname in writer.sheets:
            ws = writer.sheets[sname]
            for col in ws.columns:
                max_w = max(len(str(cell.value or "")) for cell in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_w, 40)

    return output.getvalue()

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

with st.sidebar:
    st.markdown("### 📂 Upload Files")
    it_file  = st.file_uploader("In-Transit / Open Transactions (.csv)", type=["csv"])
    grn_file = st.file_uploader("GRN / Inventory Ledger (.csv)", type=["csv"])
    st.markdown("---")
    st.caption("Upload both files to refresh the dashboard for **all viewers**.")

if it_file and grn_file:
    it_bytes  = it_file.read()
    grn_bytes = grn_file.read()
    with open(_DEFAULT_IT,  "wb") as _f: _f.write(it_bytes)
    with open(_DEFAULT_GRN, "wb") as _f: _f.write(grn_bytes)
    st.cache_data.clear()
    st.sidebar.success("Files saved — all viewers will see this data on refresh.")
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

upload_label = date.today().strftime("%d %b %Y")
today_ts = pd.Timestamp.today().normalize()

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

# ── Snapshot: save daily summary for movement graphs ─────────────────────────
_SNAP_FILE = _DATA_DIR / "snapshot_history.csv"
_today_str = str(date.today())
if _SNAP_FILE.exists():
    _snap_hist = pd.read_csv(_SNAP_FILE)
    _snap_hist["date"] = _snap_hist["date"].astype(str)
else:
    _snap_hist = pd.DataFrame(columns=["date", "dimension", "name", "units", "value"])

if _today_str not in _snap_hist["date"].values:
    _ts = df.groupby("Main Bucket").agg(units=("Intransit_quantity","sum"), value=("Open Value (INR)","sum")).reset_index().rename(columns={"Main Bucket":"name"})
    _ts["dimension"] = "type"; _ts["date"] = _today_str
    _bs = df.groupby("brand").agg(units=("Intransit_quantity","sum"), value=("Open Value (INR)","sum")).reset_index().rename(columns={"brand":"name"})
    _bs["dimension"] = "brand"; _bs["date"] = _today_str
    _new_rows = pd.concat([_ts, _bs], ignore_index=True)[["date","dimension","name","units","value"]]
    _snap_hist = pd.concat([_snap_hist, _new_rows], ignore_index=True)
    try:
        _snap_hist.to_csv(_SNAP_FILE, index=False)
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════════════════
#  TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["📊 Overview", "🏷️ Brand", "📍 Facility", "⏱️ Ageing", "📈 Movement", "⚠️ Validation", "⬇️ Download"])

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

    k = st.columns(5)
    k[0].metric("📦 Total Units",        fmt_qty(total_vol))
    k[1].metric("💰 Total Value",        fmt_L(total_val))
    k[2].metric("⚠️ >30 Days Units",     fmt_qty(gt30_vol))
    k[3].metric("⚠️ >30 Days Value",     fmt_L(gt30_val),
                delta=f"{gt30_pct:.1f}% of total", delta_color="inverse")
    k[4].metric("❓ Units Missing Cost",  fmt_qty(missing_cost_units))

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

    tbl_left, chart_right = st.columns([1, 2])
    with tbl_left:
        disp = bucket_df.copy()
        disp[">30d %"] = disp.apply(
            lambda r: f"{r['Over30_Val']/r['Value']*100:.0f}%" if r["Value"] else "—", axis=1)
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_L)
        disp[">30d Val"] = disp["Over30_Val"].apply(fmt_L)
        tot_row = pd.DataFrame([{
            "Main Bucket": "TOTAL",
            "Volume": fmt_qty(bucket_df["Volume"].sum()),
            "Value":  fmt_L(bucket_df["Value"].sum()),
            ">30d Val": fmt_L(bucket_df["Over30_Val"].sum()),
            ">30d %": f"{gt30_pct:.0f}%",
        }])
        st.dataframe(
            pd.concat([tot_row, disp[["Main Bucket","Volume","Value",">30d Val",">30d %"]]],
                      ignore_index=True),
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

    left, right = st.columns([1, 2])
    with left:
        disp = add_total_row(brand_total, "brand").copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_L)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=380)

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

    brand_metric = st.radio("Brand × Bucket — Show", ["Value (₹ L)", "Volume (Units)"],
                            horizontal=True, key="brand_metric")
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

    fac_metric = st.radio("Facility × Bucket — Show", ["Value (₹ L)", "Volume (Units)"],
                          horizontal=True, key="fac_metric")
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
    age_metric = st.radio("Show", ["Value (₹ L)", "Volume (Units)"],
                          horizontal=True, key="age_metric")
    val_col = "Open Value (INR)" if age_metric == "Value (₹ L)" else "Intransit_quantity"
    fmt_fn  = fmt_L if age_metric == "Value (₹ L)" else fmt_qty

    def _piv_dim(src, grp_col, dim_col, vcol):
        t = src[src[grp_col].notna() & (src[grp_col].astype(str) != "NaT")].copy()
        return (t.groupby([grp_col, dim_col])[vcol].sum()
                .reset_index().pivot(index=dim_col, columns=grp_col, values=vcol).fillna(0))

    def _sort_piv(piv, col_type="month"):
        try:
            cols = (sorted(piv.columns, key=lambda x: pd.to_datetime(x, format="%b %Y"))
                    if col_type == "month" else sorted(piv.columns))
            piv = piv[cols[::-1]]
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

# ── TAB 5: MOVEMENT ─────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### Movement — Value Over Time")
    st.caption("One snapshot per upload day. Run UPDATE_DATA.bat daily to build history.")

    if len(_snap_hist) == 0:
        st.info("No history yet — snapshots accumulate each time you upload new files on a new day.")
    else:
        sh = _snap_hist.copy()
        sh["date"]  = pd.to_datetime(sh["date"])
        sh["value"] = pd.to_numeric(sh["value"], errors="coerce").fillna(0)
        sh["units"] = pd.to_numeric(sh["units"], errors="coerce").fillna(0)

        gran = st.radio("Granularity", ["Day-on-Day", "Week-on-Week", "Month-on-Month"],
                        horizontal=True, key="mv_gran")

        if gran == "Day-on-Day":
            sh["period"]   = sh["date"].dt.strftime("%d %b")
            sh["sort_key"] = sh["date"]
        elif gran == "Week-on-Week":
            sh["period"]   = "W" + sh["date"].dt.isocalendar().week.astype(str) + " " + sh["date"].dt.year.astype(str)
            sh["sort_key"] = sh["date"] - pd.to_timedelta(sh["date"].dt.dayofweek, unit="D")
        else:
            sh["period"]   = sh["date"].dt.strftime("%b %Y")
            sh["sort_key"] = sh["date"].dt.to_period("M").dt.to_timestamp()

        sh_agg = sh.groupby(["dimension","name","period","sort_key"]).agg(
            value=("value","sum"), units=("units","sum")).reset_index()
        periods_sorted = (sh_agg[["period","sort_key"]].drop_duplicates()
                          .sort_values("sort_key")["period"].tolist())

        # ── Type Level ──
        st.markdown("---")
        st.markdown("#### 📦 Type Level")
        th = sh_agg[sh_agg["dimension"] == "type"]
        if len(th):
            fig_t = go.Figure()
            for bk in BUCKET_ORDER:
                td = th[th["name"] == bk].sort_values("sort_key")
                if td.empty: continue
                fig_t.add_trace(go.Scatter(
                    x=td["period"], y=td["value"]/100000,
                    mode="lines+markers", name=bk,
                    marker_color=BUCKET_COLORS.get(bk,"#888"), line=dict(width=2),
                ))
            fig_t.update_layout(
                title=f"Open Value by Type — {gran}", height=380,
                plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC", yaxis_title="₹ Lakhs",
                xaxis=dict(categoryorder="array", categoryarray=periods_sorted),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_t, use_container_width=True)
            tp = th.pivot_table(index="name", columns="period", values="value", aggfunc="sum").fillna(0)
            tp = tp[[c for c in periods_sorted if c in tp.columns]]
            tp["Total"] = tp.sum(axis=1)
            tp = tp.reindex([b for b in BUCKET_ORDER if b in tp.index])
            tp_tot = tp.sum().rename("TOTAL")
            st.dataframe(pd.concat([tp_tot.to_frame().T, tp]).map(fmt_L), use_container_width=True)

        # ── Brand Level ──
        st.markdown("---")
        st.markdown("#### 🏷️ Brand Level")
        bh = sh_agg[sh_agg["dimension"] == "brand"]
        if len(bh):
            top15 = bh.groupby("name")["value"].sum().nlargest(15).index.tolist()
            sel_mv = st.multiselect("Select brands (leave empty = top 10)",
                                    sorted(bh["name"].unique()), default=top15[:10], key="mv_brands")
            if not sel_mv: sel_mv = top15[:10]
            bh_f = bh[bh["name"].isin(sel_mv)]
            fig_b = go.Figure()
            for br in sel_mv:
                bd = bh_f[bh_f["name"] == br].sort_values("sort_key")
                if bd.empty: continue
                fig_b.add_trace(go.Scatter(
                    x=bd["period"], y=bd["value"]/100000,
                    mode="lines+markers", name=br, line=dict(width=2),
                ))
            fig_b.update_layout(
                title=f"Open Value by Brand — {gran}", height=420,
                plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC", yaxis_title="₹ Lakhs",
                xaxis=dict(categoryorder="array", categoryarray=periods_sorted),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_b, use_container_width=True)
            bp = bh_f.pivot_table(index="name", columns="period", values="value", aggfunc="sum").fillna(0)
            bp = bp[[c for c in periods_sorted if c in bp.columns]]
            bp["Total"] = bp.sum(axis=1)
            bp = bp.sort_values("Total", ascending=False)
            bp_tot = bp.sum().rename("TOTAL")
            st.dataframe(pd.concat([bp_tot.to_frame().T, bp]).map(fmt_L), use_container_width=True)

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
        file_name=f"intransit_{date.today().strftime('%Y-%m-%d')}.xlsx",
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
