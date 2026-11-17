from datetime import datetime
import pandas as pd
import pytz
import streamlit as st
import yfinance as yf


def get_cpr_bounds(df_resampled, cutoff_date):
    if df_resampled.empty:
        return None, None, "N/A"

    completed = df_resampled[df_resampled.index.date < cutoff_date]
    if completed.empty:
        return None, None, "N/A"

    prev = completed.iloc[-1]
    h, l, c = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    p = (h + l + c) / 3
    bc = (h + l) / 2
    tc = (p - bc) + p
    top_cpr = max(tc, bc, p)
    bottom_cpr = min(tc, bc, p)
    return (
        top_cpr,
        bottom_cpr,
        f"P:{round(p, 1)} [BC:{round(bc, 1)}-TC:{round(tc, 1)}]",
    )


@st.cache_data(ttl=3600)
def fetch_all_cpr_levels(tickers_tuple):
    cpr_map = {}
    if not tickers_tuple:
        return cpr_map

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute >= 30):
        cutoff_date = (now_ist + pd.Timedelta(days=1)).date()
    else:
        cutoff_date = now_ist.date()

    try:
        bulk_df = yf.download(
            list(tickers_tuple), period="6mo", group_by="ticker", progress=False
        )
        for t in tickers_tuple:
            try:
                df = bulk_df[t] if len(tickers_tuple) > 1 else bulk_df
                df = df.dropna(subset=["Close"])
                if len(df) < 5:
                    continue

                daily_top, daily_bottom, daily_str = get_cpr_bounds(
                    df, cutoff_date
                )
                if daily_top is None:
                    continue

                df_w = df.resample("W-FRI").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                }).dropna()
                weekly_top, weekly_bottom, weekly_str = get_cpr_bounds(
                    df_w, cutoff_date
                )

                df_m = df.resample("ME").agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                }).dropna()
                monthly_top, monthly_bottom, monthly_str = get_cpr_bounds(
                    df_m, cutoff_date
                )

                cpr_map[t] = {
                    "daily_top": daily_top,
                    "daily_bottom": daily_bottom,
                    "daily_str": daily_str,
                    "weekly_top": weekly_top,
                    "weekly_bottom": weekly_bottom,
                    "weekly_str": weekly_str,
                    "monthly_top": monthly_top,
                    "monthly_bottom": monthly_bottom,
                    "monthly_str": monthly_str,
                }
            except Exception:
                continue
    except Exception:
        pass
    return cpr_map


@st.cache_data(ttl=25)
def fetch_yahoo_fallback_prices(tickers_tuple):
    fallback_rows = []
    if not tickers_tuple:
        return pd.DataFrame()

    try:
        bulk_df = yf.download(
            list(tickers_tuple), period="5d", group_by="ticker", progress=False
        )
        for t in tickers_tuple:
            try:
                df = bulk_df[t] if len(tickers_tuple) > 1 else bulk_df
                df = df.dropna(subset=["Close"])
                if len(df) < 2:
                    continue

                latest_price = float(df["Close"].iloc[-1])
                prev_close = float(df["Close"].iloc[-2])
                pct_change = ((latest_price - prev_close) / prev_close) * 100
                volume = int(df["Volume"].iloc[-1])

                fallback_rows.append({
                    "Symbol": t.replace(".NS", ""),
                    "Ticker": t,
                    "Live Price (₹)": round(latest_price, 2),
                    "Change (%)": round(pct_change, 2),
                    "Volume": volume,
                })
            except Exception:
                continue
    except Exception:
        pass
    return pd.DataFrame(fallback_rows)


def evaluate_signals(live_df, cpr_dict):
    final_rows = []
    for _, row in live_df.iterrows():
        t = row["Ticker"]
        price = row["Live Price (₹)"]
        cpr = cpr_dict.get(t)
        if not cpr:
            continue

        d_top, d_bot = cpr["daily_top"], cpr["daily_bottom"]
        w_top, w_bot = cpr["weekly_top"], cpr["weekly_bottom"]
        m_top, m_bot = cpr["monthly_top"], cpr["monthly_bottom"]

        above_d = price > d_top
        above_w = (w_top is not None) and (price > w_top)
        above_m = (m_top is not None) and (price > m_top)

        below_d = price < d_bot
        below_w = (w_bot is not None) and (price < w_bot)
        below_m = (m_bot is not None) and (price < m_bot)

        if above_d and above_w and above_m:
            sig, trend = "Strong Buy 🔥", "Super Bullish 🚀"
        elif below_d and below_w and below_m:
            sig, trend = "Strong Sell 🩸", "Super Bearish 🐻"
        elif above_w and above_m:
            sig, trend = "mod Buy 📈", "Macro Bullish 🛰️"
        elif above_d:
            sig, trend = "Buy 👍", "Intraday Bullish ⚡"
        elif below_d and below_w:
            sig, trend = "HOLD ⏳", "Bearish 📉"
        else:
            sig, trend = "HOLD ⏳", "Sideways ➡️"

        final_rows.append({
            "Symbol": row["Symbol"],
            "Live Price (₹)": price,
            "Change (%)": row["Change (%)"],
            "Volume": row["Volume"],
            "Daily CPR": cpr["daily_str"],
            "Weekly CPR": cpr["weekly_str"],
            "Trend": trend,
            "Result": sig,
        })
    return pd.DataFrame(final_rows)
