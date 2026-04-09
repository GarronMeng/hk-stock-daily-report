import yfinance as yf
import akshare as ak
import smtplib
import json
import os
import socket
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
if not BASE_URL.endswith("/v1"):
    BASE_URL = BASE_URL.rstrip("/") + "/v1"
MODEL = "claude-opus-4-6"

def llm_call(prompt, max_tokens=800):
    if not API_KEY: return ""
    try:
        r = requests.post(f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"LLM error: {e}"); return ""

def fetch_market_indices():
    tickers = {"^HSI": "HSI", "^HSCE": "HSCEI", "000001.SS": "SSE", "399001.SZ": "SZSE", "399006.SZ": "ChiNext"}
    results = {}
    for t, name in tickers.items():
        try:
            h = yf.Ticker(t).history(period="10d")
            closes = h["Close"].dropna()
            if len(closes) >= 2:
                c, p = float(closes.iloc[-1]), float(closes.iloc[-2])
                results[name] = {"close": round(c, 2), "change": round(c-p, 2), "pct": round((c-p)/p*100, 2)}
            elif len(closes) == 1:
                results[name] = {"close": round(float(closes.iloc[-1]), 2), "change": 0, "pct": 0}
        except Exception as e: print(f"Index error {name}: {e}")
    return results

def fetch_stocks(watchlist):
    results = []
    for item in watchlist:
        t, name = item["ticker"], item["name"]
        try:
            h = yf.Ticker(t).history(period="10d")
            closes = h["Close"].dropna()
            vols = h["Volume"].dropna()
            if len(closes) >= 2:
                c, p = float(closes.iloc[-1]), float(closes.iloc[-2])
                vol = float(vols.iloc[-1]) if len(vols) else 0
            elif len(closes) == 1:
                c, p, vol = float(closes.iloc[-1]), float(closes.iloc[-1]), float(vols.iloc[-1]) if len(vols) else 0
            else: continue
            pct = (c-p)/p*100 if p else 0
            results.append({"name": name, "ticker": t, "close": round(c, 2), "change": round(c-p, 2), "pct": round(pct, 2), "volume": vol})
        except Exception as e: print(f"Stock error {name}: {e}")
    return results

def fetch_market_movers():
    data = {}
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(60)
        df = ak.stock_zh_a_spot_em()
        socket.setdefaulttimeout(old_to)
        if df is not None and not df.empty:
            cols = df.columns.tolist()
            name_col, code_col = cols[1] if len(cols)>1 else cols[0], cols[0]
            pct_col = vol_col = None
            for c in cols:
                if "涨跌幅" in str(c): pct_col = c
                if "成交额" in str(c): vol_col = c
            if pct_col:
                valid = df[(df[pct_col].abs() <= 20) & (df[pct_col].notna())]
                top = valid.nlargest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_gainers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]),2)} for r in top]
                bot = valid.nsmallest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_losers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]),2)} for r in bot]
            if vol_col:
                tv = df.nlargest(10, vol_col)[[code_col, name_col, vol_col]].values.tolist()
                data["top_volume"] = [{"code": str(r[0]), "name": str(r[1]), "vol_cny": round(float(r[2])/1e8,1)} for r in tv]
    except Exception as e: print(f"A-share movers error: {e}")
    return data

def fetch_northbound_flow():
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        socket.setdefaulttimeout(old_to)
        if df is not None and not df.empty:
            cols = df.columns.tolist()
            print(f"  NB hist columns: {cols}, rows: {len(df)}")
            # Data is sorted ascending by date, latest is last row
            latest = df.iloc[-1]
            trade_date = str(latest.get("日期", "N/A"))
            # 当日成交净买额 is in 亿 CNY already, convert to bn (divide by 10)
            net_val = float(latest.get("当日成交净买额", 0) or 0) / 10
            detail = {}
            for sym_name, label in [("沪股通", "沪股通"), ("深股通", "深股通")]:
                try:
                    sub = ak.stock_hsgt_hist_em(symbol=sym_name)
                    if sub is not None and not sub.empty:
                        sub_latest = sub.iloc[-1]
                        sub_net = float(sub_latest.get("当日成交净买额", 0) or 0) / 10
                        detail[label] = round(sub_net, 2)
                except: pass
            print(f"  NB latest: date={trade_date}, total={net_val:.2f}bn, detail={detail}")
            return {"detail": detail, "northbound_total_bn": round(net_val, 2), "date": trade_date}
    except Exception as e: print(f"Northbound flow error: {e}")
    return {}

def fetch_real_news():
    news_items = []
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(20)
        df = ak.stock_news_main_cx()
        socket.setdefaulttimeout(old_to)
        if df is not None and not df.empty:
            for _, row in df.head(15).iterrows():
                summary = str(row.get("summary", ""))
                tag = str(row.get("tag", ""))
                if summary:
                    news_items.append({"source": "Caixin", "tag": tag, "text": summary})
    except Exception as e: print(f"Caixin news error: {e}")
    for sym in ["600519", "300750", "002594", "601318"]:
        try:
            old_to = socket.getdefaulttimeout()
            socket.setdefaulttimeout(15)
            df = ak.stock_news_em(symbol=sym)
            socket.setdefaulttimeout(old_to)
            if df is not None and not df.empty:
                title = str(df.iloc[0].get("新闻标题", ""))
                if title:
                    news_items.append({"source": "EastMoney", "symbol": sym, "text": title})
        except Exception as e: print(f"Stock news error {sym}: {e}")
    return news_items[:20]

def pick_spotlight(movers, core_stocks):
    if not API_KEY: return []
    hk_movers = [{"code": s["ticker"], "name": s["name"], "pct": s["pct"]} for s in core_stocks if ".HK" in s["ticker"] and abs(s["pct"]) >= 1.5]
    combined = {}
    if movers: combined["a_share_movers"] = movers
    if hk_movers: combined["hk_notable"] = hk_movers
    if not combined: return []
    mt = json.dumps(combined, ensure_ascii=False, default=str)
    prompt = f"""Based on today's market data, pick 5-8 notable stocks from BOTH HK and A-share markets. For each provide code, English name, one-line reason.

Data:
{mt}

Reply ONLY as JSON array like: [{{"code":"600519","name":"Kweichow Moutai","reason":"..."}},{{"code":"0700.HK","name":"Tencent","reason":"..."}}]
For HK stocks use .HK suffix. For A-shares use 6-digit code only."""
    raw = llm_call(prompt, max_tokens=600)
    if not raw: return []
    try:
        return json.loads(raw[raw.index("["):raw.rindex("]")+1])[:8]
    except: return []

def fetch_spotlight_prices(picks):
    results = []
    for p in picks:
        code, name, reason = p.get("code",""), p.get("name",""), p.get("reason","")
        if code.endswith(".HK"): ticker = code
        elif code.startswith("6") or code.startswith("688"): ticker = code+".SS"
        else: ticker = code+".SZ"
        try:
            h = yf.Ticker(ticker).history(period="10d")
            closes = h["Close"].dropna()
            if len(closes)>=2:
                c,p2 = float(closes.iloc[-1]), float(closes.iloc[-2])
                results.append({"name":name,"ticker":ticker,"close":round(c,2),"pct":round((c-p2)/p2*100,2),"reason":reason})
            elif len(closes)==1:
                results.append({"name":name,"ticker":ticker,"close":round(float(closes.iloc[-1]),2),"pct":0,"reason":reason})
        except:
            results.append({"name":name,"ticker":ticker,"close":0,"pct":0,"reason":reason})
    return results

def generate_corps(real_news, core_stocks, northbound):
    if not API_KEY or not real_news: return ""
    news_text = "\n".join([f"[{n.get('source','')}] {n['text']}" for n in real_news[:15]])
    stocks_ctx = ", ".join([f"{s['name']}({s['ticker']}) {s['pct']}%" for s in core_stocks[:10]])
    nb_ctx = f"Northbound net flow: {northbound.get('northbound_total_bn', 'N/A')} bn CNY" if northbound else ""
    prompt = f"""You are a CLSA equity sales trader writing the CORPS section. Translate and curate these REAL Chinese market news into 6-10 concise English one-line bullets.

Format each line as: COMPANY_NAME (CODE): One sentence about the news.
If news is about macro/policy, format as: MACRO/POLICY: One sentence.

Real news:
{news_text}

Context - key stocks: {stocks_ctx}
{nb_ctx}

Rules:
- Translate accurately from the Chinese news, do NOT fabricate
- Be specific with numbers and facts from the source
- If a news item is vague, skip it
- Output ONLY the bullet lines, one per line, no numbering, no markdown."""
    return llm_call(prompt, max_tokens=800)

def generate_ai_brief(indices, core_stocks, movers, northbound, real_news):
    idx = ", ".join([f"{k} {v['pct']}%" for k,v in indices.items()])
    stocks = ", ".join([f"{s['name']} {s['pct']}%" for s in core_stocks[:10]])
    nb_text = f"Northbound net flow: {northbound.get('northbound_total_bn', 'N/A')} bn CNY. Detail: {json.dumps(northbound.get('detail',{}), ensure_ascii=False)}" if northbound else "Northbound data unavailable."
    news_text = " | ".join([n["text"][:60] for n in real_news[:8]]) if real_news else "No news available."
    prompt = f"""You are a CLSA strategist. Write a 3-4 sentence market overview for today's HK/China markets.

Indices: {idx}
Key stocks: {stocks}
Northbound flow: {nb_text}
Key headlines: {news_text}

Be direct, specific, professional. Reference northbound flows and key catalysts. Plain text only."""
    return llm_call(prompt, max_tokens=400)

def build_html(today, indices, core_stocks, spotlight, corps_text, brief_text, northbound):
    tpl = open(Path(__file__).parent/"templates"/"email.html","r",encoding="utf-8").read()
    def cc(pct): return "#e8a838" if pct>=0 else "#5cb85c"
    def ss(pct): return "+" if pct>=0 else ""
    fl_parts = []
    for name, d in indices.items():
        fl_parts.append(f"{name} {ss(d['pct'])}{d['change']} pts / {ss(d['pct'])}{d['pct']}%")
    futures_line = " | ".join(fl_parts[:3])
    idx_rows = ""
    items = list(indices.items())
    for i in range(0, len(items), 3):
        row = "<tr>"
        for j in range(3):
            if i+j < len(items):
                n, d = items[i+j]
                c = cc(d["pct"])
                row += f'<td style="padding:2px 12px 2px 0;color:#e8a838">{n}</td><td style="padding:2px 12px 2px 0;color:{c}">{d["close"]:,.0f}</td><td style="padding:2px 16px 2px 0;color:{c}">{ss(d["pct"])}{d["pct"]}%</td>'
        row += "</tr>"
        idx_rows += row
    sg = ""
    for i in range(0, len(core_stocks), 3):
        row = "<tr>"
        for j in range(3):
            if i+j < len(core_stocks):
                s = core_stocks[i+j]
                c = cc(s["pct"])
                short = s["name"][:10]
                row += f'<td style="padding:2px 6px 2px 0;color:#e8a838">{short}</td><td style="padding:2px 12px 2px 0;color:{c}">{ss(s["pct"])}{s["pct"]}%</td>'
            else:
                row += '<td></td><td></td>'
        row += "</tr>"
        sg += row
    flow_html = ""
    if northbound and northbound.get("detail"):
        nb_total = northbound.get("northbound_total_bn", 0)
        c = "#e8a838" if nb_total >= 0 else "#5cb85c"
        sign = "+" if nb_total >= 0 else ""
        flow_html = f'<div style="margin:16px 0"><div style="color:#888;font-size:11px;margin-bottom:4px">NORTHBOUND FLOW</div>'
        flow_html += f'<div style="color:{c};font-size:14px;font-weight:bold">{sign}{nb_total:.2f} bn CNY</div>'
        detail_parts = []
        for board, val in northbound["detail"].items():
            bc = "#e8a838" if val >= 0 else "#5cb85c"
            bs = "+" if val >= 0 else ""
            detail_parts.append(f'<span style="color:{bc}">{board} {bs}{val:.2f}bn</span>')
        if detail_parts:
            flow_html += f'<div style="color:#888;font-size:11px;margin-top:4px">{" | ".join(detail_parts)}</div>'
        flow_html += '</div>'
    corps_html = ""
    if corps_text:
        for line in corps_text.strip().split("\n"):
            line = line.strip()
            if line:
                corps_html += f'<div style="margin-bottom:6px">• {line}</div>'
    spot_html = ""
    for s in spotlight:
        c = cc(s["pct"])
        spot_html += f'<div style="margin-bottom:6px"><span style="color:#e8a838">{s["name"]} ({s["ticker"]})</span> <span style="color:{c}">{ss(s["pct"])}{s["pct"]}%</span>: {s["reason"]}</div>'
    if not spot_html:
        spot_html = '<div style="color:#666">No spotlight picks today.</div>'
    brief_html = brief_text.replace("\n","<br>") if brief_text else "AI summary unavailable."
    dt = datetime.now().strftime("%m/%d/%y %H:%M:%S UTC+08:00")
    html = tpl.replace("{{DATETIME}}",dt).replace("{{FUTURES_LINE}}",futures_line)
    html = html.replace("{{INDEX_TABLE}}",idx_rows).replace("{{STOCK_GRID}}",sg)
    html = html.replace("{{FLOW_SECTION}}",flow_html).replace("{{CORPS_ITEMS}}",corps_html)
    html = html.replace("{{SPOTLIGHT_ITEMS}}",spot_html).replace("{{AI_BRIEF}}",brief_html)
    return html

def send_email(subject, html_body):
    addr = os.environ.get("GMAIL_ADDRESS","")
    pwd = os.environ.get("GMAIL_APP_PASSWORD","")
    if not addr or not pwd: print("Gmail creds not set"); return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
            s.login(addr, pwd); s.sendmail(addr, addr, msg.as_string())
        print("Email sent successfully")
    except Exception as e: print(f"Email error: {e}")

def main():
    wl = json.load(open(Path(__file__).parent/"watchlist.json","r",encoding="utf-8"))
    print("Fetching market indices...")
    indices = fetch_market_indices()
    print("Fetching core watchlist...")
    core_stocks = fetch_stocks(wl)
    print("Fetching market movers...")
    movers = fetch_market_movers()
    print("Fetching northbound flow...")
    northbound = fetch_northbound_flow()
    print(f"  Northbound: {northbound}")
    print("Fetching real news...")
    real_news = fetch_real_news()
    print(f"  Got {len(real_news)} news items")
    print("AI picking spotlight...")
    picks = pick_spotlight(movers, core_stocks)
    print("Fetching spotlight prices...")
    spotlight = fetch_spotlight_prices(picks)
    print("Generating CORPS from real news...")
    corps = generate_corps(real_news, core_stocks, northbound)
    print("Generating AI brief...")
    brief = generate_ai_brief(indices, core_stocks, movers, northbound, real_news)
    today = datetime.now().strftime("%Y-%m-%d")
    html = build_html(today, indices, core_stocks, spotlight, corps, brief, northbound)
    print("Sending email...")
    send_email(f"HK/China Morning Brief - {today}", html)
    print("Done!")

if __name__ == "__main__":
    main()
