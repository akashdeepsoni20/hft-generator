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
            df.columns = df.columns.str.strip().str.replace('"', '').str.replace("'", '')

            date_col = next(c for c in df.columns if "date" in c.lower())
            symbol_col = next(c for c in df.columns if "symbol" in c.lower())
            client_col = next(c for c in df.columns if "client" in c.lower())

            df["CleanSym"] = df[symbol_col].astype(str).str.strip().str.upper()
            df["Matched_HFT"] = df[client_col].apply(match_hft)

            hft_df = df.dropna(subset=["Matched_HFT"]).copy()
            hft_df["ParsedDate"] = pd.to_datetime(hft_df[date_col], errors="coerce")
            hft_df = hft_df.dropna(subset=["ParsedDate"]).sort_values(by="ParsedDate")

            grouped = hft_df.groupby(["CleanSym", "ParsedDate"])["Matched_HFT"].unique().reset_index()

            symbols = sorted(grouped["CleanSym"].unique())
            chunk_size = 35  # Keep each switch block small to prevent compiler limits
            symbol_chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

            lines = []
            
            # 1. Generate chunk functions
            for idx, chunk in enumerate(symbol_chunks):
                func_name = f"load_batch_{idx+1}"
                lines.append(f"f_{func_name}(curSym, dtArr, cntArr, firmsArr) =>")
                lines.append(f"    switch curSym")
                for sym in chunk:
                    lines.append(f'        "{sym}" =>')
                    group = grouped[grouped["CleanSym"] == sym]
                    for _, row in group.iterrows():
                        dt = row["ParsedDate"]
                        firms = list(row["Matched_HFT"])
                        count = len(firms)
                        firms_str = "\\n".join(firms)
                        lines.append(f'            f_add(dtArr, cntArr, firmsArr, {dt.year}, {dt.month}, {dt.day}, {count}, "{firms_str}")')
                lines.append("")

            # 2. Generate the main execution block for barstate.isfirst
            lines.append("if barstate.isfirst")
            lines.append("    string curSym = str.upper(syminfo.ticker)")
            lines.append("    int colonPos = str.pos(curSym, \":\")")
            lines.append("    if colonPos >= 0")
            lines.append("        curSym := str.substring(curSym, colonPos + 1)")
            for idx in range(len(symbol_chunks)):
                lines.append(f"    f_load_batch_{idx+1}(curSym, dealTimes, dealCount, dealFirms)")

            output = "\n".join(lines)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(output.encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"Error: {str(e)}".encode("utf-8"))