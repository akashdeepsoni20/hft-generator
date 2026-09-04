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

            # Safe chunk generator (max 1500 chars per literal to stay well below 4096)
            def make_indexed_vars(lst, prefix, max_chars=1500):
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
                return output_lines

            s_chunks = make_indexed_vars(sym_list, "s")
            t_chunks = make_indexed_vars(time_list, "t")
            c_chunks = make_indexed_vars(count_list, "c")
            f_chunks = make_indexed_vars(firm_list, "f")

            # Assemble the complete Pine Script code block programmatically
            script_lines = [
                '//@version=5',
                'indicator("HFT Universal Bulk Tracker", overlay=true, max_labels_count=500)',
                '',
                'f_split(str) =>',
                '    string[] res = array.new_string(0)',
                '    int start = 0',
                '    int len = str.length(str)',
                '    for i = 0 to len - 1',
                '        if str.substring(str, i, i + 1) == ","',
                '            array.push(res, str.substring(str, start, i))',
                '            start := i + 1',
                '    if start <= len',
                '        array.push(res, str.substring(str, start, len))',
                '    res',
                '',
                'var int[]    dealTimes = array.new_int(0)',
                'var int[]    dealCount = array.new_int(0)',
                'var string[] dealFirms = array.new_string(0)',
                '',
                'if barstate.isfirst',
                '    string curSym = str.upper(syminfo.ticker)',
                '    int colonPos = str.pos(curSym, ":")',
                '    if colonPos >= 0',
                '        curSym := str.substring(curSym, colonPos + 1)',
                ''
            ]

            script_lines.extend(s_chunks)
            script_lines.append('')
            script_lines.extend(t_chunks)
            script_lines.append('')
            script_lines.extend(c_chunks)
            script_lines.append('')
            script_lines.extend(f_chunks)

            script_lines.extend([
                '',
                '    string[] sArr = array.new_string(0)',
                '    string[] tArr = array.new_string(0)',
                '    string[] cArr = array.new_string(0)',
                '    string[] fArr = array.new_string(0)',
                ''
            ])

            for i in range(len(s_chunks)):
                script_lines.append(f'    array.concat(sArr, f_split(s{i+1}))')
            for i in range(len(t_chunks)):
                script_lines.append(f'    array.concat(tArr, f_split(t{i+1}))')
            for i in range(len(c_chunks)):
                script_lines.append(f'    array.concat(cArr, f_split(c{i+1}))')
            for i in range(len(f_chunks)):
                script_lines.append(f'    array.concat(fArr, f_split(f{i+1}))')

            script_lines.extend([
                '',
                '    for i = 0 to array.size(sArr) - 1',
                '        if array.get(sArr, i) == curSym',
                '            array.push(dealTimes, int(str.tonumber(array.get(tArr, i))))',
                '            array.push(dealCount, int(str.tonumber(array.get(cArr, i))))',
                '            array.push(dealFirms, array.get(fArr, i))',
                '',
                'int curBarDayStart = timestamp("UTC", year(time, syminfo.timezone), month(time, syminfo.timezone), dayofmonth(time, syminfo.timezone), 0, 0, 0)',
                'int dealIdx = array.size(dealTimes) > 0 ? array.binary_search(dealTimes, curBarDayStart) : -1',
                'color hftBgColor = na',
                '',
                'if dealIdx >= 0',
                '    bool shouldPlotLabel = timeframe.isdaily or (timeframe.isintraday and ta.change(time("D")))',
                '    int    tCount = array.get(dealCount, dealIdx)',
                '    string fList  = array.get(dealFirms, dealIdx)',
                '    string lblSz = tCount >= 6 ? size.large : tCount >= 3 ? size.normal : size.small',
                '    color  cCol  = tCount >= 6 ? #00E676 : #2E7D32',
                '    hftBgColor := color.new(cCol, 85)',
                '',
                '    if shouldPlotLabel',
                '        string tip = "HFT BULK DEAL\\nStock: " + syminfo.ticker + "\\nDate: " + str.format_time(time, "dd-MM-yyyy", syminfo.timezone) + "\\nTraders: " + str.tostring(tCount) + " of 12\\n----\\n" + fList',
                '        label.new(x=bar_index, y=low, text="▲" + str.tostring(tCount), style=label.style_label_up, color=cCol, textcolor=color.white, size=lblSz, tooltip=tip)',
                '',
                'bgcolor(hftBgColor, title="HFT Cluster Day")'
            ])

            output = "\n".join(script_lines)

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