import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
import io

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
fmt_inr = lambda v: f"₹{v:,.0f}" if pd.notna(v) else "₹0"
fmt_qty = lambda v: f"{int(v):,}"  if pd.notna(v) else "0"

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

c1, c2 = st.columns(2)
with c1:
    it_file = st.file_uploader("In-Transit / Open Transactions (.csv)", type=["csv"])
with c2:
    grn_file = st.file_uploader("GRN / Inventory Ledger (.csv)", type=["csv"])

DEFAULT_IT  = r"C:\Users\Vaibhav\Downloads\inventory_dataframe_2026-07-21 (3).csv"
DEFAULT_GRN = r"C:\Users\Vaibhav\Downloads\india_grn1_all_fc (25).csv"

import os as _os
if not it_file and not grn_file and _os.path.exists(DEFAULT_IT) and _os.path.exists(DEFAULT_GRN):
    st.info("Using last uploaded files automatically. Upload new files above to refresh.")
    with open(DEFAULT_IT, "rb") as f:  it_bytes  = f.read()
    with open(DEFAULT_GRN, "rb") as f: grn_bytes = f.read()
elif it_file and grn_file:
    it_bytes  = it_file.read()
    grn_bytes = grn_file.read()
else:
    st.info("Upload both files above. The dashboard updates automatically on each upload.")
    st.stop()

with st.spinner("Processing files…"):
    df, missing_skus, avg_cost = process(it_bytes, grn_bytes)

upload_label = date.today().strftime("%d %b %Y")
today_ts = pd.Timestamp.today().normalize()

# ════════════════════════════════════════════════════════════════════════════
#  TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tabs = st.tabs(["📊 Overview", "🏭 Warehouse", "🏷️ Brand", "📍 Facility", "⏱️ Ageing", "⚠️ Validation", "⬇️ Download"])

# ── TAB 1: OVERVIEW ─────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown(f"### Open In-Transit — {upload_label}")

    total_vol = df["Intransit_quantity"].sum()
    total_val = df["Open Value (INR)"].sum()
    n_skus    = df["sku"].nunique()
    n_docs    = df["GP_PO"].nunique()
    val_cov   = df["Open Value (INR)"].notna().mean() * 100

    k = st.columns(5)
    with k[0]: styled_metric("Total Units", fmt_qty(total_vol))
    with k[1]: styled_metric("Total Value (INR)", fmt_inr(total_val))
    with k[2]: styled_metric("Unique SKUs", f"{n_skus:,}")
    with k[3]: styled_metric("Open Documents", f"{n_docs:,}")
    with k[4]: styled_metric("Cost Coverage", f"{val_cov:.1f}%", f"{len(missing_skus)} SKUs missing")

    st.markdown("---")

    bucket_df = (
        df.groupby("Main Bucket")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    left, right = st.columns([1, 2])
    with left:
        st.markdown("**By Bucket**")
        disp = bucket_df.copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_inr)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=260)

    with right:
        fig = go.Figure()
        for _, row in bucket_df.iterrows():
            fig.add_trace(go.Bar(
                x=[row["Main Bucket"]], y=[row["Value"]],
                name=row["Main Bucket"],
                marker_color=BUCKET_COLORS.get(row["Main Bucket"], "#6B7280"),
                text=[fmt_inr(row["Value"])], textposition="outside",
            ))
        fig.update_layout(
            title="Open Value by Bucket (INR)", showlegend=False,
            height=320, plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
            yaxis_title="INR", xaxis_title="",
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Bucket × brand heatmap
    st.markdown("**Bucket × Brand heatmap (value)**")
    heat_df = (
        df.groupby(["Main Bucket", "brand"])["Open Value (INR)"].sum()
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

# ── TAB 2: WAREHOUSE ────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("### Warehouse Health")

    wh_df = (
        df.groupby("warehouse")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    # Colour: red if any pending
    def wh_flag(v):
        if v > 0: return "🔴"
        return "✅"

    wh_df["Status"]     = wh_df["Volume"].apply(wh_flag)
    wh_df["Volume_fmt"] = wh_df["Volume"].apply(fmt_qty)
    wh_df["Value_fmt"]  = wh_df["Value"].apply(fmt_inr)

    st.dataframe(
        wh_df[["Status", "warehouse", "Volume_fmt", "Value_fmt"]].rename(
            columns={"warehouse": "Warehouse", "Volume_fmt": "Volume", "Value_fmt": "Value (INR)"}
        ),
        hide_index=True, use_container_width=True,
    )

    fig_wh = px.treemap(
        wh_df[wh_df["Value"] > 0], path=["warehouse"], values="Value",
        title="Open Value by Warehouse",
        color="Value", color_continuous_scale="RdYlGn_r",
    )
    fig_wh.update_layout(height=420, paper_bgcolor="#F8FAFC")
    st.plotly_chart(fig_wh, use_container_width=True)

# ── TAB 3: BRAND ────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("### Brand Summary")

    brand_total = (
        df.groupby("brand")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    left, right = st.columns([1, 2])
    with left:
        disp = brand_total.copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_inr)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=350)

    with right:
        fig_b = px.bar(
            brand_total.head(15), y="brand", x="Value",
            orientation="h", title="Top 15 Brands by Open Value",
            color="Value", color_continuous_scale="Blues",
        )
        fig_b.update_layout(height=420, paper_bgcolor="#F8FAFC", yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_b, use_container_width=True)

    st.markdown("**Brand × Bucket**")
    bxb = (
        df.groupby(["brand", "Main Bucket"])["Open Value (INR)"].sum()
        .reset_index()
        .pivot(index="brand", columns="Main Bucket", values="Open Value (INR)")
        .fillna(0)
    )
    for b in BUCKET_ORDER:
        if b not in bxb.columns:
            bxb[b] = 0
    bxb = bxb[BUCKET_ORDER].sort_values("IWIT", ascending=False)
    bxb["Total"] = bxb.sum(axis=1)
    bxb = bxb.sort_values("Total", ascending=False)
    st.dataframe(bxb.map(fmt_inr), use_container_width=True)

# ── TAB 4: FACILITY ─────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("### Facility Summary")

    fac_total = (
        df.groupby("Facility")
        .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
        .reset_index()
        .sort_values("Value", ascending=False)
    )

    left, right = st.columns([1, 2])
    with left:
        disp = fac_total.copy()
        disp["Volume"] = disp["Volume"].apply(fmt_qty)
        disp["Value"]  = disp["Value"].apply(fmt_inr)
        st.dataframe(disp, hide_index=True, use_container_width=True, height=350)

    with right:
        fig_f = px.bar(
            fac_total.head(15), y="Facility", x="Value",
            orientation="h", title="Top 15 Facilities by Open Value",
            color="Value", color_continuous_scale="Purples",
        )
        fig_f.update_layout(height=420, paper_bgcolor="#F8FAFC", yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_f, use_container_width=True)

    st.markdown("**Facility × Bucket**")
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
    st.dataframe(fxb.map(fmt_inr), use_container_width=True)

# ── TAB 5: AGEING ───────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("### Ageing Analysis")

    view_col, drill_col = st.columns(2)
    with view_col:
        period = st.selectbox(
            "Period",
            ["Current Month", "Previous Month", "Quarter-to-Date", "Month-on-Month", "All Time"],
        )
    with drill_col:
        drill = st.radio("Drill-down by", ["Overall", "Brand", "Facility", "Warehouse"], horizontal=True)

    dim_map = {"Brand": "brand", "Facility": "Facility", "Warehouse": "warehouse"}

    # Filter
    if period == "Current Month":
        filtered = df[df["Current Month Flag"]]
    elif period == "Previous Month":
        filtered = df[df["Previous Month Flag"]]
    elif period == "Quarter-to-Date":
        filtered = df[df["Quarter Flag"]]
    elif period == "Month-on-Month":
        filtered = df  # all; will pivot by Month below
    else:
        filtered = df

    if period == "Month-on-Month":
        # Special view: Month rows, Age Bucket columns
        mom = (
            filtered.groupby(["Month", "Age Bucket"])["Open Value (INR)"].sum()
            .reset_index()
            .pivot(index="Month", columns="Age Bucket", values="Open Value (INR)")
            .fillna(0)
        )
        for b in AGE_BUCKETS:
            if b not in mom.columns:
                mom[b] = 0
        mom = mom[AGE_BUCKETS]
        mom["Total"] = mom.sum(axis=1)
        # sort by month chronologically
        mom.index = pd.to_datetime(mom.index, format="%b %Y", errors="coerce")
        mom = mom.sort_index()
        mom.index = mom.index.strftime("%b %Y")
        st.dataframe(mom.map(fmt_inr), use_container_width=True)
    else:
        age_agg = (
            filtered.groupby("Age Bucket")
            .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
            .reset_index()
        )
        age_agg["Age Bucket"] = pd.Categorical(age_agg["Age Bucket"], categories=AGE_BUCKETS, ordered=True)
        age_agg = age_agg.sort_values("Age Bucket")

        fig_age = go.Figure()
        for i, row in age_agg.iterrows():
            bk = row["Age Bucket"]
            fig_age.add_trace(go.Bar(
                x=[bk], y=[row["Value"]],
                name=bk,
                marker_color=AGE_COLORS[AGE_BUCKETS.index(bk)] if bk in AGE_BUCKETS else "#888",
                text=[fmt_inr(row["Value"])], textposition="outside",
            ))
        fig_age.update_layout(
            title=f"Open Value by Age — {period}", showlegend=False,
            height=320, plot_bgcolor="#F8FAFC", paper_bgcolor="#F8FAFC",
        )
        st.plotly_chart(fig_age, use_container_width=True)

        if drill == "Overall":
            disp = age_agg.copy()
            disp["Volume"] = disp["Volume"].apply(fmt_qty)
            disp["Value"]  = disp["Value"].apply(fmt_inr)
            st.dataframe(disp, hide_index=True, use_container_width=True)
        else:
            dim = dim_map[drill]
            age_drill = (
                filtered.groupby([dim, "Age Bucket"])
                .agg(Volume=("Intransit_quantity", "sum"), Value=("Open Value (INR)", "sum"))
                .reset_index()
            )
            pivot_v = age_drill.pivot_table(index=dim, columns="Age Bucket", values="Value", fill_value=0)
            pivot_q = age_drill.pivot_table(index=dim, columns="Age Bucket", values="Volume", fill_value=0)
            for b in AGE_BUCKETS:
                if b not in pivot_v.columns: pivot_v[b] = 0
                if b not in pivot_q.columns: pivot_q[b] = 0
            pivot_v = pivot_v[AGE_BUCKETS]
            pivot_q = pivot_q[AGE_BUCKETS]
            pivot_v["Total Value"]  = pivot_v.sum(axis=1)
            pivot_q["Total Volume"] = pivot_q.sum(axis=1)
            pivot_v = pivot_v.sort_values("Total Value", ascending=False)
            pivot_q = pivot_q.loc[pivot_v.index]

            subtab_v, subtab_q = st.tabs(["By Value (INR)", "By Volume (Units)"])
            with subtab_v:
                st.dataframe(pivot_v.map(fmt_inr), use_container_width=True)
            with subtab_q:
                st.dataframe(pivot_q.map(fmt_qty), use_container_width=True)

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
        st.metric("Total open value", fmt_inr(df["Open Value (INR)"].sum()))
