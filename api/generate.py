from http.server import BaseHTTPRequestHandler
import io
import json
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


def fetch_and_process_hft_data():
    resp = requests.get(BLOB_CSV_URL, timeout=12)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text), encoding="utf-8-sig")
    df.columns = df.columns.str.strip().str.replace('"', "").str.replace("'", "")

    date_col = next(c for c in df.columns if "date" in c.lower())
    symbol_col = next(c for c in df.columns if "symbol" in c.lower())
    client_col = next(c for c in df.columns if "client" in c.lower())

    df["CleanSym"] = df[symbol_col].astype(str).str.strip().str.upper()
    df["Matched_HFT"] = df[client_col].apply(match_hft)

    hft_df = df.dropna(subset=["Matched_HFT"]).copy()
    
    # Try standard format first, fallback to mixed
    try:
        hft_df["ParsedDate"] = pd.to_datetime(hft_df[date_col], format="%d-%b-%y", errors="coerce")
    except Exception:
        hft_df["ParsedDate"] = pd.to_datetime(hft_df[date_col], errors="coerce")

    hft_df = hft_df.dropna(subset=["ParsedDate"])

    grouped = (
        hft_df.groupby(["CleanSym", "ParsedDate"])["Matched_HFT"]
        .agg(lambda x: sorted(list(set(x))))
        .reset_index()
    )
    grouped = grouped.sort_values(by="ParsedDate")
    return grouped


def generate_pinescript(grouped, selected_syms=None):
    if selected_syms and "ALL" not in [s.upper() for s in selected_syms]:
        selected_set = set(s.upper() for s in selected_syms)
        filtered_group = grouped[grouped["CleanSym"].isin(selected_set)]
        title_tag = f"({len(selected_set)} Stocks)"
    else:
        filtered_group = grouped
        title_tag = "(All Tracked Stocks)"

    sym_list, time_list, count_list, firm_list = [], [], [], []
    for _, row in filtered_group.iterrows():
        dt = row["ParsedDate"]
        t_val = int(pd.Timestamp(dt).timestamp() * 1000)
        sym_list.append(row["CleanSym"])
        time_list.append(str(t_val))
        count_list.append(str(len(row["Matched_HFT"])))
        firm_list.append("\\n".join(row["Matched_HFT"]))

    if not sym_list:
        return f"""//@version=5
indicator("HFT Custom Tracker {title_tag}", overlay=true)
// No bulk deals found for the selected symbols in tracked desks.
"""

    def make_indexed_vars(lst, prefix, max_chars=1200):
        chunks = []
        current_batch = []
        current_len = 0
        for item in lst:
            item_str = str(item)
            item_len = len(item_str) + 1
            if current_len + item_len > max_chars and current_batch:
                chunks.append(",".join(current_batch))
                current_batch = [item_str]
                current_len = item_len
            else:
                current_batch.append(item_str)
                current_len += item_len
        if current_batch:
            chunks.append(",".join(current_batch))

        output_lines = []
        for idx, chunk in enumerate(chunks):
            output_lines.append(f'    {prefix}{idx+1} = "{chunk}"')
        return output_lines, len(chunks)

    s_chunks, s_cnt = make_indexed_vars(sym_list, "s")
    t_chunks, t_cnt = make_indexed_vars(time_list, "t")
    c_chunks, c_cnt = make_indexed_vars(count_list, "c")
    f_chunks, f_cnt = make_indexed_vars(firm_list, "f")

    script_lines = [
        "//@version=5",
        f'indicator("HFT Custom Tracker {title_tag}", overlay=true, max_labels_count=500)',
        "",
        "f_split(str) =>",
        "    string[] res = array.new_string(0)",
        "    int start = 0",
        "    int len = str.length(str)",
        "    for i = 0 to len - 1",
        '        if str.substring(str, i, i + 1) == ","',
        "            array.push(res, str.substring(str, start, i))",
        "            start := i + 1",
        "    if start <= len",
        "        array.push(res, str.substring(str, start, len))",
        "    res",
        "",
        "var int[]    dealTimes = array.new_int(0)",
        "var int[]    dealCount = array.new_int(0)",
        "var string[] dealFirms = array.new_string(0)",
        "",
        "if barstate.isfirst",
        "    string curSym = str.upper(syminfo.ticker)",
        '    int colonPos = str.pos(curSym, ":")',
        "    if colonPos >= 0",
        "        curSym := str.substring(curSym, colonPos + 1)",
        "",
    ]

    script_lines.extend(s_chunks)
    script_lines.append("")
    script_lines.extend(t_chunks)
    script_lines.append("")
    script_lines.extend(c_chunks)
    script_lines.append("")
    script_lines.extend(f_chunks)

    script_lines.extend([
        "",
        "    string[] sArr = array.new_string(0)",
        "    string[] tArr = array.new_string(0)",
        "    string[] cArr = array.new_string(0)",
        "    string[] fArr = array.new_string(0)",
        "",
    ])

    for i in range(s_cnt):
        script_lines.append(f"    array.concat(sArr, f_split(s{i+1}))")
    for i in range(t_cnt):
        script_lines.append(f"    array.concat(tArr, f_split(t{i+1}))")
    for i in range(c_cnt):
        script_lines.append(f"    array.concat(cArr, f_split(c{i+1}))")
    for i in range(f_cnt):
        script_lines.append(f"    array.concat(fArr, f_split(f{i+1}))")

    script_lines.extend([
        "",
        "    for i = 0 to array.size(sArr) - 1",
        "        if array.get(sArr, i) == curSym",
        "            array.push(dealTimes, int(str.tonumber(array.get(tArr, i))))",
        "            array.push(dealCount, int(str.tonumber(array.get(cArr, i))))",
        "            array.push(dealFirms, array.get(fArr, i))",
        "",
        'int curBarDayStart = timestamp("UTC", year(time, syminfo.timezone), month(time, syminfo.timezone), dayofmonth(time, syminfo.timezone), 0, 0, 0)',
        "int dealIdx = array.size(dealTimes) > 0 ? array.binary_search(dealTimes, curBarDayStart) : -1",
        "color hftBgColor = na",
        "",
        "if dealIdx >= 0",
        '    bool shouldPlotLabel = timeframe.isdaily or (timeframe.isintraday and ta.change(time("D")))',
        "    int    tCount = array.get(dealCount, dealIdx)",
        "    string fList  = array.get(dealFirms, dealIdx)",
        "    string lblSz = tCount >= 6 ? size.large : tCount >= 3 ? size.normal : size.small",
        "    color  cCol  = tCount >= 6 ? #00E676 : #2E7D32",
        "    hftBgColor := color.new(cCol, 85)",
        "",
        "    if shouldPlotLabel",
        '        string tip = "HFT BULK DEAL\\nStock: " + syminfo.ticker + "\\nDate: " + str.format_time(time, "dd-MM-yyyy", syminfo.timezone) + "\\nTraders: " + str.tostring(tCount) + " of 12\\n----\\n" + fList',
        '        label.new(x=bar_index, y=low, text="▲" + str.tostring(tCount), style=label.style_label_up, color=cCol, textcolor=color.white, size=lblSz, tooltip=tip)',
        "",
        'bgcolor(hftBgColor, title="HFT Cluster Day")',
    ])

    return "\n".join(script_lines)


class handler(BaseHTTPRequestHandler):
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

            grouped = fetch_and_process_hft_data()
            all_symbols = sorted(grouped["CleanSym"].unique().tolist())

            # Return list of symbols for the frontend multi-select dropdown
            if query_params.get("action") == ["symbols"] or "symbols" not in query_params and "symbol" not in query_params and "application/json" in accept_header:
                response_data = {
                    "status": "success",
                    "count": len(all_symbols),
                    "symbols": all_symbols,
                }
                body = json.dumps(response_data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return

            # Pine Script generation for selected symbols
            selected_syms = None
            if "symbols" in query_params:
                raw = query_params["symbols"][0]
                selected_syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
            elif "symbol" in query_params:
                selected_syms = [s.strip().upper() for s in query_params["symbol"][0].split(",") if s.strip()]

            script_text = generate_pinescript(grouped, selected_syms)

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