import http.server
import io
import socketserver
import webbrowser
import pandas as pd
import requests

PORT = 5000
BLOB_CSV_URL = "https://mywebsitecontainer.blob.core.windows.net/hft/hft.csv"

# 12 Tracked HFT / Algo Desks
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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Local HFT Sync</title>
  <style>
    body {
      background: #0d1117;
      color: #c9d1d9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 30px;
    }
    .container { max-width: 950px; margin: 0 auto; }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    h1 { font-size: 20px; color: #58a6ff; margin: 0; }
    .btn-group { display: flex; gap: 10px; }
    button {
      background: #238636;
      color: #ffffff;
      font-weight: 600;
      border: 1px solid rgba(240, 246, 252, 0.1);
      padding: 8px 16px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
    }
    button:hover { background: #2ea043; }
    button.refresh { background: #21262d; color: #c9d1d9; border-color: #30363d; }
    button.refresh:hover { background: #30363d; }
    textarea {
      width: 100%;
      height: 620px;
      background: #161b22;
      color: #7ee787;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 16px;
      font-family: "Fira Code", monospace;
      font-size: 13px;
      line-height: 1.5;
      box-sizing: border-box;
      resize: vertical;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>HFT Bulk Deal &rarr; Pine Script Sync</h1>
      <div class="btn-group">
        <button class="refresh" onclick="fetchData()">Reload from Blob</button>
        <button id="copyBtn" onclick="copyCode()">Copy Code</button>
      </div>
    </div>
    <textarea id="output" readonly placeholder="Parsing Azure Blob CSV..."></textarea>
  </div>

  <script>
    async function fetchData() {
      const area = document.getElementById("output");
      area.value = "Fetching and parsing latest data from Azure Blob...";
      try {
        const res = await fetch("/api/generate");
        const data = await res.text();
        area.value = data;
      } catch (err) {
        area.value = "Error fetching data: " + err;
      }
    }

    function copyCode() {
      const area = document.getElementById("output");
      area.select();
      navigator.clipboard.writeText(area.value);
      const btn = document.getElementById("copyBtn");
      btn.innerText = "Copied!";
      btn.style.background = "#1f6feb";
      setTimeout(() => {
        btn.innerText = "Copy Code";
        btn.style.background = "#238636";
      }, 2000);
    }

    fetchData();
  </script>
</body>
</html>
"""


def match_hft(client_name):
    if not isinstance(client_name, str):
        return None
    c_upper = client_name.upper()
    for key, display_name in TRACKED_HFTS.items():
        if key in c_upper:
            return display_name
    return None


def generate_pinescript_block():
    resp = requests.get(BLOB_CSV_URL, timeout=10)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text), encoding="utf-8-sig")
    df.columns = df.columns.str.strip().str.replace('"', "").str.replace("'", "")

    date_col = next(c for c in df.columns if "date" in c.lower())
    symbol_col = next(c for c in df.columns if "symbol" in c.lower())
    client_col = next(c for c in df.columns if "client" in c.lower())

    df["Matched_HFT"] = df[client_col].apply(match_hft)
    hft_df = df.dropna(subset=["Matched_HFT"]).copy()
    hft_df["ParsedDate"] = pd.to_datetime(hft_df[date_col], errors="coerce")
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
            f' {dt.day:02d}, {dt.month:02d}, {dt.year}, "{sym}", {count},'
            f' "{firms_str}")'
        )

    return (
        "\n".join(deal_lines)
        if deal_lines
        else "// No tracked HFT trades found in the current CSV"
    )


class LocalRequestHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/generate":
            try:
                content = generate_pinescript_block()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Mute server access logs


if __name__ == "__main__":
    print(f"Local server started at http://localhost:{PORT}")
    webbrowser.open(f"http://localhost:{PORT}")
    with socketserver.TCPServer(("", PORT), LocalRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")