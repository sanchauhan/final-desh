from datetime import datetime
import pandas as pd
import pytz
import streamlit as st
import yfinance as yf


def _calc_traditional_cpr(h, l, c):
    """Calculates Traditional Pivot (P), Bottom CPR, Top CPR, S1, and R1."""
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = (p - bc) + p
    top_cpr = max(tc, bc, p)
    bot_cpr = min(tc, bc, p)
    s1 = (2.0 * p) - h
    r1 = (2.0 * p) - l
    return p, bot_cpr, top_cpr, s1, r1


@st.cache_data(ttl=180)
def run_daily_strategy_screener(tickers_tuple, live_prices_df=None):
    """
    Traditional Pivot Screener aligned with TradingView AUTO CPR:
    1. Price > Daily Top CPR (15m chart CPR) & Weekly/Hourly Top CPR
    2. Yesterday's last 15m close < Yesterday's Daily CPR Bottom OR S1
    3. Today's 15m candle crossed and closed above Today's Daily Top CPR
    """
    if not tickers_tuple:
        return pd.DataFrame()

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    today_date = now_ist.date()

    live_map = {}
    if live_prices_df is not None and not live_prices_df.empty:
        for _, r in live_prices_df.iterrows():
            live_map[r["Ticker"]] = {
                "price": float(r["Live Price (₹)"]),
                "pct": float(r["Change (%)"]),
                "vol": int(r["Volume"]),
            }

    results = []
    try:
        batch_size = 50
        tickers_list = list(tickers_tuple)

        for i in range(0, len(tickers_list), batch_size):
            batch = tickers_list[i : i + batch_size]
            bulk_15m = yf.download(
                batch,
                period="1mo",
                interval="15m",
                group_by="ticker",
                progress=False,
                threads=True,
            )
            if bulk_15m.empty:
                continue

            for t in batch:
                try:
                    df_15 = bulk_15m[t] if len(batch) > 1 else bulk_15m
                    df_15 = df_15.dropna(subset=["Close"]).copy()
                    if len(df_15) < 40:
                        continue

                    if df_15.index.tz is None:
                        df_15.index = df_15.index.tz_localize("UTC").tz_convert(ist)
                    else:
                        df_15.index = df_15.index.tz_convert(ist)

                    df_15 = df_15.between_time("09:15", "15:29")
                    unique_dates = sorted(set(df_15.index.date))
                    if len(unique_dates) < 3:
                        continue

                    target_date = today_date if today_date in unique_dates else unique_dates[-1]
                    target_idx = unique_dates.index(target_date)
                    if target_idx < 2:
                        continue

                    yest_date = unique_dates[target_idx - 1]
                    day_before_date = unique_dates[target_idx - 2]

                    df_day_before = df_15[df_15.index.date == day_before_date]
                    df_yest = df_15[df_15.index.date == yest_date]
                    df_today = df_15[df_15.index.date == target_date]

                    if df_day_before.empty or df_yest.empty or df_today.empty:
                        continue

                    # 1. TODAY'S DAILY TRADITIONAL CPR (shown on 15m chart)
                    _, d_bot, d_top, d_s1, d_r1 = _calc_traditional_cpr(
                        float(df_yest["High"].max()),
                        float(df_yest["Low"].min()),
                        float(df_yest["Close"].iloc[-1]),
                    )

                    # 2. YESTERDAY'S DAILY TRADITIONAL CPR (to check yesterday's close < CPR or S1)
                    _, y_d_bot, _, y_d_s1, _ = _calc_traditional_cpr(
                        float(df_day_before["High"].max()),
                        float(df_day_before["Low"].min()),
                        float(df_day_before["Close"].iloc[-1]),
                    )

                    # 3. WEEKLY TRADITIONAL CPR (shown on 1H / Hourly chart in TradingView AUTO)
                    df_w = df_15[df_15.index.date < target_date].resample("W-FRI").agg({
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                    }).dropna()
                    if df_w.empty:
                        continue
                    prev_w = df_w.iloc[-1]
                    _, _, w_top, _, _ = _calc_traditional_cpr(
                        float(prev_w["High"]),
                        float(prev_w["Low"]),
                        float(prev_w["Close"]),
                    )

                    # Current Live Price
                    y_close = float(df_yest["Close"].iloc[-1])
                    if t in live_map:
                        curr_price = live_map[t]["price"]
                        pct_change = live_map[t]["pct"]
                    else:
                        curr_price = float(df_today["Close"].iloc[-1])
                        pct_change = ((curr_price - y_close) / y_close) * 100.0

                    # Condition Checks
                    above_daily_cpr = curr_price > d_top
                    above_weekly_hourly_cpr = curr_price > w_top
                    yest_cond_met = (y_close < y_d_bot) or (y_close < y_d_s1)

                    # Today's 15m candle crossing & closing above Today's Daily Top CPR
                    signal_triggered = False
                    sig_price = None
                    sig_time = "—"

                    for _, curr_bar in df_today.iterrows():
                        bar_open = float(curr_bar["Open"])
                        bar_low = float(curr_bar["Low"])
                        bar_close = float(curr_bar["Close"])

                        crossed_above = (min(bar_open, bar_low) <= d_top) and (bar_close > d_top)
                        if crossed_above and not signal_triggered:
                            signal_triggered = True
                            sig_price = round(bar_close, 2)
                            sig_time = curr_bar.name.strftime("%d-%b-%Y %I:%M %p")

                    if above_daily_cpr and above_weekly_hourly_cpr and yest_cond_met and signal_triggered:
                        signal = "Daily Breakout Buy 🚀"
                        setup = "Above D+W CPR | Yest <CPR/S1 | 15m Cross"
                    elif above_daily_cpr and above_weekly_hourly_cpr:
                        signal = "Daily Buy 📈"
                        setup = "Above Daily & Weekly(1H) CPR"
                        if not signal_triggered:
                            sig_price = None
                            sig_time = "—"
                    else:
                        signal = "Neutral / Wait ⏳"
                        setup = "Conditions Pending"
                        sig_price = None
                        sig_time = "—"

                    results.append({
                        "Symbol": t.replace(".NS", ""),
                        "Price (₹)": round(curr_price, 2),
                        "Change (%)": round(pct_change, 2),
                        "Daily Top CPR": round(d_top, 2),
                        "Weekly(1H) Top CPR": round(w_top, 2),
                        "Daily S1 / R1": f"{round(d_s1, 1)} / {round(d_r1, 1)}",
                        "Setup": setup,
                        "Daily Signal": signal,
                        "Signal Price (₹)": sig_price,
                        "Signal Date & Time": sig_time,
                    })
                except Exception:
                    continue
    except Exception:
        pass

    return pd.DataFrame(results)
