from http.server import BaseHTTPRequestHandler
import io
import urllib.parse
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
            parsed_path = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_path.query)
            
            # Fetch and process CSV data
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

            all_symbols = sorted(grouped["CleanSym"].unique().tolist())

            # If user requested script generation for specific stocks via query params
            if "symbols" in query_params:
                selected_syms = [s.strip().upper() for s in query_params["symbols"][0].split(",") if s.strip()]
                filtered_group = grouped[grouped["CleanSym"].isin(selected_syms)]

                sym_list, time_list, count_list, firm_list = [], [], [], []
                for _, row in filtered_group.iterrows():
                    dt = row["ParsedDate"]
                    t_val = int(pd.Timestamp(dt).timestamp() * 1000)
                    sym_list.append(row["CleanSym"])
                    time_list.append(str(t_val))
                    count_list.append(str(len(row["Matched_HFT"])))
                    firm_list.append("\\n".join(row["Matched_HFT"]))

                # Generate lightweight compact script for selected stocks only
                script_content = f"""//@version=5
indicator("HFT Custom Tracker ({len(selected_syms)} Stocks)", overlay=true, max_labels_count=500)

f_split(str) =>
    string[] res = array.new_string(0)
    int start = 0
    int len = str.length(str)
    for i = 0 to len - 1
        if str.substring(str, i, i + 1) == ","
            array.push(res, str.substring(str, start, i))
            start := i + 1
    if start <= len
        array.push(res, str.substring(str, start, len))
    res

var int[]    dealTimes = array.new_int(0)
var int[]    dealCount = array.new_int(0)
var string[] dealFirms = array.new_string(0)

if barstate.isfirst
    string curSym = str.upper(syminfo.ticker)
    int colonPos = str.pos(curSym, ":")
    if colonPos >= 0
        curSym := str.substring(curSym, colonPos + 1)

    sData = "{",".join(sym_list)}"
    tData = "{",".join(time_list)}"
    cData = "{",".join(count_list)}"
    fData = "{",".join(firm_list)}"

    string[] sArr = f_split(sData)
    string[] tArr = f_split(tData)
    string[] cArr = f_split(cData)
    string[] fArr = f_split(fData)

    for i = 0 to array.size(sArr) - 1
        if array.get(sArr, i) == curSym
            array.push(dealTimes, int(str.tonumber(array.get(tArr, i))))
            array.push(dealCount, int(str.tonumber(array.get(cArr, i))))
            array.push(dealFirms, array.get(fArr, i))

int curBarDayStart = timestamp("UTC", year(time, syminfo.timezone), month(time, syminfo.timezone), dayofmonth(time, syminfo.timezone), 0, 0, 0)
int dealIdx = array.size(dealTimes) > 0 ? array.binary_search(dealTimes, curBarDayStart) : -1
color hftBgColor = na

if dealIdx >= 0
    bool shouldPlotLabel = timeframe.isdaily or (timeframe.isintraday and ta.change(time("D")))
    int    tCount = array.get(dealCount, dealIdx)
    string fList  = array.get(dealFirms, dealIdx)
    string lblSz = tCount >= 6 ? size.large : tCount >= 3 ? size.normal : size.small
    color  cCol  = tCount >= 6 ? #00E676 : #2E7D32
    hftBgColor := color.new(cCol, 85)

    if shouldPlotLabel
        string tip = "HFT BULK DEAL\\nStock: " + syminfo.ticker + "\\nTraders: " + str.tostring(tCount) + " of 12\\n----\\n" + fList
        label.new(x=bar_index, y=low, text="▲" + str.tostring(tCount), style=label.style_label_up, color=cCol, textcolor=color.white, size=lblSz, tooltip=tip)

bgcolor(hftBgColor, title="HFT Cluster Day")
"""
                # Ensure the headers are sent with explicit text/html
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(html_content.encode("utf-8"))
                return

            # Otherwise, serve a search dashboard HTML page
            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>HFT Tracker Script Generator</title>
    <link href="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css" rel="stylesheet" />
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; }}
        .card {{ max-width: 600px; margin: auto; background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); }}
        h2 {{ margin-top: 0; color: #38bdf8; }}
        p {{ color: #94a3b8; font-size: 14px; }}
        .select2-container--default .select2-selection--multiple {{ background-color: #0f172a; border: 1px solid #334155; color: white; border-radius: 6px; min-height: 45px; }}
        .select2-container--default .select2-selection--multiple .select2-selection__choice {{ background-color: #3b82f6; border: none; color: white; border-radius: 4px; }}
        .select2-dropdown {{ background-color: #1e293b; border: 1px solid #334155; color: white; }}
        .select2-search__field {{ background-color: #0f172a !important; color: white !important; }}
        .select2-results__option[aria-selected=true] {{ background-color: #334155 !important; }}
        button {{ background: #22c55e; color: white; border: none; padding: 12px 20px; font-size: 16px; font-weight: bold; border-radius: 6px; cursor: pointer; width: 100%; margin-top: 20px; transition: background 0.2s; }}
        button:hover {{ background: #16a34a; }}
        pre {{ background: #0f172a; padding: 15px; border-radius: 6px; overflow-x: auto; max-height: 250px; font-size: 12px; border: 1px solid #334155; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>HFT Stock Script Generator</h2>
        <p>Select or search for the stocks you want to track. This generates a lightweight script containing only your selection, avoiding all length limits.</p>
        
        <label for="stockSelect" style="font-weight:600; display:block; margin-bottom:8px;">Choose Stocks:</label>
        <select id="stockSelect" class="stocks-dropdown" multiple style="width: 100%;">
            {"".join([f'<option value="{sym}">{sym}</option>' for sym in all_symbols])}
        </select>

        <button onclick="generateScript()">Generate & Copy Script</button>
        
        <pre id="outputPreview">Select stocks above and click generate...</pre>
    </div>

    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
    <script>
        $(document).ready(function() {{
            $('#stockSelect').select2({{
                placeholder: "Search and select stocks...",
                allowClear: true
            }});
        }});

        async function generateScript() {{
            const selected = $('#stockSelect').val();
            if (!selected || selected.length === 0) {{
                alert("Please select at least one stock!");
                return;
            }}

            const response = await fetch('/api/generate?symbols=' + encodeURIComponent(selected.join(',')));
            const scriptText = await response.text();

            navigator.clipboard.writeText(scriptText);
            document.getElementById('outputPreview').textContent = scriptText;
            alert("Success! Custom script for " + selected.length + " stock(s) copied to clipboard.");
        }}
    </script>
</body>
</html>
"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"Error: {str(e)}".encode("utf-8"))