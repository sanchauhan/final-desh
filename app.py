from datetime import datetime
import plotly.express as px
import pytz
from st_aggrid import AgGrid, ColumnsAutoSizeMode, GridOptionsBuilder
import streamlit as st

from cpr_engine import (
    evaluate_signals,
    fetch_all_cpr_levels,
    fetch_yahoo_fallback_prices,
)
from daily_screener import run_daily_strategy_screener
from data_fetcher import (
    fetch_kotak_live_stocks,
    fetch_kotak_sectoral_indices,
    fetch_tradingview_sectoral_indices,
    fetch_tradingview_stocks,
)
from tickers_list import DEFAULT_TICKERS

st.set_page_config(page_title="Pro Stocks Screener", layout="wide")
st.title("📈 Pro CPR & Sectoral Terminal")

with st.sidebar.expander("⚡ Live Auto-Refresh", expanded=True):
    auto_refresh = st.toggle("Enable Background Refresh", value=False)
    refresh_sec = st.selectbox("Interval (Seconds):", (15, 30, 60), index=1)
    if st.button("🔄 Force Refresh Now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with st.sidebar.expander("🔍 Screener Filters", expanded=True):
    filter_mode = st.selectbox(
        "Signal Filter:",
        (
            "All Actions",
            "Strong Buy Only",
            "Strong Sell Only",
            "Strong / Mod / Buy",
            "HOLD Only",
        ),
    )
    min_price = st.number_input("Min Price (₹)", min_value=0.0, value=10.0)
    max_price = st.number_input("Max Price (₹)", min_value=0.0, value=50000.0)
    min_volume = st.number_input("Min Volume", min_value=0, value=0)

run_every = refresh_sec if auto_refresh else None

tab1, tab2, tab3 = st.tabs([
    "🔥 Stock Trend",
    "⚡ Daily Strategy Screener",
    "🟩🟥 Sectoral Heatmap",
])


def render_filterable_table(df, grid_key):
    """Renders an Excel-style table with checkbox filter menus on every column header."""
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        filter=True,
        sortable=True,
        resizable=True,
        floatingFilter=False,
        minWidth=130,
    )

    # Enable Excel-style Checkbox (Set) Filter on text/signal columns
    text_cols = df.select_dtypes(include=["object", "string"]).columns.tolist()
    for col_name in text_cols:
        gb.configure_column(
            col_name,
            filter="agSetColumnFilter",
            filterParams={
                "buttons": ["reset", "apply"],
                "closeOnApply": True,
            },
        )

    # Enable Excel-style Number Filter on numeric columns
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    for col_name in num_cols:
        gb.configure_column(
            col_name,
            filter="agNumberColumnFilter",
            filterParams={
                "buttons": ["reset", "apply"],
                "closeOnApply": True,
            },
        )

    grid_options = gb.build()

    AgGrid(
        df,
        gridOptions=grid_options,
        height=520,
        theme="balham",
        enable_enterprise_modules=True,
        columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
        key=grid_key,
    )


def _get_best_live_feed():
    """Priority: 1. Kotak Neo Live -> 2. TradingView Feed -> 3. Yahoo Finance."""
    df = fetch_kotak_live_stocks(tuple(DEFAULT_TICKERS))
    if not df.empty:
        return df, "Kotak Neo Live 🟢"

    df = fetch_tradingview_stocks(tuple(DEFAULT_TICKERS))
    if not df.empty:
        return df, "TradingView Feed 🔵"

    df = fetch_yahoo_fallback_prices(tuple(DEFAULT_TICKERS))
    if not df.empty:
        return df, "Yahoo Finance 🟡"

    return df, "Offline 🔴"


@st.fragment(run_every=run_every)
def render_screener_tab():
    ist = pytz.timezone("Asia/Kolkata")
    now_str = datetime.now(ist).strftime("%I:%M:%S %p IST")

    with st.spinner("Fetching stock prices..."):
        live_df, data_source = _get_best_live_feed()

    if live_df.empty:
        st.error("❌ Data fetch nahi ho paya.")
        return

    live_df = live_df[
        (live_df["Live Price (₹)"] >= min_price)
        & (live_df["Live Price (₹)"] <= max_price)
        & (live_df["Volume"] >= min_volume)
    ]

    with st.spinner("Calculating CPR levels..."):
        cpr_dict = fetch_all_cpr_levels(tuple(live_df["Ticker"].tolist()))
        result_df = evaluate_signals(live_df, cpr_dict)

    if result_df.empty:
        st.warning(f"⚠️ {len(live_df)} stocks mile par CPR levels calculate nahi ho paye.")
        render_filterable_table(live_df, "fallback_grid")
        return

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Scanned", len(result_df))
    m2.metric("Strong Buy 🔥", len(result_df[result_df["Result"] == "Strong Buy 🔥"]))
    m3.metric("Strong Sell 🩸", len(result_df[result_df["Result"] == "Strong Sell 🩸"]))
    m4.metric("mod Buy 📈", len(result_df[result_df["Result"] == "mod Buy 📈"]))
    m5.metric("Data Source", data_source, now_str)

    if filter_mode == "Strong Buy Only":
        display_df = result_df[result_df["Result"] == "Strong Buy 🔥"]
    elif filter_mode == "Strong Sell Only":
        display_df = result_df[result_df["Result"] == "Strong Sell 🩸"]
    elif filter_mode == "Strong / Mod / Buy":
        display_df = result_df[
            result_df["Result"].isin(["Strong Buy 🔥", "mod Buy 📈", "Buy 👍"])
        ]
    elif filter_mode == "HOLD Only":
        display_df = result_df[result_df["Result"] == "HOLD ⏳"]
    else:
        display_df = result_df

    sorted_df = display_df.sort_values(by="Volume", ascending=False)
    render_filterable_table(sorted_df, f"cpr_grid_{filter_mode}")


@st.fragment(run_every=run_every)
def render_daily_screener_tab():
    with st.spinner("Running Multi-Timeframe Daily Strategy Screener..."):
        live_df, _ = _get_best_live_feed()
        daily_df = run_daily_strategy_screener(tuple(DEFAULT_TICKERS), live_df)

    if daily_df.empty:
        st.warning("⚠️ Daily strategy data load nahi ho paya.")
        return

    d_filter = st.radio(
        "Filter Daily Setup:",
        [
            "All Stocks",
            "Daily Breakout Buy 🚀",
            "Daily Buy 📈",
        ],
        horizontal=True,
    )

    if d_filter != "All Stocks":
        daily_df = daily_df[daily_df["Daily Signal"] == d_filter]

    render_filterable_table(daily_df, f"daily_grid_{d_filter}")


@st.fragment(run_every=run_every)
def render_heatmap_tab():
    df_sectors = fetch_kotak_sectoral_indices()
    if df_sectors.empty:
        df_sectors = fetch_tradingview_sectoral_indices()

    if df_sectors.empty:
        st.warning("⚠️ Sectoral indices data abhi available nahi hai.")
        return

    df_sectors["Label"] = (
        df_sectors["Sector"]
        + "<br>"
        + df_sectors["Change (%)"].apply(lambda x: f"+{x}%" if x > 0 else f"{x}%")
    )

    fig = px.treemap(
        df_sectors,
        path=["Label"],
        values="Box_Size",
        color="Change (%)",
        color_continuous_scale=["#b71c1c", "#d32f2f", "#263238", "#2e7d32", "#1b5e20"],
        color_continuous_midpoint=0,
        range_color=[-3, 3],
    )
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=520, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)


with tab1:
    render_screener_tab()
with tab2:
    render_daily_screener_tab()
with tab3:
    render_heatmap_tab()
