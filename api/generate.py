import os
from http.server import BaseHTTPRequestHandler
import io
import json
import urllib.parse
import pandas as pd
import requests

# Auto-load .env if present
_env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip('"').strip("'")
                    if _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

BLOB_BASE_URL = "https://mywebsitecontainer.blob.core.windows.net/hft/"
BLOB_CSV_URL = "https://mywebsitecontainer.blob.core.windows.net/hft/hft.csv"
WATCHLIST_XLSX_URL = "https://mywebsitecontainer.blob.core.windows.net/hft/watchlist.xlsx"

TRACKED_HFTS = {
    "JUMP TRADING": "Jump Trading",
    "QE SECURI": "QE Securities",
    "QC SECURI": "QE Securities",
    "QE SECURITIES": "QE Securities",
    "QC SECURITIES": "QE Securities",
    "JUNOMON": "Junomoneta",
    "NK SECURI": "NK Securities",
    "HRTI PRIV": "HRTI",
    "GRAVITON": "Graviton",
    "SHARE INDIA": "Share India",
    "ELIXIR WE": "Elixir Wealth",
    "MICROCU": "Microcurves",
    "ALPHA GRE": "Alpha Alternatives",
    "ALPHAGREP": "Alpha Alternatives",
    "BLITZQUA": "Blitzkraft",
    "IRAGE": "iRage Capital",
    "MATHISYS": "Mathisys",
    "MUSIGMA": "Musigma",
    "SILVERLEAF": "Silverleaf",
    "AAKRAYA": "Aakraya Research",
    "NEO APEX": "Neo Apex",
}

_WATCHLIST_CACHE = None
_RAW_DF_CACHE = {}
_GROUPED_CACHE = {}


def normalize_fy(year_str):
    if not year_str:
        return "2026-27"
    y = str(year_str).strip()
    y_lower = y.lower()
    if "25026" in y_lower or y_lower in ["2025-25", "25-26", "2025-2026"]:
        return "2025-26"
    if y_lower in ["2026-2027", "26-27"]:
        return "2026-27"
    import re
    m = re.search(r'(\d{4}[-/]\d{2,4})', y)
    if m:
        raw = m.group(1).replace('/', '-')
        parts = raw.split('-')
        if len(parts) == 2:
            p1 = parts[0]
            p2 = parts[1] if len(parts[1]) == 2 else parts[1][-2:]
            return f"{p1}-{p2}"
        return raw
    m_short = re.search(r'(\d{2}[-/]\d{2})', y)
    if m_short:
        raw = m_short.group(1).replace('/', '-')
        parts = raw.split('-')
        return f"20{parts[0]}-{parts[1]}"
    if "25" in y_lower:
        return "2025-26"
    if "26" in y_lower:
        return "2026-27"
    return y


def match_hft(client_name):
    if not isinstance(client_name, str):
        return None
    c_upper = client_name.upper().strip()
    for key, display_name in TRACKED_HFTS.items():
        if key in c_upper:
            return display_name
    return None


def format_inr_value(val):
    if pd.isna(val) or val is None:
        return "₹0.00"
    try:
        val = float(val)
        if val >= 10000000:
            return f"₹{val / 10000000:.2f} Cr"
        elif val >= 100000:
            return f"₹{val / 100000:.2f} L"
        else:
            return f"₹{val:,.2f}"
    except Exception:
        return "₹0.00"


def fetch_watchlist(force_reload=False):
    """Fetches tracked stock symbols from Azure Blob (watchlist.xlsx)
    with optional fallback to local watchlist.xlsx file.
    Returns a set of clean uppercase symbol strings, or None if no watchlist is defined.
    """
    global _WATCHLIST_CACHE
    if not force_reload and _WATCHLIST_CACHE is not None:
        return _WATCHLIST_CACHE

    # 1. Try watchlist.xlsx from Azure Blob
    try:
        resp = requests.get(WATCHLIST_XLSX_URL, timeout=8)
        if resp.status_code == 200:
            df_wl = pd.read_excel(io.BytesIO(resp.content))
            sym_col = next((c for c in df_wl.columns if any(k in str(c).lower() for k in ["symbol", "stock", "ticker"])), df_wl.columns[0] if len(df_wl.columns) > 0 else None)
            if sym_col:
                symbols = df_wl[sym_col].dropna().astype(str).str.strip().str.upper().tolist()
                valid = {s for s in symbols if s and s != "NAN" and len(s) > 1}
                if valid:
                    _WATCHLIST_CACHE = valid
                    return valid
    except Exception:
        pass

    # 2. Local fallback (if watchlist.xlsx exists in project directory)
    local_dir = os.path.dirname(os.path.dirname(__file__))
    local_xlsx = os.path.join(local_dir, "watchlist.xlsx")

    if os.path.exists(local_xlsx):
        try:
            df_wl = pd.read_excel(local_xlsx)
            sym_col = next((c for c in df_wl.columns if any(k in str(c).lower() for k in ["symbol", "stock", "ticker"])), df_wl.columns[0] if len(df_wl.columns) > 0 else None)
            if sym_col:
                symbols = df_wl[sym_col].dropna().astype(str).str.strip().str.upper().tolist()
                valid = {s for s in symbols if s and s != "NAN" and len(s) > 1}
                if valid:
                    _WATCHLIST_CACHE = valid
                    return valid
        except Exception:
            pass

    return set()


def save_watchlist(symbols):
    """Saves the given list of symbols exclusively to watchlist.xlsx locally AND directly to Azure Blob
    if connection string or SAS token is configured.
    """
    global _WATCHLIST_CACHE
    clean_syms = sorted(list(set(str(s).strip().upper() for s in symbols if str(s).strip())))
    _WATCHLIST_CACHE = set(clean_syms)

    df = pd.DataFrame({"Symbol": clean_syms})

    # 1. Save to local watchlist.xlsx
    local_dir = os.path.dirname(os.path.dirname(__file__))
    local_xlsx = os.path.join(local_dir, "watchlist.xlsx")
    try:
        df.to_excel(local_xlsx, index=False)
    except Exception as e:
        print(f"Error saving local watchlist.xlsx: {e}")

    # Prepare excel bytes for Azure upload
    xlsx_buffer = io.BytesIO()
    df.to_excel(xlsx_buffer, index=False)
    xlsx_bytes = xlsx_buffer.getvalue()

    # 2. Save directly to Azure Blob if connection string is configured
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        try:
            from azure.storage.blob import BlobClient
            client_xlsx = BlobClient.from_connection_string(conn_str, container_name="hft", blob_name="watchlist.xlsx")
            client_xlsx.upload_blob(xlsx_bytes, overwrite=True)
            return {"status": "success", "storage": "azure_blob", "symbols": clean_syms, "file": "watchlist.xlsx"}
        except Exception as e:
            print(f"Error uploading watchlist.xlsx to Azure Blob: {e}")

    # 3. Save to Azure Blob via SAS token if configured
    sas_token = os.environ.get("AZURE_SAS_TOKEN")
    if sas_token:
        try:
            sas_clean = sas_token.lstrip("?")
            target_url_xlsx = f"{WATCHLIST_XLSX_URL}?{sas_clean}"
            requests.put(target_url_xlsx, headers={"x-ms-blob-type": "BlockBlob"}, data=xlsx_bytes, timeout=10)
            return {"status": "success", "storage": "azure_blob_sas", "symbols": clean_syms, "file": "watchlist.xlsx"}
        except Exception as e:
            print(f"Error uploading watchlist.xlsx via SAS to Azure Blob: {e}")
    return {"status": "success", "storage": "local", "symbols": clean_syms, "file": "watchlist.xlsx"}


def fetch_raw_fy_df(year="2026-27", force_reload=False):
    """Fetches raw CSV data from Azure Blob for the requested Financial Year."""
    global _RAW_DF_CACHE
    norm_year = normalize_fy(year)
    if not force_reload and norm_year in _RAW_DF_CACHE:
        return _RAW_DF_CACHE[norm_year]

    blob_name = f"{norm_year}.csv"
    raw_bytes = None

    # 1. Direct BlobClient via connection string
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        try:
            from azure.storage.blob import BlobClient
            bc = BlobClient.from_connection_string(conn_str, container_name="hft", blob_name=blob_name)
            raw_bytes = bc.download_blob().readall()
        except Exception as e:
            print(f"BlobClient download error for {blob_name}: {e}")

    # 2. HTTP Blob URL fallback
    if raw_bytes is None:
        url = f"{BLOB_BASE_URL}{blob_name}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        raw_bytes = resp.content

    df = pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8-sig")
    df.columns = [c.strip().replace('"', '').replace("'", "") for c in df.columns]

    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    symbol_col = next((c for c in df.columns if "symbol" in c.lower()), df.columns[1])
    sec_col = next((c for c in df.columns if any(k in c.lower() for k in ["security", "company", "name"])), None)
    client_col = next((c for c in df.columns if "client" in c.lower()), None)
    bs_col = next((c for c in df.columns if any(k in c.lower() for k in ["buy", "sell", "side"])), None)
    qty_col = next((c for c in df.columns if "quant" in c.lower()), None)
    price_col = next((c for c in df.columns if any(k in c.lower() for k in ["price", "trade price", "wght"])), None)
    rem_col = next((c for c in df.columns if "remark" in c.lower()), None)

    df["CleanSym"] = df[symbol_col].astype(str).str.strip().str.upper()
    df["CleanClient"] = df[client_col].astype(str).str.strip() if client_col else ""
    df["ClientUpper"] = df["CleanClient"].str.upper()
    df["CleanSide"] = df[bs_col].astype(str).str.strip().str.upper() if bs_col else "BUY"
    df["SecurityName"] = df[sec_col].astype(str).str.strip() if sec_col else df["CleanSym"]
    df["RemarksText"] = df[rem_col].astype(str).str.strip() if rem_col else "-"
    df["Matched_HFT"] = df["CleanClient"].apply(match_hft)

    # Clean numeric quantity
    if qty_col:
        qty_s = df[qty_col].astype(str).str.replace(",", "").str.strip()
        df["CleanQty"] = pd.to_numeric(qty_s, errors="coerce").fillna(0).astype(int)
    else:
        df["CleanQty"] = 0

    # Clean numeric price
    if price_col:
        price_s = df[price_col].astype(str).str.replace(",", "").str.strip()
        df["CleanPrice"] = pd.to_numeric(price_s, errors="coerce").fillna(0.0)
    else:
        df["CleanPrice"] = 0.0

    df["DealValue"] = df["CleanQty"] * df["CleanPrice"]
    df["ValueCr"] = df["DealValue"] / 10000000.0

    # Optimized date parsing
    try:
        df["ParsedDate"] = pd.to_datetime(df[date_col], format="%d-%b-%Y", errors="coerce")
    except Exception:
        df["ParsedDate"] = pd.to_datetime(df[date_col], errors="coerce")

    nan_dates = df["ParsedDate"].isna()
    if nan_dates.any():
        try:
            df.loc[nan_dates, "ParsedDate"] = pd.to_datetime(df.loc[nan_dates, date_col], format="%d-%b-%y", errors="coerce")
        except Exception:
            pass

    df["DisplayDate"] = df["ParsedDate"].dt.strftime("%B %d %Y").fillna(df[date_col].astype(str))
    df["DateFormatted"] = df["ParsedDate"].dt.strftime("%d-%b-%Y").fillna(df[date_col].astype(str))

    _RAW_DF_CACHE[norm_year] = df
    return df


def fetch_and_process_hft_data(year="2026-27"):
    """Processed BUY deal data grouped by Symbol and Date for Pine Script indicator generation."""
    global _GROUPED_CACHE
    norm_year = normalize_fy(year)
    if norm_year in _GROUPED_CACHE:
        return _GROUPED_CACHE[norm_year]

    raw_df = fetch_raw_fy_df(norm_year)
    hft_df = raw_df.dropna(subset=["Matched_HFT"]).copy()
    # Filter strictly to BUY deals for the confluence indicator
    hft_df = hft_df[hft_df["CleanSide"] == "BUY"].copy()
    hft_df = hft_df.dropna(subset=["ParsedDate"])

    grouped = (
        hft_df.groupby(["CleanSym", "ParsedDate"])["Matched_HFT"]
        .agg(lambda x: sorted(list(set(x))))
        .reset_index()
    )
    grouped = grouped.sort_values(by=["CleanSym", "ParsedDate"])
    _GROUPED_CACHE[norm_year] = grouped
    return grouped


def get_hft_entities_summary(year="2026-27", search_q=None):
    """Returns list of unique HFT/client entities with deal statistics for the dropdown."""
    df = fetch_raw_fy_df(year)
    grouped = df.groupby("CleanClient")
    
    stats = grouped.agg(
        total_deals=("CleanSym", "count"),
        buys_count=("CleanSide", lambda s: (s == "BUY").sum()),
        sells_count=("CleanSide", lambda s: (s == "SELL").sum()),
        stocks_count=("CleanSym", "nunique"),
        turnover_cr=("ValueCr", "sum"),
        matched_hft=("Matched_HFT", "first")
    ).reset_index()

    stats["turnover_cr"] = stats["turnover_cr"].round(2)
    stats = stats.sort_values(by="total_deals", ascending=False)

    q_clean = search_q.strip().upper() if search_q else None
    records = []

    # Insert default 'ALL HFTs' option at the very top of the list
    hft_mask = df["Matched_HFT"].notna()
    if hft_mask.any() and (not q_clean or any(term in q_clean for term in ["ALL", "HFT", "INSTITUTION"])):
        hft_sub = df[hft_mask]
        records.append({
            "name": "ALL HFTs (All Institutional Desks Combined)",
            "deals": len(hft_sub),
            "buys": int((hft_sub["CleanSide"] == "BUY").sum()),
            "sells": int((hft_sub["CleanSide"] == "SELL").sum()),
            "stocks": int(hft_sub["CleanSym"].nunique()),
            "turnover_cr": float(hft_sub["ValueCr"].sum().round(2)),
            "matched_hft": "ALL_HFT",
            "is_tracked": True,
            "is_all": True
        })

    for _, row in stats.iterrows():
        c_name = row["CleanClient"]
        if not c_name:
            continue
        c_upper = c_name.upper()
        if q_clean:
            if q_clean == "QC" or q_clean == "QC SECURITIES":
                if not ("QE SECURI" in c_upper or "QC SECURI" in c_upper):
                    continue
            elif q_clean not in c_upper:
                continue

        records.append({
            "name": c_name,
            "deals": int(row["total_deals"]),
            "buys": int(row["buys_count"]),
            "sells": int(row["sells_count"]),
            "stocks": int(row["stocks_count"]),
            "turnover_cr": float(row["turnover_cr"]),
            "matched_hft": row["matched_hft"] if pd.notna(row["matched_hft"]) else None,
            "is_tracked": bool(pd.notna(row["matched_hft"]))
        })
    return records


def get_hft_deals(client_query, year="2026-27", side="ALL", stock_filter=None, limit=5000):
    """Returns detailed deals for a selected HFT entity, or ALL HFTs combined, case-insensitively, with Buy & Sell data."""
    df = fetch_raw_fy_df(year)
    if not client_query:
        return {"error": "Client query required", "deals": [], "total_deals": 0}

    q = client_query.strip().upper()
    is_all_hft = (
        q in ["ALL", "ALL HFT", "ALL HFTS", "ALL_HFT", "ALL_HFTS", "ALL INSTITUTIONAL DESKS"] 
        or "ALL HFT" in q
    )

    if is_all_hft:
        sub = df[df["Matched_HFT"].notna()].copy()
        matched_name = "ALL HFTs (All Institutional Desks Combined)"
    elif "QC SECURI" in q or q == "QC":
        mask = df["ClientUpper"].str.contains("QE SECURI|QC SECURI", regex=True, na=False)
        sub = df[mask].copy()
        matched_name = sub["CleanClient"].iloc[0] if not sub.empty else client_query
    else:
        mask = (df["ClientUpper"] == q) | (df["ClientUpper"].str.contains(q, regex=False, na=False))
        sub = df[mask].copy()
        if sub.empty:
            sub = df[df["Matched_HFT"].str.upper() == q].copy()
        matched_name = sub["CleanClient"].iloc[0] if not sub.empty else client_query

    if sub.empty:
        return {
            "client_name": client_query,
            "total_deals": 0,
            "buys_count": 0,
            "sells_count": 0,
            "stocks_touched": 0,
            "total_value_cr": 0.0,
            "buy_value_cr": 0.0,
            "sell_value_cr": 0.0,
            "deals": []
        }

    total_deals = len(sub)
    buys_count = int((sub["CleanSide"] == "BUY").sum())
    sells_count = int((sub["CleanSide"] == "SELL").sum())
    stocks_touched = int(sub["CleanSym"].nunique())
    total_val = float(sub["ValueCr"].sum().round(2))
    buy_val = float(sub[sub["CleanSide"] == "BUY"]["ValueCr"].sum().round(2))
    sell_val = float(sub[sub["CleanSide"] == "SELL"]["ValueCr"].sum().round(2))

    # Apply side filter
    if side and side.upper() in ["BUY", "SELL"]:
        sub = sub[sub["CleanSide"] == side.upper()]

    # Apply stock filter
    if stock_filter:
        s_clean = stock_filter.strip().upper()
        sub = sub[sub["CleanSym"].str.contains(s_clean, regex=False, na=False)]

    # Sort: Date desc, Symbol asc, Side asc (BUY before SELL)
    sub["SideOrder"] = sub["CleanSide"].apply(lambda s: 0 if s == "BUY" else 1)
    sub = sub.sort_values(by=["ParsedDate", "CleanSym", "SideOrder"], ascending=[False, True, True])

    deals_list = []
    for _, row in sub.head(limit).iterrows():
        qty_int = int(row["CleanQty"])
        price_flt = float(row["CleanPrice"])
        val_flt = float(row["DealValue"])
        deals_list.append({
            "date": str(row["DisplayDate"]),
            "date_raw": str(row["DateFormatted"]),
            "symbol": str(row["CleanSym"]),
            "company": str(row["SecurityName"]),
            "client_name": str(row["CleanClient"]),
            "matched_hft": str(row["Matched_HFT"]) if pd.notna(row["Matched_HFT"]) else None,
            "is_hft": bool(pd.notna(row["Matched_HFT"])),
            "side": str(row["CleanSide"]),
            "quantity": qty_int,
            "quantity_formatted": f"{qty_int:,}",
            "price": price_flt,
            "price_formatted": f"{price_flt:,.2f}",
            "value_cr": round(val_flt / 10000000.0, 2),
            "value_formatted": format_inr_value(val_flt),
            "remarks": str(row["RemarksText"])
        })

    return {
        "client_name": matched_name,
        "total_deals": total_deals,
        "filtered_count": len(sub),
        "buys_count": buys_count,
        "sells_count": sells_count,
        "stocks_touched": stocks_touched,
        "total_value_cr": total_val,
        "buy_value_cr": buy_val,
        "sell_value_cr": sell_val,
        "deals": deals_list
    }


def get_market_summary(year="2026-27", period="full"):
    """Returns overview statistics for the header / market overview tab.
    period: 'full' (cumulative for the FY) or 'latest' (single latest trading day).
    """
    df = fetch_raw_fy_df(year)
    norm_year = normalize_fy(year)
    min_date = df["ParsedDate"].min()
    max_date = df["ParsedDate"].max()
    date_range_str = f"{min_date.strftime('%B %d, %Y')} to {max_date.strftime('%B %d, %Y')}" if pd.notna(min_date) and pd.notna(max_date) else norm_year
    latest_date_str = max_date.strftime('%d-%b-%Y') if pd.notna(max_date) else "NA"

    if period == "latest" and pd.notna(max_date):
        view_df = df[df["ParsedDate"] == max_date].copy()
        current_period_label = f"Latest Trading Day ({latest_date_str})"
    else:
        view_df = df.copy()
        current_period_label = f"Full Financial Year ({date_range_str})"

    top_ents = []
    ent_counts = view_df.groupby("CleanClient").agg(
        deals=("CleanSym", "count"),
        buys=("CleanSide", lambda s: (s == "BUY").sum()),
        sells=("CleanSide", lambda s: (s == "SELL").sum()),
        stocks=("CleanSym", "nunique"),
        turnover_cr=("ValueCr", "sum")
    ).reset_index().sort_values(by="deals", ascending=False).head(15)

    for _, r in ent_counts.iterrows():
        top_ents.append({
            "name": r["CleanClient"],
            "deals": int(r["deals"]),
            "buys": int(r["buys"]),
            "sells": int(r["sells"]),
            "stocks": int(r["stocks"]),
            "turnover_cr": round(float(r["turnover_cr"]), 2)
        })

    top_stks = []
    stk_counts = view_df.groupby(["CleanSym", "SecurityName"]).agg(
        deals=("CleanClient", "count"),
        buys=("CleanSide", lambda s: (s == "BUY").sum()),
        sells=("CleanSide", lambda s: (s == "SELL").sum()),
        turnover_cr=("ValueCr", "sum")
    ).reset_index().sort_values(by="deals", ascending=False).head(15)

    for _, r in stk_counts.iterrows():
        top_stks.append({
            "symbol": r["CleanSym"],
            "company": r["SecurityName"],
            "deals": int(r["deals"]),
            "buys": int(r["buys"]),
            "sells": int(r["sells"]),
            "turnover_cr": round(float(r["turnover_cr"]), 2)
        })

    return {
        "year": norm_year,
        "period": period,
        "period_label": current_period_label,
        "latest_date": latest_date_str,
        "total_deals": len(view_df),
        "total_stocks": int(view_df["CleanSym"].nunique()),
        "total_entities": int(view_df["CleanClient"].nunique()),
        "date_range": date_range_str,
        "top_entities": top_ents,
        "top_stocks": top_stks
    }


def get_top_hft_stocks(year="2026-27"):
    """Returns Top 10 stocks with multiple HFT activity and Top 10 by turnover."""
    df = fetch_raw_fy_df(year)
    norm_year = normalize_fy(year)

    # 1. Top 10 by distinct HFT desks
    hft_only = df[df["Matched_HFT"].notna()]
    top_by_hft = []
    if not hft_only.empty:
        hft_agg = hft_only.groupby(["CleanSym", "SecurityName"]).agg(
            distinct_hfts=("Matched_HFT", "nunique"),
            total_deals=("CleanSym", "count"),
            buys=("CleanSide", lambda s: (s == "BUY").sum()),
            sells=("CleanSide", lambda s: (s == "SELL").sum()),
            turnover_cr=("ValueCr", "sum"),
            hft_names=("Matched_HFT", lambda x: sorted(list(set(x))))
        ).reset_index().sort_values(by=["distinct_hfts", "total_deals"], ascending=[False, False]).head(10)

        for _, r in hft_agg.iterrows():
            top_by_hft.append({
                "symbol": r["CleanSym"],
                "company": r["SecurityName"],
                "distinct_hfts": int(r["distinct_hfts"]),
                "total_deals": int(r["total_deals"]),
                "buys": int(r["buys"]),
                "sells": int(r["sells"]),
                "turnover_cr": round(float(r["turnover_cr"]), 2),
                "hft_names": r["hft_names"]
            })

    # 2. Top 10 by highest turnover
    turnover_agg = df.groupby(["CleanSym", "SecurityName"]).agg(
        total_deals=("CleanSym", "count"),
        buys=("CleanSide", lambda s: (s == "BUY").sum()),
        sells=("CleanSide", lambda s: (s == "SELL").sum()),
        turnover_cr=("ValueCr", "sum"),
        distinct_hfts=("Matched_HFT", lambda x: x.dropna().nunique()),
        hft_deals=("Matched_HFT", lambda x: x.notna().sum())
    ).reset_index().sort_values(by="turnover_cr", ascending=False).head(10)

    top_by_turnover = []
    for _, r in turnover_agg.iterrows():
        top_by_turnover.append({
            "symbol": r["CleanSym"],
            "company": r["SecurityName"],
            "total_deals": int(r["total_deals"]),
            "buys": int(r["buys"]),
            "sells": int(r["sells"]),
            "turnover_cr": round(float(r["turnover_cr"]), 2),
            "distinct_hfts": int(r["distinct_hfts"]),
            "hft_deals": int(r["hft_deals"])
        })

    return {
        "year": norm_year,
        "top_by_hft_activity": top_by_hft,
        "top_by_turnover": top_by_turnover
    }


def get_stock_deals(symbol_query, year="2026-27", side="ALL", client_filter=None, limit=500):
    """Returns all deals for a specific stock ticker, including participating HFT desks."""
    df = fetch_raw_fy_df(year)
    if not symbol_query:
        return {"error": "Symbol required", "deals": [], "total_deals": 0}

    sym = symbol_query.strip().upper()
    sub = df[df["CleanSym"] == sym].copy()

    if sub.empty:
        return {
            "symbol": sym,
            "company": sym,
            "total_deals": 0,
            "buys_count": 0,
            "sells_count": 0,
            "hft_desks_count": 0,
            "hft_desks": [],
            "total_turnover_cr": 0.0,
            "buy_turnover_cr": 0.0,
            "sell_turnover_cr": 0.0,
            "deals": []
        }

    company_name = sub["SecurityName"].iloc[0]
    total_deals = len(sub)
    buys_count = int((sub["CleanSide"] == "BUY").sum())
    sells_count = int((sub["CleanSide"] == "SELL").sum())
    total_val = float(sub["ValueCr"].sum().round(2))
    buy_val = float(sub[sub["CleanSide"] == "BUY"]["ValueCr"].sum().round(2))
    sell_val = float(sub[sub["CleanSide"] == "SELL"]["ValueCr"].sum().round(2))

    hft_desks_set = sorted(list(set(sub["Matched_HFT"].dropna().unique())))

    if side and side.upper() in ["BUY", "SELL"]:
        sub = sub[sub["CleanSide"] == side.upper()]
    elif side and side.upper() == "HFT":
        sub = sub[sub["Matched_HFT"].notna()]

    if client_filter:
        c_clean = client_filter.strip().upper()
        sub = sub[sub["ClientUpper"].str.contains(c_clean, regex=False, na=False)]

    sub["SideOrder"] = sub["CleanSide"].apply(lambda s: 0 if s == "BUY" else 1)
    sub = sub.sort_values(by=["ParsedDate", "SideOrder"], ascending=[False, True])

    deals_list = []
    for _, row in sub.head(limit).iterrows():
        qty_int = int(row["CleanQty"])
        price_flt = float(row["CleanPrice"])
        val_flt = float(row["DealValue"])
        deals_list.append({
            "date": str(row["DisplayDate"]),
            "date_raw": str(row["DateFormatted"]),
            "client_name": str(row["CleanClient"]),
            "matched_hft": str(row["Matched_HFT"]) if row["Matched_HFT"] else None,
            "is_hft": bool(row["Matched_HFT"]),
            "side": str(row["CleanSide"]),
            "quantity": qty_int,
            "quantity_formatted": f"{qty_int:,}",
            "price": price_flt,
            "price_formatted": f"{price_flt:,.2f}",
            "value_cr": round(val_flt / 10000000.0, 2),
            "value_formatted": format_inr_value(val_flt),
            "remarks": str(row["RemarksText"])
        })

    return {
        "symbol": sym,
        "company": company_name,
        "total_deals": total_deals,
        "filtered_count": len(sub),
        "buys_count": buys_count,
        "sells_count": sells_count,
        "hft_desks_count": len(hft_desks_set),
        "hft_desks": hft_desks_set,
        "total_turnover_cr": total_val,
        "buy_turnover_cr": buy_val,
        "sell_turnover_cr": sell_val,
        "deals": deals_list
    }


def generate_pinescript(grouped, selected_syms=None, year="2026-27"):
    norm_year = normalize_fy(year)
    indicator_title = f"HFT Tracker [{norm_year}]"

    # Fallback to Azure Blob / local watchlist if no explicit symbols were selected
    if not selected_syms:
        wl = fetch_watchlist()
        if wl:
            selected_syms = list(wl)

    if selected_syms and "ALL" not in [s.upper() for s in selected_syms]:
        selected_set = set(s.upper() for s in selected_syms)
        filtered_group = grouped[grouped["CleanSym"].isin(selected_set)]
    else:
        filtered_group = grouped

    symbols_in_data = sorted(filtered_group["CleanSym"].unique().tolist())
    if not symbols_in_data:
        return f"""//@version=5
indicator("{indicator_title}", overlay=true)
// No HFT bulk BUY deals found for the selected symbols.
"""

    stock_branches = []
    first = True
    BATCH_SIZE = 40

    for sym in symbols_in_data:
        sub = filtered_group[filtered_group["CleanSym"] == sym].sort_values(by="ParsedDate")
        if sub.empty:
            continue

        keyword = "if" if first else "else if"
        first = False

        t_list = [str(int(pd.Timestamp(dt).timestamp() * 1000)) for dt in sub["ParsedDate"]]
        c_list = [str(len(f)) for f in sub["Matched_HFT"]]
        f_list = ["\\n".join(f) for f in sub["Matched_HFT"]]

        stock_branches.append(f'    {keyword} curSym == "{sym}"')
        for b_idx in range(0, len(t_list), BATCH_SIZE):
            b_t = ",".join(t_list[b_idx:b_idx + BATCH_SIZE])
            b_c = ",".join(c_list[b_idx:b_idx + BATCH_SIZE])
            b_f = "~".join(f_list[b_idx:b_idx + BATCH_SIZE])
            stock_branches.append(f'        f_loadData(dealTimes, dealCount, dealFirms, "{b_t}", "{b_c}", "{b_f}")')

    data_loading_block = "\n".join(stock_branches)

    script = f"""//@version=5
indicator("{indicator_title}", overlay=true, max_labels_count=500, max_lines_count=500, max_boxes_count=500)

// ================================================================
// 1. CONFIGURATION & INPUTS
// ================================================================
grp_v20 = "V20: Price Action Pulse Setup"
show_v20_chart        = input.bool(true,  title="Draw V20 Lines & Boxes on Chart?", group=grp_v20)
percentThreshold      = input.float(0.20, title="Pulse Move Threshold (0.20 = 20%)", step=0.01, group=grp_v20)
useMACondition        = input.bool(false, title="Require Start Low Below 200 SMA?", group=grp_v20)
maLength              = input.int(200,    title="SMA Length", group=grp_v20)
useCloseForExit       = input.bool(false, title="Use Close for Target Exit (vs High)?", group=grp_v20)
rectOpacity           = input.int(20,     title="Streak Box Opacity (0-100)", group=grp_v20)
v20ProximityThreshold = input.float(4.0,  title="Radar Proximity Threshold %", group=grp_v20)
v20TolerancePct       = input.float(1.5,  title="Pullback Entry Tolerance %", minval=0.0, maxval=5.0, step=0.5, group=grp_v20, tooltip="Allows entry if price tests within this % above the base support level (e.g. 1.5% buffer).")
maxLineAgeYears       = input.int(2,      title="Max Setup Age (Years)", group=grp_v20)
env_lookback_years    = input.int(10,     title="Backtest Lookback Window (Years)", minval=1, maxval=25, group=grp_v20)

grp_hft = "HFT Buying Confluence Filters"
requireHftInStreak    = input.bool(true,  title="Require HFT Buying in Surge Streak (Filter Non-HFT)", group=grp_hft, tooltip="When enabled, V20 setups are ONLY generated if tracked HFT desks bought during any candle of the 20%+ surge. Non-HFT setups are removed.")
requireHftOnTrigger   = input.bool(false, title="Require HFT Buying on Pullback Entry Day?", group=grp_hft, tooltip="If enabled, price must test the entry level AND HFT desks must be buying on that day.")
showHftLabels         = input.bool(true,  title="Show HFT Bulk Deal Labels on Chart?", group=grp_hft)
hftMinDealsToPlot     = input.int(1,      title="Min HFT Desks to Show Deal Label", minval=1, maxval=12, group=grp_hft, tooltip="Filter out minor deals to declutter chart. Set 2 or 3 for only major clusters.")
showHftBg             = input.bool(true,  title="Show HFT Cluster Day Background?", group=grp_hft)
showDashboard         = input.bool(true,  title="Show V20 + HFT Performance Dashboard?", group="Dashboard")

// Colors
color tvGreen = #388E3C
color tvRed   = #D32F2F

// ================================================================
// 2. HFT DATA PARSER & PER-STOCK DATA INGESTION (NO DUPLICATES)
// ================================================================
f_split(str, sep) =>
    string[] res = array.new_string(0)
    int start = 0
    int len = str.length(str)
    for i = 0 to len - 1
        if str.substring(str, i, i + 1) == sep
            array.push(res, str.substring(str, start, i))
            start := i + 1
    if start <= len
        array.push(res, str.substring(str, start, len))
    res

f_loadData(int[] tArr, int[] cArr, string[] fArr, string tStr, string cStr, string fStr) =>
    string[] tSplit = f_split(tStr, ",")
    string[] cSplit = f_split(cStr, ",")
    string[] fSplit = f_split(fStr, "~")
    int sz = array.size(tSplit)
    if sz > 0
        for i = 0 to sz - 1
            array.push(tArr, int(str.tonumber(array.get(tSplit, i))))
            array.push(cArr, int(str.tonumber(array.get(cSplit, i))))
            array.push(fArr, array.get(fSplit, i))

var int[]    dealTimes = array.new_int(0)
var int[]    dealCount = array.new_int(0)
var string[] dealFirms = array.new_string(0)

if barstate.isfirst
    string curSym = str.upper(syminfo.ticker)
    int colonPos = str.pos(curSym, ":")
    if colonPos >= 0
        curSym := str.substring(curSym, colonPos + 1)

{data_loading_block}

// ================================================================
// 3. HFT SIGNAL DETECTION ON CURRENT BAR
// ================================================================
int curBarDayStart = timestamp("UTC", year(time, syminfo.timezone), month(time, syminfo.timezone), dayofmonth(time, syminfo.timezone), 0, 0, 0)
int dealIdx = array.size(dealTimes) > 0 ? array.binary_search(dealTimes, curBarDayStart) : -1
bool isHftToday = dealIdx >= 0
int tCount = isHftToday ? array.get(dealCount, dealIdx) : 0
string fList = isHftToday ? array.get(dealFirms, dealIdx) : ""

// HFT Subtle Background Tint
color cCol = tCount >= 6 ? #00C853 : #2E7D32
color hftBgColor = (showHftBg and isHftToday) ? color.new(cCol, 92) : na
bgcolor(hftBgColor, title="HFT Cluster Buying Day")

// Sleek Compact HFT Label (Tiny badge below bar, never overlaps candles)
if showHftLabels and isHftToday and (tCount >= hftMinDealsToPlot)
    bool shouldPlotHft = timeframe.isdaily or (timeframe.isintraday and ta.change(time("D")))
    if shouldPlotHft
        string tip = "HFT BULK BUY\\nStock: " + syminfo.ticker + "\\nDate: " + str.format_time(time, "dd-MM-yyyy", syminfo.timezone) + "\\nTraders: " + str.tostring(tCount) + " of 12 Desks\\n----\\n" + fList
        label.new(x=bar_index, y=na, yloc=yloc.belowbar, text="▲" + str.tostring(tCount), style=label.style_label_up, color=cCol, textcolor=color.white, size=size.tiny, tooltip=tip)

// ================================================================
// 4. V20 PRICE ACTION PULSE ENGINE (FILTERED BY HFT BUYING)
// ================================================================
var float startCandleLow          = na
var int   startLowTime            = na
var float maAtStartLow            = na
var float endCandleHigh           = na
var float endCandleClose          = na
var int   count                   = 0
var bool  validStreak             = false
var float percentageMove          = na
var float streakFirstOpen         = na
var float streakLastClose         = na
var bool  hasPositiveOverallMove  = false
var bool  streakHadHftBuy         = false
var box   v20Rect                 = na

var float[] v20_entries           = array.new_float(0)
var float[] v20_exits             = array.new_float(0)
var int[]   v20_startDates        = array.new_int(0)
var int[]   v20_formTimes         = array.new_int(0)
var bool[]  v20_active            = array.new_bool(0)
var bool[]  v20_done              = array.new_bool(0)
var int[]   v20_trigTimes         = array.new_int(0)
var int[]   v20_ids               = array.new_int(0)
var int     v20_setup_counter     = 0

var int   v20_totalWins           = 0
var int   v20_totalTrades         = 0
var int   v20_totalHoldDays       = 0
var int   v20_lastCompletedDate   = na
var float v20_lastEntryPrice      = na
var int   v20_lastEntryDate       = na
var float v20_lastTargetPrice     = na
var bool  v20_lastWin             = false
var int   v20_lastSetupId         = na
var int   v20_lastHoldDaysCount   = na

maFilter = ta.sma(close, maLength)
isGreen  = close >= open

checkOverallMovement(firstOpen, lastClose) =>
    if na(firstOpen) or na(lastClose)
        false
    else
        overallMove = ((lastClose - firstOpen) / firstOpen) * 100.0
        overallMove >= 0.5

// Streak Completion (Streak Breaks or Turns Non-Green)
if not isGreen
    // Check if climax / break day candle had HFT buying
    if isHftToday and validStreak
        streakHadHftBuy := true

    if count > 0 and not na(streakFirstOpen) and not na(streakLastClose)
        hasPositiveOverallMove := checkOverallMovement(streakFirstOpen, streakLastClose)

    // CRITICAL HFT FILTER: Remove V20 setups where HFT did not buy during any candle of the 20%+ surge
    bool passesHftFilter = not requireHftInStreak or streakHadHftBuy

    if validStreak and not na(percentageMove) and percentageMove >= (percentThreshold * 100.0) and hasPositiveOverallMove and passesHftFilter
        patternMeetsMA = not useMACondition or (startCandleLow < maAtStartLow)
        if patternMeetsMA
            v20_setup_counter += 1
            float entryPrice = startCandleLow
            float exitLevel  = useCloseForExit ? endCandleClose : endCandleHigh

            array.push(v20_entries, entryPrice)
            array.push(v20_exits, exitLevel)
            array.push(v20_startDates, startLowTime)
            array.push(v20_formTimes, time)
            array.push(v20_active, false)
            array.push(v20_done, false)
            array.push(v20_trigTimes, 0)
            array.push(v20_ids, v20_setup_counter)

            if show_v20_chart
                string tip = "V20+HFT Setup #" + str.tostring(v20_setup_counter) + "\\nEntry: " + str.tostring(entryPrice, "#.##") + "\\nTarget: " + str.tostring(exitLevel, "#.##")
                label.new(x=bar_index, y=na, yloc=yloc.belowbar, text="📐 #" + str.tostring(v20_setup_counter), style=label.style_label_up, color=#1565C0, textcolor=color.white, size=size.tiny, tooltip=tip)

    if not na(v20Rect)
        v20Rect := na

    count := 0
    validStreak := false
    startCandleLow := na
    startLowTime := na
    endCandleHigh := na
    endCandleClose := na
    percentageMove := na
    streakFirstOpen := na
    streakLastClose := na
    hasPositiveOverallMove := false
    streakHadHftBuy := false

// Streak Progression (Green Candle)
if isGreen
    if na(startCandleLow)
        startCandleLow := low
        startLowTime   := time
        maAtStartLow   := maFilter
        streakFirstOpen:= open
        streakHadHftBuy:= isHftToday
    else
        // Check if ANY candle of the 20%+ move has HFT buying
        if isHftToday
            streakHadHftBuy := true

    count += 1
    endCandleHigh  := math.max(nz(endCandleHigh, low), high)
    endCandleClose := close
    streakLastClose:= close

    percentageMove := ((endCandleHigh - startCandleLow) / startCandleLow) * 100.0
    if percentageMove >= (percentThreshold * 100.0) or ((high - low) / low) >= percentThreshold
        validStreak := true

    currentPatternMeetsMA = not useMACondition or (startCandleLow < maAtStartLow)
    if not na(streakFirstOpen) and not na(streakLastClose)
        hasPositiveOverallMove := checkOverallMovement(streakFirstOpen, streakLastClose)

    bool meetsHftConfluence = not requireHftInStreak or streakHadHftBuy
    if validStreak and currentPatternMeetsMA and hasPositiveOverallMove and meetsHftConfluence and show_v20_chart
        if na(v20Rect)
            v20Rect := box.new(left=bar_index - count + 1, top=endCandleHigh, right=bar_index, bottom=startCandleLow, border_color=na, bgcolor=color.new(color.green, 100 - rectOpacity))
        else
            box.set_right(v20Rect, bar_index)
            box.set_top(v20Rect, endCandleHigh)
            box.set_bottom(v20Rect, startCandleLow)

// ================================================================
// 5. V20 TRADE SIMULATION & BACKTEST (PULLBACK ENTRY & TARGET HIT)
// ================================================================
int capMs = maxLineAgeYears * 365 * 24 * 60 * 60 * 1000
int globalCutoffTime = timenow - (env_lookback_years * 365 * 24 * 60 * 60 * 1000)
int totalV20Setups = array.size(v20_entries)

if totalV20Setups > 0
    for i = 0 to totalV20Setups - 1
        int   fTime    = array.get(v20_formTimes, i)
        float entryLvl = array.get(v20_entries, i)
        float exitLvl  = array.get(v20_exits, i)
        bool  inTrade  = array.get(v20_active, i)
        bool  isDone   = array.get(v20_done, i)
        int   tTime    = array.get(v20_trigTimes, i)
        int   sId      = array.get(v20_ids, i)

        if isDone or (not inTrade and (time - fTime > capMs))
            continue

        // Pullback Entry Trigger
        if not inTrade and not isDone and (fTime >= globalCutoffTime) and (time > fTime)
            bool triggerAllowed = not requireHftOnTrigger or isHftToday
            float maxEntryZone = entryLvl * (1.0 + (v20TolerancePct / 100.0))
            if low <= maxEntryZone and triggerAllowed
                array.set(v20_active, i, true)
                array.set(v20_trigTimes, i, time)
                tTime   := time
                inTrade := true
                if show_v20_chart
                    label.new(x=bar_index, y=na, yloc=yloc.belowbar, text="🟢 BUY #" + str.tostring(sId) + "\\n₹" + str.tostring(close, "#.##"), style=label.style_label_up, color=#2E7D32, textcolor=color.white, size=size.small)

        // Target Hit Detection
        if inTrade and (time > tTime)
            int holdDays = math.max(1, math.round((time - tTime) / (1000 * 60 * 60 * 24)))
            if high >= exitLvl
                v20_totalTrades   += 1
                v20_totalWins     += 1
                v20_totalHoldDays += holdDays

                if na(v20_lastCompletedDate) or (time >= v20_lastCompletedDate and tTime >= nz(v20_lastEntryDate, 0))
                    v20_lastCompletedDate := time
                    v20_lastEntryPrice    := entryLvl
                    v20_lastEntryDate     := tTime
                    v20_lastTargetPrice   := exitLvl
                    v20_lastWin           := true
                    v20_lastSetupId       := sId
                    v20_lastHoldDaysCount := holdDays

                array.set(v20_active, i, false)
                array.set(v20_done, i, true)
                if show_v20_chart
                    label.new(x=bar_index, y=na, yloc=yloc.abovebar, text="🎯 TARGET #" + str.tostring(sId) + "\\n₹" + str.tostring(exitLvl, "#.##") + " (" + str.tostring(holdDays) + "d)", style=label.style_label_down, color=color.purple, textcolor=color.white, size=size.small)

// ================================================================
// 6. ACTIVE & NEARBY OPPORTUNITY SUPPORT LINES
// ================================================================
float activeEntry        = na
float activeTarget       = na
int   activeDate         = na
int   activeId           = na
bool  isAnyV20InTrade    = false
float minActiveDist      = 100000000.0

int   nearestOppId       = na
float nearestOppEntry    = na
float nearestOppTarget   = na
int   nearestOppFormDate = na
float minOppDist         = 100000000.0
bool  isNearInRadar      = false
int   totalPendingCount  = 0
string pendingIdsStr     = ""

if totalV20Setups > 0
    for i = 0 to totalV20Setups - 1
        bool  isActive = array.get(v20_active, i)
        bool  isDone   = array.get(v20_done, i)
        int   fTime    = array.get(v20_formTimes, i)
        float entryLvl = array.get(v20_entries, i)
        float exitLvl  = array.get(v20_exits, i)
        int   sId      = array.get(v20_ids, i)
        int   tTime    = array.get(v20_trigTimes, i)

        // Track active trade
        if isActive
            isAnyV20InTrade := true
            float dist = math.abs(close - entryLvl)
            if dist < minActiveDist
                minActiveDist := dist
                activeEntry   := entryLvl
                activeTarget  := exitLvl
                activeDate    := tTime
                activeId      := sId

        // Track pending opportunities
        if not isDone and not isActive and (time - fTime <= capMs)
            totalPendingCount += 1
            pendingIdsStr := (pendingIdsStr == "" ? "" : pendingIdsStr + ", ") + "#" + str.tostring(sId)
            float distToClose = math.abs(close - entryLvl)
            if distToClose < minOppDist
                minOppDist         := distToClose
                nearestOppId       := sId
                nearestOppEntry    := entryLvl
                nearestOppTarget   := exitLvl
                nearestOppFormDate := fTime

    // Check if nearest pending opportunity is within Radar Proximity threshold
    if not na(nearestOppEntry)
        float uBuff = nearestOppEntry * (1.0 + (v20ProximityThreshold / 100.0))
        float lBuff = nearestOppEntry * (1.0 - (v20ProximityThreshold / 100.0))
        if ((close <= uBuff and close >= lBuff) or (low <= uBuff and high >= lBuff))
            isNearInRadar := true

var line v20_entry_line  = na
var line v20_target_line = na

if show_v20_chart
    line.delete(v20_entry_line)
    line.delete(v20_target_line)
    // If active trade exists, show active trade levels; else show nearest opportunity levels!
    float lineEnt = isAnyV20InTrade ? activeEntry : nearestOppEntry
    float lineTgt = isAnyV20InTrade ? activeTarget : nearestOppTarget
    if not na(lineEnt)
        color entColor = isAnyV20InTrade ? color.green : (isNearInRadar ? color.orange : color.blue)
        v20_entry_line  := line.new(x1=bar_index - 40, y1=lineEnt, x2=bar_index + 10, y2=lineEnt, color=entColor, width=2)
        v20_target_line := line.new(x1=bar_index - 40, y1=lineTgt, x2=bar_index + 10, y2=lineTgt, color=color.red,   width=2)

// ================================================================
// 7. ACTIONABLE V20 + HFT RADAR & PERFORMANCE DASHBOARD
// ================================================================
float v20_winRate = v20_totalTrades > 0 ? (v20_totalWins / v20_totalTrades) * 100.0 : 0.0
int   v20_avgHold = v20_totalTrades > 0 ? math.round(v20_totalHoldDays / v20_totalTrades) : 0

var table infoTbl = table.new(position=position.top_right, columns=2, rows=11, bgcolor=#161B22, border_width=1, border_color=#30363D)
if showDashboard and barstate.islast
    // Header
    table.cell(infoTbl, 0, 0, "⚡ V20 + HFT Engine", bgcolor=#21262D, text_color=#58A6FF, text_size=size.small)
    table.cell(infoTbl, 1, 0, syminfo.ticker,           bgcolor=#21262D, text_color=color.white, text_size=size.small)

    // Row 1: Active Trade Status & Live PnL
    if isAnyV20InTrade
        float livePnL = not na(activeEntry) and activeEntry > 0 ? ((close - activeEntry) / activeEntry) * 100.0 : 0.0
        int openDays = not na(activeDate) ? math.max(1, math.round((time - activeDate) / (1000 * 60 * 60 * 24))) : 1
        string actStr = "#" + str.tostring(activeId) + " @ " + str.tostring(activeEntry, "#.##") + " (" + (livePnL >= 0 ? "+" : "") + str.tostring(livePnL, "#.#") + "% | " + str.tostring(openDays) + "d)"
        table.cell(infoTbl, 0, 1, "Active Trade", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 1, actStr,        bgcolor=livePnL >= 0 ? tvGreen : tvRed,  text_color=color.white, text_size=size.small)
    else
        table.cell(infoTbl, 0, 1, "Active Trade", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 1, "None",        bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 2: Active Trade Target
    if isAnyV20InTrade and not na(activeTarget)
        float remGain = not na(activeEntry) and activeEntry > 0 ? ((activeTarget - close) / close) * 100.0 : 0.0
        string tgtStr = str.tostring(activeTarget, "#.##") + " (" + (remGain >= 0 ? "+" : "") + str.tostring(remGain, "#.#") + "% to target)"
        table.cell(infoTbl, 0, 2, "Active Target", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 2, tgtStr,         bgcolor=#161B22, text_color=color.white, text_size=size.small)
    else
        table.cell(infoTbl, 0, 2, "Active Target", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 2, "NA",            bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 3: Pending Setups (Count & IDs Waiting for Pullback)
    string pendStr = totalPendingCount > 0 ? str.tostring(totalPendingCount) + " (" + pendingIdsStr + ")" : "0 Pending"
    color pendBg = totalPendingCount > 0 ? #21262D : #161B22
    table.cell(infoTbl, 0, 3, "Pending Setups", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
    table.cell(infoTbl, 1, 3, pendStr,         bgcolor=pendBg,   text_color=#58A6FF,    text_size=size.small)

    // Row 4: Nearby New Opportunity (Setup # and Entry Price based on Current Price)
    if not na(nearestOppId)
        float oppDiffPct = ((close - nearestOppEntry) / close) * 100.0
        string diffStr = str.tostring(math.abs(oppDiffPct), "#.#") + "% " + (oppDiffPct > 0 ? "below" : "above")
        string oppStr = "#" + str.tostring(nearestOppId) + " @ " + str.tostring(nearestOppEntry, "#.##") + " (" + diffStr + ")"
        color oppBg = isNearInRadar ? color.orange : #21262D
        color oppTxt = isNearInRadar ? color.black : #58A6FF
        table.cell(infoTbl, 0, 4, "Nearby Setup", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 4, oppStr,        bgcolor=oppBg,   text_color=oppTxt,      text_size=size.small)
    else
        table.cell(infoTbl, 0, 4, "Nearby Setup", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 4, "None Pending", bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 5: Opportunity Target & Potential Gain
    if not na(nearestOppId) and not na(nearestOppTarget)
        float potReward = nearestOppEntry > 0 ? ((nearestOppTarget - nearestOppEntry) / nearestOppEntry) * 100.0 : 0.0
        string potStr = str.tostring(nearestOppTarget, "#.##") + " (+" + str.tostring(potReward, "#.#") + "% pot)" + (isNearInRadar ? " [RADAR]" : "")
        table.cell(infoTbl, 0, 5, "Setup Target", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 5, potStr,        bgcolor=#161B22, text_color=color.white, text_size=size.small)
    else
        table.cell(infoTbl, 0, 5, "Setup Target", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 5, "NA",           bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 6: Last Exited Trade Outcome & Profit/Loss
    float v20_lastGain = not na(v20_lastTargetPrice) and not na(v20_lastEntryPrice) ? ((v20_lastTargetPrice - v20_lastEntryPrice) / v20_lastEntryPrice) * 100.0 : na
    int   v20_dispDays = nz(v20_lastHoldDaysCount, not na(v20_lastCompletedDate) and not na(v20_lastEntryDate) ? math.max(1, math.round((v20_lastCompletedDate - v20_lastEntryDate) / (1000 * 60 * 60 * 24))) : 0)
    if not na(v20_lastCompletedDate)
        string outcomeStr = v20_lastWin ? "Target Hit" : "Stopped"
        string lastPnlStr = "#" + str.tostring(v20_lastSetupId) + ": " + (v20_lastGain >= 0 ? "+" : "") + str.tostring(v20_lastGain, "#.#") + "% (" + str.tostring(v20_dispDays) + "d hold) - " + outcomeStr
        color  lastBg = v20_lastWin ? color.purple : color.maroon
        table.cell(infoTbl, 0, 6, "Last Trade Result", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 6, lastPnlStr,          bgcolor=lastBg,  text_color=color.white, text_size=size.small)
    else
        table.cell(infoTbl, 0, 6, "Last Trade Result", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 6, "No closed trades",  bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 7: Last Entry Price & Exit Price
    if not na(v20_lastEntryPrice) and not na(v20_lastTargetPrice)
        string priceStr = "Buy: " + str.tostring(v20_lastEntryPrice, "#.##") + " | Exit: " + str.tostring(v20_lastTargetPrice, "#.##")
        table.cell(infoTbl, 0, 7, "Last Buy / Exit", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 7, priceStr,          bgcolor=#161B22, text_color=#58A6FF, text_size=size.small)
    else
        table.cell(infoTbl, 0, 7, "Last Buy / Exit", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 7, "NA",              bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 8: Buying Date & Exit Date of Last Trade
    if not na(v20_lastCompletedDate) and not na(v20_lastEntryDate)
        string buyDateStr = str.format_time(v20_lastEntryDate, "dd-MM-yyyy", syminfo.timezone)
        string exitDateStr = str.format_time(v20_lastCompletedDate, "dd-MM-yyyy", syminfo.timezone)
        string datesStr = buyDateStr + " → " + exitDateStr
        table.cell(infoTbl, 0, 8, "Last Trade Dates", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 8, datesStr,            bgcolor=#161B22, text_color=#58A6FF, text_size=size.small)
    else
        table.cell(infoTbl, 0, 8, "Last Trade Dates", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
        table.cell(infoTbl, 1, 8, "NA",                bgcolor=#21262D, text_color=#8B949E, text_size=size.small)

    // Row 9: Historical Win Rate & Stats
    string perfStr = v20_totalTrades > 0 ? str.tostring(v20_winRate, "#") + "% (" + str.tostring(v20_totalWins) + "/" + str.tostring(v20_totalTrades) + " wins, avg " + str.tostring(v20_avgHold) + "d)" : "No Trades"
    color  perfBg  = v20_winRate >= 70 ? tvGreen : v20_winRate >= 50 ? color.orange : color.new(color.black, 40)
    table.cell(infoTbl, 0, 9, "Win Rate", bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
    table.cell(infoTbl, 1, 9, perfStr,    bgcolor=perfBg,  text_color=color.white, text_size=size.small)

    // Row 10: HFT Bulk Buy Deals
    string hftStr = array.size(dealTimes) > 0 ? str.tostring(array.size(dealTimes)) + " Deals (12 Desks)" : "No HFT Deals"
    table.cell(infoTbl, 0, 10, "HFT Buys",  bgcolor=#161B22, text_color=#8B949E, text_size=size.small)
    table.cell(infoTbl, 1, 10, hftStr,     bgcolor=#161B22, text_color=#7EE787, text_size=size.small)
"""
    return script


class handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        try:
            parsed_path = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_path.query)

            # Direct browser access without query params: redirect to the interactive UI
            accept_header = self.headers.get("Accept", "")
            if not query_params and "text/html" in accept_header:
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return

            action = query_params.get("action", [None])[0]
            selected_year = normalize_fy(query_params.get("year", ["2026-27"])[0])

            # 1. Available Financial Years
            if action == "years":
                years_data = [
                    {
                        "id": "2026-27",
                        "label": "FY 2026-27 (01-Apr-2026 to 31-Mar-2027)",
                        "short": "2026-27",
                        "is_current": True,
                        "file": "2026-27.csv"
                    },
                    {
                        "id": "2025-26",
                        "label": "FY 2025-26 (01-Apr-2025 to 31-Mar-2026)",
                        "short": "2025-26",
                        "is_current": False,
                        "file": "2025-26.csv"
                    }
                ]
                self._send_json({"status": "success", "years": years_data, "default_year": "2026-27"})
                return

            # 2. Market Overview Summary
            if action == "market_summary":
                period_param = query_params.get("period", ["full"])[0]
                summary = get_market_summary(selected_year, period=period_param)
                self._send_json({"status": "success", **summary})
                return

            # 3. HFT Entities list for dropdown
            if action == "hft_entities":
                search_q = query_params.get("q", [None])[0]
                entities = get_hft_entities_summary(selected_year, search_q=search_q)
                self._send_json({
                    "status": "success",
                    "year": selected_year,
                    "count": len(entities),
                    "entities": entities
                })
                return

            # 4. HFT Deals for a specific client entity (Includes Buy & Sell)
            if action == "hft_deals":
                client_q = query_params.get("client", query_params.get("name", [""]))[0]
                side_filter = query_params.get("side", ["ALL"])[0]
                stock_filter = query_params.get("symbol", query_params.get("stock", [None]))[0]
                limit_param = int(query_params.get("limit", [5000])[0])
                deals_data = get_hft_deals(
                    client_query=client_q,
                    year=selected_year,
                    side=side_filter,
                    stock_filter=stock_filter,
                    limit=limit_param
                )
                self._send_json({"status": "success", "year": selected_year, **deals_data})
                return

            # 4b. Top 10 Stocks by Multiple HFT Activity & Highest Turnover
            if action == "top_hft_stocks":
                stocks_summary = get_top_hft_stocks(selected_year)
                self._send_json({"status": "success", **stocks_summary})
                return

            # 4c. Deals for a specific Stock Symbol (e.g. IFCI, ANTELOPUS, JINDRILL)
            if action == "stock_deals":
                stock_sym = query_params.get("symbol", query_params.get("stock", [""]))[0]
                side_filter = query_params.get("side", ["ALL"])[0]
                client_filter = query_params.get("client", [None])[0]
                limit_param = int(query_params.get("limit", [5000])[0])
                deals_data = get_stock_deals(
                    symbol_query=stock_sym,
                    year=selected_year,
                    side=side_filter,
                    client_filter=client_filter,
                    limit=limit_param
                )
                self._send_json({"status": "success", "year": selected_year, **deals_data})
                return

            # 5. Watchlist Management Actions
            if action == "watchlist":
                wl = sorted(list(fetch_watchlist() or []))
                data = {
                    "status": "success",
                    "watchlist": wl,
                    "count": len(wl),
                    "storage": "azure_blob" if os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("AZURE_SAS_TOKEN") else "local"
                }
                self._send_json(data)
                return

            if action == "watchlist_add":
                sym_raw = query_params.get("symbol", query_params.get("symbols", [""]))[0]
                new_syms = [s.strip().upper() for s in sym_raw.split(",") if s.strip()]
                current_wl = set(fetch_watchlist() or [])
                current_wl.update(new_syms)
                save_res = save_watchlist(list(current_wl))
                data = {
                    "status": "success",
                    "watchlist": sorted(list(current_wl)),
                    "count": len(current_wl),
                    "saved_to": save_res.get("storage", "local")
                }
                self._send_json(data)
                return

            if action == "watchlist_remove":
                sym_raw = query_params.get("symbol", query_params.get("symbols", [""]))[0]
                rem_syms = set(s.strip().upper() for s in sym_raw.split(",") if s.strip())
                current_wl = set(fetch_watchlist() or [])
                current_wl.difference_update(rem_syms)
                save_res = save_watchlist(list(current_wl))
                data = {
                    "status": "success",
                    "watchlist": sorted(list(current_wl)),
                    "count": len(current_wl),
                    "saved_to": save_res.get("storage", "local")
                }
                self._send_json(data)
                return

            if action == "watchlist_clear":
                save_res = save_watchlist([])
                data = {
                    "status": "success",
                    "watchlist": [],
                    "count": 0,
                    "saved_to": save_res.get("storage", "local")
                }
                self._send_json(data)
                return

            # 6. Stocks list with summaries for Pine Script and Watchlist
            raw_df = fetch_raw_fy_df(selected_year)
            all_market_symbols = sorted(raw_df["CleanSym"].dropna().unique().tolist())
            grouped = fetch_and_process_hft_data(selected_year)
            hft_symbols = sorted(grouped["CleanSym"].unique().tolist())

            if action == "symbols" or ("symbols" not in query_params and "symbol" not in query_params and "application/json" in accept_header):
                summaries = {}
                for sym, g in grouped.groupby("CleanSym"):
                    desks_set = sorted(list(set(d for desks in g["Matched_HFT"] for d in desks)))
                    summaries[sym] = {
                        "deals": len(g),
                        "desks_count": len(desks_set),
                        "desks": desks_set,
                        "latest_date": g["ParsedDate"].max().strftime("%d-%b-%Y") if pd.notna(g["ParsedDate"].max()) else "NA",
                        "first_date": g["ParsedDate"].min().strftime("%d-%b-%Y") if pd.notna(g["ParsedDate"].min()) else "NA",
                        "is_hft": True
                    }
                response_data = {
                    "status": "success",
                    "year": selected_year,
                    "count": len(all_market_symbols),
                    "symbols": all_market_symbols,
                    "hft_count": len(hft_symbols),
                    "hft_symbols": hft_symbols,
                    "summaries": summaries,
                }
                self._send_json(response_data)
                return

            # 7. Pine Script generation for selected symbols
            selected_syms = None
            if "symbols" in query_params:
                raw = query_params["symbols"][0]
                selected_syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
            elif "symbol" in query_params:
                selected_syms = [s.strip().upper() for s in query_params["symbol"][0].split(",") if s.strip()]

            script_text = generate_pinescript(grouped, selected_syms, year=selected_year)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(script_text.encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"Error: {str(e)}".encode("utf-8"))