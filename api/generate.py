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
            hft_df = hft_df.dropna(subset=["ParsedDate"])

            grouped = hft_df.groupby(["CleanSym", "ParsedDate"])["Matched_HFT"].agg(lambda x: sorted(list(set(x)))).reset_index()
            grouped = grouped.sort_values(by="ParsedDate")

            sym_list = []
            time_list = []
            count_list = []
            firm_list = []

            for _, row in grouped.iterrows():
                dt = row["ParsedDate"]
                t_val = int(pd.Timestamp(dt).timestamp() * 1000)
                sym_list.append(row["CleanSym"])
                time_list.append(str(t_val))
                count_list.append(str(len(row["Matched_HFT"])))
                firm_list.append("\\n".join(row["Matched_HFT"]))

            # Strict character-budget chunking (max 500 chars per literal to stay safely below 4096)
            def make_safe_chunked_str(lst, max_chars=500):
                chunks = []
                current_batch = []
                current_len = 0
                for item in lst:
                    item_str = str(item)
                    item_len = len(item_str) + 1  # +1 for comma separator
                    if current_len + item_len > max_chars and current_batch:
                        chunks.append(f'"{",".join(current_batch)}"')
                        current_batch = [item_str]
                        current_len = item_len
                    else:
                        current_batch.append(item_str)
                        current_len += item_len
                if current_batch:
                    chunks.append(f'"{",".join(current_batch)}"')
                return ' + \n         '.join(chunks)

            lines = [
                f'sData = {make_safe_chunked_str(sym_list)}',
                f'tData = {make_safe_chunked_str(time_list)}',
                f'cData = {make_safe_chunked_str(count_list)}',
                f'fData = {make_safe_chunked_str(firm_list)}'
            ]

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