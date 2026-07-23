import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
import io
import datetime as _dt
from pathlib import Path as _Path

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

    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
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
    st.markdown(
        f"""<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;
        padding:14px 18px;margin:4px 0">
        <div style="font-size:12px;color:#64748B;font-weight:500">{label}</div>
        <div style="font-size:22px;font-weight:700;color:#0F172A">{value}</div>
        {"<div style='font-size:11px;color:#94A3B8'>"+sub+"</div>" if sub else ""}
        </div>""",
        unsafe_allow_html=True,
    )

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
.block-container{padding-top:1.5rem}
div[data-testid="metric-container"]{background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:12px}
</style>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div style="background:linear-gradient(90deg,#131A48,#1e2a6e);color:white;border-radius:12px;
padding:18px 24px;margin-bottom:20px;display:flex;align-items:center;gap:12px">
<span style="font-size:28px">📦</span>
<div>
  <div style="font-size:20px;font-weight:700">In-Transit Visibility Dashboard</div>
  <div style="font-size:13px;opacity:0.7">Opptra Supply Chain · Upload files to refresh</div>
</div>
</div>
""", unsafe_allow_html=True)

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

# ════════════════════════════════════════════════════════════════════════════
#  TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["📊 Overview", "🏷️ Brand", "📍 Facility", "⏱️ Ageing", "⚠️ Validation", "⬇️ Download"])

# ── TAB 1: OVERVIEW ─────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown(f"### Open In-Transit — {upload_label}")

    all_brands = sorted(df["brand"].dropna().unique().tolist())
    ov_brand = st.selectbox("Filter by brand", ["All"] + all_brands, key="ov_brand")
    ov_df = df if ov_brand == "All" else df[df["brand"] == ov_brand]

    total_vol  = ov_df["Intransit_quantity"].sum()
    total_val  = ov_df["Open Value (INR)"].sum()
    gt30       = ov_df[ov_df["Age"] > 30]
    gt30_vol   = gt30["Intransit_quantity"].sum()
    gt30_val   = gt30["Open Value (INR)"].sum()
    gt30_pct   = (gt30_val / total_val * 100) if total_val else 0

    k = st.columns(5)
    with k[0]: styled_metric("Total Open Units", fmt_qty(total_vol))
    with k[1]: styled_metric("Total Open Value", fmt_L(total_val))
    with k[2]: styled_metric(">30 Days Units",   fmt_qty(gt30_vol))
    with k[3]: styled_metric(">30 Days Value",   fmt_L(gt30_val))
    with k[4]: styled_metric(">30 Days % of Value", f"{gt30_pct:.1f}%")

    st.markdown("---")

    bucket_df = (
        ov_df.groupby("Main Bucket")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    left, right = st.columns([1, 2])
    with left:
        st.markdown("**By Bucket**")
        disp = add_total_row(bucket_df, "Main Bucket").copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_L)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=280)

    with right:
        fig = go.Figure()
        for _, row in bucket_df.iterrows():
            fig.add_trace(go.Bar(
                x=[row["Main Bucket"]], y=[row["Value"] / 100000],
                name=row["Main Bucket"],
                marker_color=BUCKET_COLORS.get(row["Main Bucket"], "#6B7280"),
                text=[fmt_L(row["Value"])], textposition="outside",
            ))
        fig.update_layout(
            title="Open Value by Bucket (₹ L)", showlegend=False,
            height=320, plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
            yaxis_title="₹ Lakhs", xaxis_title="",
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Bucket × brand heatmap
    st.markdown("**Bucket × Brand heatmap (value)**")
    heat_df = (
        ov_df.groupby(["Main Bucket", "brand"])["Open Value (INR)"].sum()
        .reset_index()
        .pivot(index="brand", columns="Main Bucket", values="Open Value (INR)")
        .fillna(0)
    )
    top_brands = heat_df.sum(axis=1).nlargest(20).index
    heat_df = heat_df.loc[top_brands]
    fig_heat = px.imshow(
        heat_df,
        color_continuous_scale="Blues", aspect="auto",
        labels=dict(color="INR"), title="Top 20 Brands × Bucket (Open Value)"
    )
    fig_heat.update_layout(height=450, paper_bgcolor="#F8FAFC")
    st.plotly_chart(fig_heat, use_container_width=True)

# ── TAB 2: BRAND ────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("### Brand Summary")

    b_bucket = st.selectbox("Filter by bucket", ["All"] + BUCKET_ORDER, key="brand_bucket")
    b_src = df if b_bucket == "All" else df[df["Main Bucket"] == b_bucket]

    brand_total = (
        b_src.groupby("brand")
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
        fig_b = px.bar(
            brand_total.head(15), y="brand", x=brand_total.head(15)["Value"] / 100000,
            orientation="h", title="Top 15 Brands (₹ L)",
            color="Value", color_continuous_scale="Blues",
        )
        fig_b.update_layout(height=420, paper_bgcolor="#F8FAFC",
                            xaxis_title="₹ Lakhs",
                            yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_b, use_container_width=True)

    st.markdown("**Brand × Bucket (₹ L)**")
    bxb = (
        df.groupby(["brand", "Main Bucket"])["Open Value (INR)"].sum()
        .reset_index()
        .pivot(index="brand", columns="Main Bucket", values="Open Value (INR)")
        .fillna(0)
    )
    for b in BUCKET_ORDER:
        if b not in bxb.columns:
            bxb[b] = 0
    bxb = bxb[BUCKET_ORDER]
    bxb["Total"] = bxb.sum(axis=1)
    bxb = bxb.sort_values("Total", ascending=False)
    st.dataframe(bxb.map(fmt_L), use_container_width=True)

# ── TAB 3: FACILITY ─────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("### Facility Summary")

    f_bucket = st.selectbox("Filter by bucket", ["All"] + BUCKET_ORDER, key="fac_bucket")
    f_src = df if f_bucket == "All" else df[df["Main Bucket"] == f_bucket]

    fac_total = (
        f_src.groupby("Facility")
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
        fig_f = px.bar(
            fac_total.head(15), y="Facility", x=fac_total.head(15)["Value"] / 100000,
            orientation="h", title="Top 15 Facilities (₹ L)",
            color="Value", color_continuous_scale="Purples",
        )
        fig_f.update_layout(height=420, paper_bgcolor="#F8FAFC",
                            xaxis_title="₹ Lakhs",
                            yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_f, use_container_width=True)

    st.markdown("**Facility × Bucket (₹ L)**")
    fxb = (
        df.groupby(["Facility", "Main Bucket"])["Open Value (INR)"].sum()
        .reset_index()
        .pivot(index="Facility", columns="Main Bucket", values="Open Value (INR)")
        .fillna(0)
    )
    for b in BUCKET_ORDER:
        if b not in fxb.columns:
            fxb[b] = 0
    fxb = fxb[BUCKET_ORDER]
    fxb["Total"] = fxb.sum(axis=1)
    fxb = fxb.sort_values("Total", ascending=False)
    st.dataframe(fxb.map(fmt_L), use_container_width=True)

# ── TAB 4: AGEING ───────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("### Ageing Analysis")

    age_bucket_filter = st.selectbox("Filter by bucket", ["All"] + BUCKET_ORDER, key="age_bucket")
    suffix = f" · {age_bucket_filter}" if age_bucket_filter != "All" else ""

    # Base filter — drop rows with no valid date (fixes "none" age bucket)
    age_df = df[df["Age"].notna()].copy()
    if age_bucket_filter != "All":
        age_df = age_df[age_df["Main Bucket"] == age_bucket_filter]

    RISK_BUCKETS = ["31–60 Days", "60+ Days"]

    def _make_piv(src, grp_col):
        trend = src[src[grp_col].notna() & (src[grp_col].astype(str) != "NaT")].copy()
        pv = (trend.groupby([grp_col, "Age Bucket"])["Open Value (INR)"].sum()
              .reset_index().pivot(index=grp_col, columns="Age Bucket",
                                   values="Open Value (INR)").fillna(0))
        pq = (trend.groupby([grp_col, "Age Bucket"])["Intransit_quantity"].sum()
              .reset_index().pivot(index=grp_col, columns="Age Bucket",
                                   values="Intransit_quantity").fillna(0))
        for b in AGE_BUCKETS:
            if b not in pv.columns: pv[b] = 0
            if b not in pq.columns: pq[b] = 0
        pv = pv[AGE_BUCKETS]; pq = pq[AGE_BUCKETS]
        if grp_col == "Month":
            pv.index = pd.to_datetime(pv.index, format="%b %Y", errors="coerce")
            pq.index = pd.to_datetime(pq.index, format="%b %Y", errors="coerce")
        return pv.sort_index(), pq.sort_index()

    mom_val, mom_vol = _make_piv(age_df, "Month")
    qoq_val, qoq_vol = _make_piv(age_df, "Quarter")
    mom_labels = mom_val.index.strftime("%b %Y").tolist()
    qoq_labels = [str(x) for x in qoq_val.index.tolist()]

    # ── Section 1: Month-on-Month ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📅 Month-on-Month")
    fig_mom = go.Figure()
    for i, bk in enumerate(AGE_BUCKETS):
        y = mom_val[bk].values / 100000
        fig_mom.add_trace(go.Bar(
            name=bk, x=mom_labels, y=y, marker_color=AGE_COLORS[i],
            text=[fmt_L(v * 100000) if v > 0 else "" for v in y],
            textposition="inside", insidetextanchor="middle",
        ))
    fig_mom.update_layout(
        barmode="stack",
        title=f"Ageing Profile — Month-on-Month{suffix}  (green = fresh, red = old)",
        height=400, plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
        yaxis_title="₹ Lakhs",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_mom, use_container_width=True)
    mv_d = mom_val.copy(); mv_d["Total"] = mv_d.sum(axis=1); mv_d = mv_d.iloc[::-1]
    mq_d = mom_vol.copy(); mq_d["Total"] = mq_d.sum(axis=1); mq_d = mq_d.iloc[::-1]
    mv_d.index = pd.to_datetime(mv_d.index).strftime("%b %Y")
    mq_d.index = pd.to_datetime(mq_d.index).strftime("%b %Y")
    ms1, ms2 = st.tabs(["Value (₹ L)", "Volume (Units)"])
    with ms1: st.dataframe(mv_d.map(fmt_L), use_container_width=True)
    with ms2: st.dataframe(mq_d.map(fmt_qty), use_container_width=True)

    # ── Section 2: Quarter-on-Quarter ────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📆 Quarter-on-Quarter")
    fig_qoq = go.Figure()
    for i, bk in enumerate(AGE_BUCKETS):
        y = qoq_val[bk].values / 100000
        fig_qoq.add_trace(go.Bar(
            name=bk, x=qoq_labels, y=y, marker_color=AGE_COLORS[i],
            text=[fmt_L(v * 100000) if v > 0 else "" for v in y],
            textposition="inside", insidetextanchor="middle",
        ))
    fig_qoq.update_layout(
        barmode="stack",
        title=f"Ageing Profile — Quarter-on-Quarter{suffix}  (green = fresh, red = old)",
        height=400, plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
        yaxis_title="₹ Lakhs",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_qoq, use_container_width=True)
    qv_d = qoq_val.copy(); qv_d["Total"] = qv_d.sum(axis=1); qv_d = qv_d.iloc[::-1]
    qq_d = qoq_vol.copy(); qq_d["Total"] = qq_d.sum(axis=1); qq_d = qq_d.iloc[::-1]
    qs1, qs2 = st.tabs(["Value (₹ L)", "Volume (Units)"])
    with qs1: st.dataframe(qv_d.map(fmt_L), use_container_width=True)
    with qs2: st.dataframe(qq_d.map(fmt_qty), use_container_width=True)

    # ── Section 3: Risk Movement (>30 Days) ──────────────────────────────────
    st.markdown("---")
    st.markdown("#### ⚠️ Risk Movement — >30 Days Only")
    st.caption("Excludes <30 day buckets — shows how at-risk inventory value has moved over time.")

    risk_m_cols = [b for b in RISK_BUCKETS if b in mom_val.columns]
    risk_q_cols = [b for b in RISK_BUCKETS if b in qoq_val.columns]
    risk_mom_v  = mom_val[risk_m_cols].sum(axis=1) / 100000
    risk_qoq_v  = qoq_val[risk_q_cols].sum(axis=1) / 100000

    rc1, rc2 = st.columns(2)
    with rc1:
        fig_rm = go.Figure(go.Bar(
            x=mom_labels, y=risk_mom_v.values, marker_color="#E63946",
            text=[fmt_L(v * 100000) for v in risk_mom_v.values],
            textposition="outside",
        ))
        fig_rm.update_layout(
            title="MoM: >30d Risk (₹ L)", height=340,
            plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
            yaxis_title="₹ Lakhs", showlegend=False,
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig_rm, use_container_width=True)
        rtbl_m = mom_val[risk_m_cols].copy()
        rtbl_m["Total >30d"] = rtbl_m.sum(axis=1)
        rtbl_m = rtbl_m.iloc[::-1]
        rtbl_m.index = pd.to_datetime(rtbl_m.index).strftime("%b %Y")
        st.dataframe(rtbl_m.map(fmt_L), use_container_width=True)

    with rc2:
        fig_rq = go.Figure(go.Bar(
            x=qoq_labels, y=risk_qoq_v.values, marker_color="#FF5800",
            text=[fmt_L(v * 100000) for v in risk_qoq_v.values],
            textposition="outside",
        ))
        fig_rq.update_layout(
            title="QoQ: >30d Risk (₹ L)", height=340,
            plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
            yaxis_title="₹ Lakhs", showlegend=False,
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig_rq, use_container_width=True)
        rtbl_q = qoq_val[risk_q_cols].copy()
        rtbl_q["Total >30d"] = rtbl_q.sum(axis=1)
        rtbl_q = rtbl_q.iloc[::-1]
        st.dataframe(rtbl_q.map(fmt_L), use_container_width=True)

    # ── Section 4: Brand × Month ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 🏷️ Brand × Month (MoM)")
    bm_src = age_df[age_df["Month"].notna() & (age_df["Month"].astype(str) != "NaT")].copy()
    brand_mom_piv = (
        bm_src.groupby(["brand", "Month"])["Open Value (INR)"].sum()
        .reset_index().pivot(index="brand", columns="Month",
                             values="Open Value (INR)").fillna(0)
    )
    try:
        sorted_m = sorted(brand_mom_piv.columns,
                          key=lambda x: pd.to_datetime(x, format="%b %Y"))
        brand_mom_piv = brand_mom_piv[sorted_m[::-1]]
    except Exception:
        pass
    brand_mom_piv["Total"] = brand_mom_piv.sum(axis=1)
    brand_mom_piv = brand_mom_piv.sort_values("Total", ascending=False)
    bm_tot = brand_mom_piv.sum().rename("TOTAL")
    brand_mom_disp = pd.concat([bm_tot.to_frame().T, brand_mom_piv])
    st.dataframe(brand_mom_disp.map(fmt_L), use_container_width=True)

    # ── Section 5: Brand × Quarter ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊 Brand × Quarter (QoQ)")
    bq_src = age_df[age_df["Quarter"].notna() & (age_df["Quarter"].astype(str) != "NaT")].copy()
    brand_qoq_piv = (
        bq_src.groupby(["brand", "Quarter"])["Open Value (INR)"].sum()
        .reset_index().pivot(index="brand", columns="Quarter",
                             values="Open Value (INR)").fillna(0)
    )
    try:
        sorted_q = sorted(brand_qoq_piv.columns)
        brand_qoq_piv = brand_qoq_piv[sorted_q[::-1]]
    except Exception:
        pass
    brand_qoq_piv["Total"] = brand_qoq_piv.sum(axis=1)
    brand_qoq_piv = brand_qoq_piv.sort_values("Total", ascending=False)
    bq_tot = brand_qoq_piv.sum().rename("TOTAL")
    brand_qoq_disp = pd.concat([bq_tot.to_frame().T, brand_qoq_piv])
    st.dataframe(brand_qoq_disp.map(fmt_L), use_container_width=True)

# ── TAB 5: VALIDATION ───────────────────────────────────────────────────────
with tabs[4]:
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

# ── TAB 6: DOWNLOAD ─────────────────────────────────────────────────────────
with tabs[5]:
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
