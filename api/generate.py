from http.server import BaseHTTPRequestHandler
import io
import pandas as pd
import requests

BLOB_CSV_URL = "https://mywebsitecontainer.blob.core.windows.net/hft/hft.csv"

TRACKED_HFTS = {
    "JUMP TRADING": "Jump Trading",
    "QE SECURI": "QE Securities",
    "JUNOMON": "Junomoneta",
    "NK SECURI": "NK Securities",
    "HRTI PRIV": "HRTI",
    "GRAVITON": "Graviton",
    "SHARE INDIA": "Share India",
    "ELIXIR WE": "Elixir Wealth",
    "MICROCU": "Microcurves",
    "ALPHA GRE": "Alpha Alternatives",
    "BLITZQUA": "Blitzkraft",
    "IRAGE": "iRage Capital",
}


def match_hft(client_name):
    if not isinstance(client_name, str):
        return None
    c_upper = client_name.upper()
    for key, display_name in TRACKED_HFTS.items():
        if key in c_upper:
            return display_name
    return None


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        try:
            resp = requests.get(BLOB_CSV_URL, timeout=10)
            resp.raise_for_status()

            df = pd.read_csv(io.StringIO(resp.text), encoding="utf-8-sig")
            df.columns = (
                df.columns.str.strip().str.replace('"', "").str.replace("'", "")
            )

            date_col = next(c for c in df.columns if "date" in c.lower())
            symbol_col = next(c for c in df.columns if "symbol" in c.lower())
            client_col = next(c for c in df.columns if "client" in c.lower())

            df["Matched_HFT"] = df[client_col].apply(match_hft)
            hft_df = df.dropna(subset=["Matched_HFT"]).copy()
            hft_df["ParsedDate"] = pd.to_datetime(
                hft_df[date_col], errors="coerce"
            )
            hft_df = hft_df.dropna(subset=["ParsedDate"])

            grouped = (
                hft_df.groupby([symbol_col, "ParsedDate"])["Matched_HFT"]
                .unique()
                .reset_index()
            )

            deal_lines = []
            for _, row in grouped.iterrows():
                sym = str(row[symbol_col]).strip().upper()
                dt = row["ParsedDate"]
                firms = list(row["Matched_HFT"])
                count = len(firms)
                firms_str = "\\n".join(firms)
                deal_lines.append(
                    f'    f_addDeal(dealTimes, dealSyms, dealCount, dealFirms,'
                    f' {dt.day:02d}, {dt.month:02d}, {dt.year}, "{sym}",'
                    f' {count}, "{firms_str}")'
                )

            pine_output = (
                "\n".join(deal_lines)
                if deal_lines
                else "// No HFT deals detected in current CSV"
            )

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(pine_output.encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"Error processing CSV: {str(e)}".encode("utf-8"))