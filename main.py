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
            h = yf.Ticker(t).history(period="2d")
            if len(h) >= 2:
                c, p = h["Close"].iloc[-1], h["Close"].iloc[-2]
                results[name] = {"close": round(c, 2), "change": round(c-p, 2), "pct": round((c-p)/p*100, 2)}
            elif len(h) == 1:
                results[name] = {"close": round(h["Close"].iloc[-1], 2), "change": 0, "pct": 0}
        except Exception as e: print(f"Index error {name}: {e}")
    return results

def fetch_stocks(watchlist):
    results = []
    for item in watchlist:
        t, name = item["ticker"], item["name"]
        try:
            h = yf.Ticker(t).history(period="5d")
            if len(h) >= 2:
                c, p = h["Close"].iloc[-1], h["Close"].iloc[-2]
                vol = h["Volume"].iloc[-1]
            elif len(h) == 1:
                c, p, vol = h["Close"].iloc[-1], h["Close"].iloc[-1], h["Volume"].iloc[-1]
            else: continue
            pct = (c-p)/p*100 if p else 0
            results.append({"name": name, "ticker": t, "close": round(c, 2), "change": round(c-p, 2), "pct": round(pct, 2), "volume": vol})
        except Exception as e: print(f"Stock error {name}: {e}")
    return results

def fetch_market_movers():
    data = {}
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
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
                top = df.nlargest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_gainers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]),2)} for r in top]
                bot = df.nsmallest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_losers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]),2)} for r in bot]
            if vol_col:
                tv = df.nlargest(10, vol_col)[[code_col, name_col, vol_col]].values.tolist()
                data["top_volume"] = [{"code": str(r[0]), "name": str(r[1]), "vol_cny": round(float(r[2])/1e8,1)} for r in tv]
    except Exception as e: print(f"A-share movers error: {e}")
    return data

def pick_spotlight(movers):
    if not API_KEY or not movers: return []
    mt = json.dumps(movers, ensure_ascii=False, default=str)
    prompt = f"""Based on today's A-share market data, pick 5-8 notable stocks. For each provide code, English name, one-line reason.

Data:
{mt}

Reply ONLY as JSON array like: [{{"code":"600519","name":"Kweichow Moutai","reason":"..."}}]"""
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
            h = yf.Ticker(ticker).history(period="2d")
            if len(h)>=2:
                c,p2 = h["Close"].iloc[-1], h["Close"].iloc[-2]
                results.append({"name":name,"ticker":ticker,"close":round(c,2),"pct":round((c-p2)/p2*100,2),"reason":reason})
            elif len(h)==1:
                results.append({"name":name,"ticker":ticker,"close":round(h["Close"].iloc[-1],2),"pct":0,"reason":reason})
        except:
            results.append({"name":name,"ticker":ticker,"close":0,"pct":0,"reason":reason})
    return results

def generate_ai_corps(core_stocks, movers):
    stocks_info = ", ".join([f"{s['name']}({s['ticker']}) {s['pct']}%" for s in core_stocks])
    movers_text = json.dumps(movers, ensure_ascii=False, default=str) if movers else "N/A"
    prompt = f"""You are a CLSA equity sales trader writing the CORPS section of the morning note. Generate 6-10 one-line news bullets about HK and China stocks.

Format each line EXACTLY as: COMPANY_NAME (CODE): One sentence about the news/development.

Base it on these stocks and market data:
Stocks: {stocks_info}
Movers: {movers_text}

Be specific with numbers, percentages, and catalysts. Write realistic market-relevant news bullets.
Output ONLY the bullet lines, one per line, no numbering, no markdown."""
    return llm_call(prompt, max_tokens=800)

def generate_ai_brief(indices, core_stocks, movers):
    idx = ", ".join([f"{k} {v['pct']}%" for k,v in indices.items()])
    stocks = ", ".join([f"{s['name']} {s['pct']}%" for s in core_stocks[:10]])
    prompt = f"""You are a CLSA strategist. Write a 3-4 sentence market overview for today's HK/China markets.

Indices: {idx}
Key stocks: {stocks}

Be direct, specific, professional. Plain text only. No markdown, no bullet points, no headers. Just a short paragraph."""
    return llm_call(prompt, max_tokens=300)

def build_html(today, indices, core_stocks, spotlight, corps_text, brief_text):
    tpl = open(Path(__file__).parent/"templates"/"email.html","r",encoding="utf-8").read()
    def cc(pct): return "#e8a838" if pct>=0 else "#5cb85c"
    def ss(pct): return "+" if pct>=0 else ""
    td = 'style="padding:3px 10px 3px 0;color:{c}"'.replace  # placeholder
    # Futures line
    fl_parts = []
    for name, d in indices.items():
        fl_parts.append(f"{name} {ss(d['pct'])}{d['change']} pts / {ss(d['pct'])}{d['pct']}%")
    futures_line = " | ".join(fl_parts[:3])
    # Index table - compact 3-column grid like ADR table
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
    # Stock grid - compact 3-across like ADR Prem/Disc
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
    # Flow section - top movers
    flow = ""
    if False:
        flow = "<div style=\"margin:16px 0\"><div style=\"color:#888;font-size:11px;margin-bottom:4px\">A-SHARE TOP MOVERS</div></div>"
    # Corps
    corps_html = ""
    if corps_text:
        for line in corps_text.strip().split("\n"):
            line = line.strip()
            if line:
                corps_html += f'<div style="margin-bottom:6px">• {line}</div>'
    # Spotlight
    spot_html = ""
    for s in spotlight:
        c = cc(s["pct"])
        spot_html += f'<div style="margin-bottom:6px"><span style="color:#e8a838">{s["name"]} ({s["ticker"]})</span> <span style="color:{c}">{ss(s["pct"])}{s["pct"]}%</span>: {s["reason"]}</div>'
    if not spot_html:
        spot_html = '<div style="color:#666">No spotlight picks today.</div>'
    # Brief
    brief_html = brief_text.replace("\n","<br>") if brief_text else "AI summary unavailable."
    dt = datetime.now().strftime("%m/%d/%y %H:%M:%S UTC+08:00")
    html = tpl.replace("{{DATETIME}}",dt).replace("{{FUTURES_LINE}}",futures_line)
    html = html.replace("{{INDEX_TABLE}}",idx_rows).replace("{{STOCK_GRID}}",sg)
    html = html.replace("{{FLOW_SECTION}}",flow).replace("{{CORPS_ITEMS}}",corps_html)
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
    print("AI picking spotlight...")
    picks = pick_spotlight(movers)
    print("Fetching spotlight prices...")
    spotlight = fetch_spotlight_prices(picks)
    print("Generating AI corps news...")
    corps = generate_ai_corps(core_stocks, movers)
    print("Generating AI brief...")
    brief = generate_ai_brief(indices, core_stocks, movers)
    today = datetime.now().strftime("%Y-%m-%d")
    html = build_html(today, indices, core_stocks, spotlight, corps, brief)
    print("Sending email...")
    send_email(f"HK/China Morning Brief - {today}", html)
    print("Done!")

if __name__ == "__main__":
    main()
