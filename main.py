import yfinance as yf
import akshare as ak
import smtplib
import json
import os
import re
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
    if not API_KEY:
        return ""
    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"LLM error: {e}")
        return ""

def fetch_market_indices():
    tickers = {"^HSI": "Hang Seng", "^HSCE": "HSCEI", "000001.SS": "SSE Comp", "399001.SZ": "SZSE Comp", "399006.SZ": "ChiNext"}
    results = {}
    for ticker, name in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                c, p = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
                results[name] = {"close": round(c, 2), "change": round(c - p, 2), "pct": round((c - p) / p * 100, 2)}
            elif len(hist) == 1:
                results[name] = {"close": round(hist["Close"].iloc[-1], 2), "change": 0, "pct": 0}
        except Exception as e:
            print(f"Index error {name}: {e}")
    return results

def fetch_stocks(watchlist):
    results = []
    for item in watchlist:
        ticker, name = item["ticker"], item["name"]
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                c, p = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
                vol = hist["Volume"].iloc[-1]
            elif len(hist) == 1:
                c, p, vol = hist["Close"].iloc[-1], hist["Close"].iloc[-1], hist["Volume"].iloc[-1]
            else:
                continue
            pct = (c - p) / p * 100 if p else 0
            if vol >= 1e9: vs = f"{vol/1e9:.2f}B"
            elif vol >= 1e6: vs = f"{vol/1e6:.1f}M"
            else: vs = f"{vol/1e3:.0f}K"
            results.append({"name": name, "ticker": ticker, "close": round(c, 2), "change": round(c - p, 2), "pct": round(pct, 2), "volume": vs})
        except Exception as e:
            print(f"Stock error {name}: {e}")
    return results

def fetch_market_movers():
    data = {}
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
        df = ak.stock_zh_a_spot_em()
        socket.setdefaulttimeout(old_timeout)
        if df is not None and not df.empty:
            cols = df.columns.tolist()
            name_col, code_col = cols[1] if len(cols) > 1 else cols[0], cols[0]
            pct_col = vol_col = None
            for c in cols:
                if "涨跌幅" in str(c): pct_col = c
                if "成交额" in str(c): vol_col = c
            if pct_col:
                top = df.nlargest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_gainers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]), 2)} for r in top]
                bot = df.nsmallest(10, pct_col)[[code_col, name_col, pct_col]].values.tolist()
                data["top_losers"] = [{"code": str(r[0]), "name": str(r[1]), "pct": round(float(r[2]), 2)} for r in bot]
            if vol_col:
                tv = df.nlargest(10, vol_col)[[code_col, name_col, vol_col]].values.tolist()
                data["top_volume"] = [{"code": str(r[0]), "name": str(r[1]), "vol_cny": round(float(r[2])/1e8, 1)} for r in tv]
    except Exception as e:
        print(f"A-share movers error: {e}")
    return data

def pick_spotlight(movers_data):
    if not API_KEY or not movers_data:
        return []
    movers_text = json.dumps(movers_data, ensure_ascii=False, default=str)
    prompt = f"""You are a senior equity analyst. Based on today's A-share market data, pick 5-8 stocks that deserve attention. For each provide:
1. Stock code (6-digit for A-share, XXXX.HK for HK)
2. English name
3. One-line reason

Data:
{movers_text}

Reply ONLY with JSON array:
[{{"code": "600519", "name": "Kweichow Moutai", "reason": "..."}}]"""
    raw = llm_call(prompt, max_tokens=600)
    if not raw: return []
    try:
        return json.loads(raw[raw.index("["):raw.rindex("]")+1])[:8]
    except Exception as e:
        print(f"Spotlight parse error: {e}")
        return []

def fetch_spotlight_prices(picks):
    results = []
    for p in picks:
        code = p.get("code", "")
        name, reason = p.get("name", code), p.get("reason", "")
        if code.endswith(".HK"): ticker = code
        elif code.startswith("6") or code.startswith("688"): ticker = code + ".SS"
        else: ticker = code + ".SZ"
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                c, p2 = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
                results.append({"name": name, "ticker": ticker, "close": round(c, 2), "pct": round((c-p2)/p2*100, 2), "reason": reason})
            elif len(hist) == 1:
                results.append({"name": name, "ticker": ticker, "close": round(hist["Close"].iloc[-1], 2), "pct": 0, "reason": reason})
        except:
            results.append({"name": name, "ticker": ticker, "close": 0, "pct": 0, "reason": reason})
    return results

def generate_ai_quick_take(indices, movers_data):
    idx_text = ", ".join([f"{k} {v['close']} ('{'+' if v['pct']>=0 else ''}{v['pct']}%)" for k, v in indices.items()])
    movers_text = json.dumps(movers_data, ensure_ascii=False, default=str) if movers_data else "N/A"
    prompt = f"""You are a CLSA equity strategist. Write a 2-3 sentence quick take on today's HK and A-share markets. Be direct and insightful, like a morning note to fund managers.

Indices: {idx_text}
A-share movers: {movers_text}

Output ONLY plain text, no markdown, no bullet points. Just 2-3 punchy sentences."""
    return llm_call(prompt, max_tokens=200)

def generate_ai_spotlight_analysis(spotlight, movers_data):
    if not spotlight: return ""
    stocks_text = "; ".join([f"{s['name']}({s['ticker']}) {s['pct']}% - {s['reason']}" for s in spotlight])
    prompt = f"""You are a CLSA equity analyst. For each stock below, write a short analysis paragraph (2-3 sentences). Be specific about catalysts, valuation, or sector dynamics.

Stocks: {stocks_text}

Output as HTML. For each stock use this exact format:
<div style="margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid #eee">
<div style="display:flex;justify-content:space-between;align-items:baseline">
<span style="font-weight:bold;font-size:15px">STOCK_NAME</span>
<span style="background:#1a3c6e;color:#fff;font-size:10px;padding:2px 8px;border-radius:3px">IMPORTANCE</span>
</div>
<div style="color:#888;font-size:12px">TICKER | CLOSE | CHG%</div>
<p style="margin:8px 0 0;font-size:13px;line-height:1.6">Your analysis here.</p>
</div>

IMPORTANCE should be HIGH, MEDIUM, or LOW based on significance.
Use the actual stock data for CLOSE and CHG%.
Output ONLY the HTML divs, nothing else."""
    return llm_call(prompt, max_tokens=1500)

def generate_ai_brief(indices, core_stocks, spotlight, movers_data):
    idx_text = ", ".join([f"{k} {v['pct']}%" for k, v in indices.items()])
    prompt = f"""You are a CLSA strategist writing the daily market brief. Cover: 1) Market overview 2) Key themes 3) Outlook.

Indices: {idx_text}
Core stocks: {", ".join([f"{s['name']} {s['pct']}%" for s in core_stocks[:10]])}

Output as clean HTML paragraphs. Use <p> tags. No markdown. No ** or ## symbols. Keep it under 150 words. Be direct and professional."""
    return llm_call(prompt, max_tokens=500)

def build_html(today, indices, core_stocks, spotlight, quick_take, spotlight_html, brief_html):
    tpl_path = Path(__file__).parent / "templates" / "email.html"
    with open(tpl_path, "r", encoding="utf-8") as f:
        template = f.read()
    def cc(pct): return "#c0392b" if pct >= 0 else "#27ae60"
    def ss(pct): return "+" if pct >= 0 else ""
    # Index cards
    cards = ""
    for name, d in indices.items():
        c = cc(d["pct"])
        cards += f'<div style="flex:1;min-width:120px;background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:10px 12px;text-align:center"><div style="font-size:11px;color:#888">{name}</div><div style="font-size:18px;font-weight:bold">{d["close"]:,.0f}</div><div style="font-size:14px;font-weight:bold;color:{c}">{ss(d["pct"])}{d["pct"]}%</div></div>'
    # Core rows
    core_rows = ""
    for i, s in enumerate(core_stocks):
        c = cc(s["pct"])
        bg = "#fafafa" if i % 2 else "#fff"
        core_rows += f'<tr style="background:{bg}"><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0">{s["name"]}</td><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;color:#888">{s["ticker"]}</td><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right">{s["close"]}</td><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right;color:{c}">{ss(s["pct"])}{s["change"]}</td><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right;color:{c};font-weight:bold">{ss(s["pct"])}{s["pct"]}%</td><td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right;color:#888">{s["volume"]}</td></tr>'
    html = template.replace("{{DATE}}", today).replace("{{INDEX_CARDS}}", cards).replace("{{CORE_ROWS}}", core_rows)
    html = html.replace("{{AI_QUICK_TAKE}}", f'<p style="margin:0;font-size:14px;line-height:1.6;font-style:italic">{quick_take}</p>')
    html = html.replace("{{SPOTLIGHT_SECTIONS}}", spotlight_html if spotlight_html else '<p style="color:#888">No spotlight picks today.</p>')
    html = html.replace("{{AI_BRIEF}}", brief_html if brief_html else '<p>AI summary unavailable.</p>')
    return html

def send_email(subject, html_body):
    addr = os.environ.get("GMAIL_ADDRESS", "")
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not addr or not pwd: print("Gmail credentials not set"); return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(addr, pwd)
            s.sendmail(addr, addr, msg.as_string())
        print("Email sent successfully")
    except Exception as e:
        print(f"Email error: {e}")

def main():
    wl = json.load(open(Path(__file__).parent / "watchlist.json", "r", encoding="utf-8"))
    print("Fetching market indices...")
    indices = fetch_market_indices()
    print("Fetching core watchlist...")
    core_stocks = fetch_stocks(wl)
    print("Fetching market movers...")
    movers = fetch_market_movers()
    print("AI picking spotlight stocks...")
    picks = pick_spotlight(movers)
    print("Fetching spotlight prices...")
    spotlight = fetch_spotlight_prices(picks)
    print("Generating AI quick take...")
    quick_take = generate_ai_quick_take(indices, movers)
    print("Generating AI spotlight analysis...")
    spotlight_html = generate_ai_spotlight_analysis(spotlight, movers)
    print("Generating AI market brief...")
    brief_html = generate_ai_brief(indices, core_stocks, spotlight, movers)
    today = datetime.now().strftime("%Y-%m-%d")
    html = build_html(today, indices, core_stocks, spotlight, quick_take, spotlight_html, brief_html)
    print("Sending email...")
    send_email(f"CLSA Daily | HK & A-Share Brief - {today}", html)
    print("Done!")

if __name__ == "__main__":
    main()
