import os
import time
import pandas as pd
import streamlit as st

try:
    from neo_api_client import NeoAPI
except ImportError:
    NeoAPI = None

try:
    from tradingview_screener import Query
except ImportError:
    Query = None


def _get_kotak_client():
    """Initializes Kotak Neo client using consumer_key (safe if secrets.toml is missing)."""
    if NeoAPI is None:
        return None

    consumer_key = os.getenv("KOTAK_CONSUMER_KEY", "")
    if not consumer_key:
        try:
            consumer_key = st.secrets.get("KOTAK_CONSUMER_KEY", "")
        except Exception:
            consumer_key = ""

    if not consumer_key:
        return None

    try:
        return NeoAPI(consumer_key=consumer_key, environment="prod")
    except Exception:
        return None


@st.cache_data(ttl=86400)
def get_kotak_scrip_map():
    """Downloads Kotak Neo NSE Cash Scrip Master once a day to map symbols -> tokens."""
    client = _get_kotak_client()
    if client is None:
        return {}

    try:
        master_res = client.scrip_master(exchange_segment="nse_cm")
        csv_url = None
        if isinstance(master_res, str) and master_res.startswith("http"):
            csv_url = master_res
        elif isinstance(master_res, dict):
            files = master_res.get("filesPaths") or master_res.get("data") or []
            if isinstance(files, list) and files:
                csv_url = files

        if not csv_url:
            return {}

        df_master = pd.read_csv(csv_url)
        df_master.columns = [c.strip() for c in df_master.columns]

        if "pGroup" in df_master.columns:
            df_master = df_master[df_master["pGroup"].isin(["EQ", "BE"])]

        symbol_col = next(
            (c for c in ["pSymbolName", "pTrdSymbol", "pSymbol"] if c in df_master.columns),
            None,
        )
        token_col = next(
            (c for c in ["pSymbol", "pInstrumentToken", "instrument_token"] if c in df_master.columns),
            None,
        )

        if not symbol_col or not token_col:
            return {}

        token_map = {}
        for _, row in df_master.iterrows():
            raw_sym = str(row[symbol_col]).replace("-EQ", "").strip().upper()
            tok = str(row[token_col]).strip()
            if raw_sym and tok:
                token_map[raw_sym] = tok
        return token_map
    except Exception:
        return {}


@st.cache_data(ttl=8)
def fetch_kotak_live_stocks(tickers_tuple):
    """Fetches real-time quotes from Kotak Neo API in batches of 50."""
    client = _get_kotak_client()
    token_map = get_kotak_scrip_map()
    if client is None or not token_map:
        return pd.DataFrame()

    req_tokens = []
    token_to_symbol = {}
    for t in tickers_tuple:
        sym = t.replace(".NS", "").upper()
        tok = token_map.get(sym)
        if tok:
            req_tokens.append({"instrument_token": tok, "exchange_segment": "nse_cm"})
            token_to_symbol[tok] = sym

    if not req_tokens:
        return pd.DataFrame()

    stocks = []
    try:
        for i in range(0, len(req_tokens), 50):
            batch = req_tokens[i : i + 50]
            resp = client.quotes(instrument_tokens=batch, quote_type="all")
            quote_list = resp if isinstance(resp, list) else resp.get("data", [])

            for q in quote_list:
                tok = str(q.get("instrument_token", "")).strip()
                sym = token_to_symbol.get(tok)
                if not sym:
                    continue

                ltp = float(q.get("ltp") or q.get("last_price") or 0.0)
                ohlc = q.get("ohlc") or {}
                prev_close = float(ohlc.get("close") or q.get("prev_close") or ltp)
                pct_change = (
                    ((ltp - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
                )
                vol = int(q.get("volume") or q.get("total_traded_volume") or 0)

                if ltp > 0:
                    stocks.append({
                        "Symbol": sym,
                        "Ticker": f"{sym}.NS",
                        "Live Price (₹)": round(ltp, 2),
                        "Change (%)": round(pct_change, 2),
                        "Volume": vol,
                    })
            time.sleep(0.05)
        return pd.DataFrame(stocks)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=15)
def fetch_tradingview_stocks(tickers_tuple):
    """Fetches NSE stock prices & volume from TradingView Scanner in a single fast call."""
    if Query is None or not tickers_tuple:
        return pd.DataFrame()

    tv_symbols = [f"NSE:{t.replace('.NS', '').upper()}" for t in tickers_tuple]
    try:
        _, df_tv = (
            Query()
            .set_markets("india")
            .select("name", "close", "change", "volume")
            .set_tickers(*tv_symbols)
            .limit(len(tv_symbols))
            .get_scanner_data()
        )
        if df_tv is None or df_tv.empty:
            return pd.DataFrame()

        stocks = []
        for _, row in df_tv.iterrows():
            sym = str(row.get("name", "")).strip().upper()
            ltp = float(row.get("close") or 0.0)
            pct = float(row.get("change") or 0.0)
            vol = int(row.get("volume") or 0)
            if sym and ltp > 0:
                stocks.append({
                    "Symbol": sym,
                    "Ticker": f"{sym}.NS",
                    "Live Price (₹)": round(ltp, 2),
                    "Change (%)": round(pct, 2),
                    "Volume": vol,
                })
        return pd.DataFrame(stocks)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=15)
def fetch_tradingview_sectoral_indices():
    """Fetches NSE Sectoral Indices from TradingView Scanner."""
    if Query is None:
        return pd.DataFrame()

    sector_tickers = [
        "NSE:BANKNIFTY",
        "NSE:CNXIT",
        "NSE:CNXAUTO",
        "NSE:CNXPHARMA",
        "NSE:CNXFMCG",
        "NSE:CNXMETAL",
        "NSE:CNXENERGY",
        "NSE:CNXREALTY",
        "NSE:CNXPSUBANK",
        "NSE:CNXMEDIA",
        "NSE:CNXFINANCE",
    ]
    try:
        _, df_tv = (
            Query()
            .set_markets("india")
            .select("name", "description", "close", "change", "open", "high", "low")
            .set_tickers(*sector_tickers)
            .limit(len(sector_tickers))
            .get_scanner_data()
        )
        if df_tv is None or df_tv.empty:
            return pd.DataFrame()

        sectors = []
        for _, row in df_tv.iterrows():
            name = str(row.get("description") or row.get("name") or "N/A").upper()
            ltp = float(row.get("close") or 0.0)
            pct = float(row.get("change") or 0.0)
            prev_close = ltp / (1 + (pct / 100.0)) if (1 + pct / 100.0) != 0 else ltp
            var = ltp - prev_close
            if ltp > 0:
                sectors.append({
                    "Sector": name,
                    "Last Price": round(ltp, 2),
                    "Points Change": round(var, 2),
                    "Change (%)": round(pct, 2),
                    "Open": float(row.get("open") or 0.0),
                    "High": float(row.get("high") or 0.0),
                    "Low": float(row.get("low") or 0.0),
                    "Box_Size": max(abs(pct), 0.15),
                })
        return pd.DataFrame(sectors)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=12)
def fetch_kotak_sectoral_indices():
    """Fetches live NSE Sectoral Indices via Kotak Neo quotes API."""
    client = _get_kotak_client()
    if client is None:
        return pd.DataFrame()

    sector_indices = [
        "Nifty Bank",
        "Nifty IT",
        "Nifty Auto",
        "Nifty Pharma",
        "Nifty FMCG",
        "Nifty Metal",
        "Nifty Energy",
        "Nifty Realty",
        "Nifty PSU Bank",
        "Nifty Media",
        "Nifty Fin Service",
    ]
    tokens = [
        {"instrument_token": idx, "exchange_segment": "nse_cm"}
        for idx in sector_indices
    ]

    try:
        resp = client.quotes(instrument_tokens=tokens, quote_type="all")
        quote_list = resp if isinstance(resp, list) else resp.get("data", [])
        sectors = []
        for q in quote_list:
            name = q.get("display_symbol") or q.get("instrument_token") or "N/A"
            ltp = float(q.get("ltp") or q.get("last_price") or 0.0)
            ohlc = q.get("ohlc") or {}
            prev_close = float(ohlc.get("close") or ltp)
            var = ltp - prev_close
            pct = (var / prev_close) * 100 if prev_close > 0 else 0.0

            if ltp > 0:
                sectors.append({
                    "Sector": name.upper(),
                    "Last Price": round(ltp, 2),
                    "Points Change": round(var, 2),
                    "Change (%)": round(pct, 2),
                    "Open": float(ohlc.get("open", 0.0)),
                    "High": float(ohlc.get("high", 0.0)),
                    "Low": float(ohlc.get("low", 0.0)),
                    "Box_Size": max(abs(pct), 0.15),
                })
        return pd.DataFrame(sectors)
    except Exception:
        return pd.DataFrame()
