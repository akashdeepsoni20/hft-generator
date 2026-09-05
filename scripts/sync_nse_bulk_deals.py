#!/usr/bin/env python3
"""
Automated Daily NSE Bulk Deal Sync Script
Fetches daily bulk deal reports from the official NSE archive, tags HFT activity,
deduplicates against existing data in Azure Blob storage, and appends new deals.

Can be run locally or via GitHub Actions.
"""

import os
import sys
import io
import ssl
import argparse
from datetime import datetime
import urllib.request
import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 1. Auto-load local .env if present
_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_file = os.path.join(_root_dir, ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        print(f"[Warning] Failed reading .env file: {e}")

NSE_BULK_CSV_URL = "https://archives.nseindia.com/content/equities/bulk.csv"
CONTAINER_NAME = "hft"

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


def match_hft(client_name):
    if not isinstance(client_name, str):
        return None
    c_upper = client_name.upper().strip()
    for key, display_name in TRACKED_HFTS.items():
        if key in c_upper:
            return display_name
    return None


def fetch_nse_daily_csv():
    """Downloads today's bulk.csv file from NSE archive with browser user-agent."""
    print(f"[1/4] Fetching latest daily bulk deals from NSE: {NSE_BULK_CSV_URL}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        NSE_BULK_CSV_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            content = resp.read()
            if not content or len(content) < 50:
                raise ValueError("NSE returned empty or truncated response.")
            
            df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
            print(f"[OK] Successfully downloaded {len(df)} deal rows from NSE.")
            return df
    except Exception as e:
        print(f"[Error] Failed downloading NSE daily bulk deals: {e}")
        sys.exit(1)


def get_financial_year(dt):
    """Calculates financial year string (e.g. 2026-27) for a given datetime."""
    year = dt.year
    if dt.month >= 4:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"


def normalize_deal_df(df):
    """Normalizes column names and types to match Azure Blob standard schema."""
    df = df.copy()
    df.columns = [c.strip().replace('"', '').replace("'", "") for c in df.columns]

    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    sym_col = next((c for c in df.columns if "symbol" in c.lower()), df.columns[1])
    sec_col = next((c for c in df.columns if any(k in c.lower() for k in ["security", "company", "name"])), None)
    client_col = next((c for c in df.columns if "client" in c.lower()), None)
    bs_col = next((c for c in df.columns if any(k in c.lower() for k in ["buy", "sell", "side"])), None)
    qty_col = next((c for c in df.columns if "quant" in c.lower()), None)
    price_col = next((c for c in df.columns if any(k in c.lower() for k in ["price", "trade price", "wght"])), None)
    rem_col = next((c for c in df.columns if "remark" in c.lower()), None)

    # Standard column mapping
    standard_df = pd.DataFrame()
    standard_df["Date"] = df[date_col].astype(str).str.strip()
    standard_df["Symbol"] = df[sym_col].astype(str).str.strip().str.upper()
    standard_df["Security Name"] = df[sec_col].astype(str).str.strip() if sec_col else standard_df["Symbol"]
    standard_df["Client Name"] = df[client_col].astype(str).str.strip() if client_col else ""
    standard_df["Buy / Sell"] = df[bs_col].astype(str).str.strip().str.upper() if bs_col else "BUY"
    
    if qty_col:
        qty_s = df[qty_col].astype(str).str.replace(",", "").str.strip()
        standard_df["Quantity Traded"] = pd.to_numeric(qty_s, errors="coerce").fillna(0).astype(int)
    else:
        standard_df["Quantity Traded"] = 0

    if price_col:
        price_s = df[price_col].astype(str).str.replace(",", "").str.strip()
        standard_df["Trade Price / Wght. Avg. Price"] = pd.to_numeric(price_s, errors="coerce").fillna(0.0).round(2)
    else:
        standard_df["Trade Price / Wght. Avg. Price"] = 0.0

    standard_df["Remarks"] = df[rem_col].astype(str).str.strip() if rem_col else "-"

    # Parse date for financial year determination
    try:
        standard_df["_ParsedDate"] = pd.to_datetime(standard_df["Date"], format="%d-%b-%Y", errors="coerce")
    except Exception:
        standard_df["_ParsedDate"] = pd.to_datetime(standard_df["Date"], errors="coerce")

    nan_dates = standard_df["_ParsedDate"].isna()
    if nan_dates.any():
        try:
            standard_df.loc[nan_dates, "_ParsedDate"] = pd.to_datetime(standard_df.loc[nan_dates, "Date"], format="%d-%b-%y", errors="coerce")
        except Exception:
            pass

    return standard_df


def sync_bulk_deals(dry_run=False):
    """Main synchronization workflow."""
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        print("[Error] AZURE_STORAGE_CONNECTION_STRING environment variable is not set.")
        print("Please set it in your .env file or repository secrets.")
        sys.exit(1)

    try:
        from azure.storage.blob import BlobServiceClient
        blob_service = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service.get_container_client(CONTAINER_NAME)
    except Exception as e:
        print(f"[Error] Failed to initialize Azure Blob connection: {e}")
        sys.exit(1)

    # 1. Fetch NSE Daily File
    daily_raw_df = fetch_nse_daily_csv()
    daily_clean = normalize_deal_df(daily_raw_df)

    # 2. Determine Financial Year(s) present in daily file
    valid_dates = daily_clean["_ParsedDate"].dropna()
    if valid_dates.empty:
        print("[Error] Could not parse valid trading dates from NSE file.")
        sys.exit(1)

    latest_trade_date = valid_dates.max()
    fy_str = get_financial_year(latest_trade_date)
    blob_file_name = f"{fy_str}.csv"
    trade_date_str = latest_trade_date.strftime("%d-%b-%Y")

    print(f"[2/4] Trade Date: {trade_date_str} -> Target Financial Year: {fy_str} ({blob_file_name})")

    # 3. Download Existing Azure Blob CSV for this Financial Year
    existing_df = None
    blob_client = container_client.get_blob_client(blob_file_name)

    try:
        if blob_client.exists():
            print(f"      Downloading existing {blob_file_name} from Azure Blob...")
            raw_blob = blob_client.download_blob().readall()
            raw_existing = pd.read_csv(io.BytesIO(raw_blob), encoding="utf-8-sig")
            existing_df = normalize_deal_df(raw_existing)
            print(f"[OK] Existing {blob_file_name} currently has {len(existing_df):,} deals.")
        else:
            print(f"[Info] Blob {blob_file_name} does not exist yet. It will be created as a new file.")
            existing_df = pd.DataFrame()
    except Exception as e:
        print(f"[Error] Failed downloading existing blob {blob_file_name}: {e}")
        sys.exit(1)

    # 4. Deduplicate: Find only new deals that do not already exist in the blob
    def make_dedup_key(df_in):
        d_clean = df_in["Date"].astype(str).str.strip().str.upper()
        s_clean = df_in["Symbol"].astype(str).str.strip().str.upper()
        c_clean = df_in["Client Name"].astype(str).str.strip().str.upper()
        side_clean = df_in["Buy / Sell"].astype(str).str.strip().str.upper()
        qty_clean = df_in["Quantity Traded"].astype(str).str.replace(",", "").str.strip()
        price_clean = df_in["Trade Price / Wght. Avg. Price"].astype(str).str.replace(",", "").str.strip()
        return d_clean + "||" + s_clean + "||" + c_clean + "||" + side_clean + "||" + qty_clean + "||" + price_clean

    existing_keys = set()
    if existing_df is not None and not existing_df.empty:
        existing_keys = set(make_dedup_key(existing_df))

    daily_clean["_dedup_key"] = make_dedup_key(daily_clean)
    new_deals_mask = ~daily_clean["_dedup_key"].isin(existing_keys)
    new_deals_df = daily_clean[new_deals_mask].copy()

    # Drop helper columns before saving
    columns_to_keep = [
        "Date", "Symbol", "Security Name", "Client Name", "Buy / Sell",
        "Quantity Traded", "Trade Price / Wght. Avg. Price", "Remarks"
    ]
    new_deals_df = new_deals_df[columns_to_keep]

    print(f"[3/4] Deduplication Results for {trade_date_str}:")
    print(f"      Total deals reported by NSE today : {len(daily_clean)}")
    print(f"      Already present in Azure Blob     : {len(daily_clean) - len(new_deals_df)}")
    print(f"      New deals to append               : {len(new_deals_df)}")

    # Check for HFT desks in new deals
    hft_deals_today = []
    for _, r in new_deals_df.iterrows():
        desk = match_hft(r["Client Name"])
        if desk:
            hft_deals_today.append((desk, r["Symbol"], r["Buy / Sell"], r["Quantity Traded"]))

    if hft_deals_today:
        print(f"\n[HFT] HFT Activity Detected in New Deals ({len(hft_deals_today)} trades):")
        for desk, sym, side, qty in hft_deals_today[:8]:
            print(f"     - {desk:18} | {sym:12} | {side:4} | {qty:,} shares")
        if len(hft_deals_today) > 8:
            print(f"     ... and {len(hft_deals_today) - 8} more HFT deals.")
    else:
        print("[i] No tracked HFT institutional desks in new deals.")

    if len(new_deals_df) == 0:
        print(f"\n[OK] Azure Blob {blob_file_name} is already completely up to date. No upload required.")
        return

    # 5. Combine and Upload to Azure Blob
    if dry_run:
        print(f"\n[DRY RUN] Would have appended {len(new_deals_df)} rows to Azure Blob {blob_file_name}. (Skipping write)")
        return

    print(f"\n[4/4] Appending {len(new_deals_df)} new deals to Azure Blob ({blob_file_name})...")
    if existing_df is not None and not existing_df.empty:
        exist_std = existing_df[columns_to_keep]
        combined_df = pd.concat([exist_std, new_deals_df], ignore_index=True)
    else:
        combined_df = new_deals_df

    # Export to CSV in-memory
    csv_buffer = io.BytesIO()
    combined_df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
    csv_bytes = csv_buffer.getvalue()

    blob_client.upload_blob(csv_bytes, overwrite=True)
    print(f"[OK] SUCCESS: Azure Blob {blob_file_name} updated! Total deals now: {len(combined_df):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync daily NSE bulk deals to Azure Blob storage")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and test deduplication without writing to Azure Blob")
    args = parser.parse_args()

    print("=" * 65)
    print(f" NSE Daily Bulk Deals Ingestion Pipeline - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    sync_bulk_deals(dry_run=args.dry_run)
    print("=" * 65)
