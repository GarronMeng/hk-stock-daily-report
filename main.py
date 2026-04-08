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
    tickers = {
        "^HSI": "Hang Seng Index",
        "^HSCE": "HS China Enterprises",
        "000001.SS": "SSE Composite",
        "399001.SZ": "SZSE Component",
        "399006.SZ": "ChiNext Index",
    }
    results = {}
    for ticker, name in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                close = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = close - prev
                pct = chg / prev * 100
                results[name] = {"close": round(close, 2), "change": round(chg, 2), "pct": round(pct, 2)}
            elif len(hist) == 1:
                results[name] = {"close": round(hist["Close"].iloc[-1], 2), "change": 0, "pct": 0}
        except Exception as e:
            print(f"Error fetching {name}: {e}")
    return results


def fetch_stocks(watchlist):
    results = []
    for item in watchlist:
        ticker, name = item["ticker"], item["name"]
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) >= 2:
                close = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = close - prev
                pct = chg / prev * 100
                vol = hist["Volume"].iloc[-1]
            elif len(hist) == 1:
                close = hist["Close"].iloc[-1]
                chg, pct, vol = 0, 0, hist["Volume"].iloc[-1]
            else:
                continue
            if vol >= 1e9:
                vol_str = f"{vol/1e9:.2f}B"
            elif vol >= 1e6:
                vol_str = f"{vol/1e6:.1f}M"
            else:
                vol_str = f"{vol/1e3:.0f}K"
            results.append({"name": name, "ticker": ticker, "close": round(close, 2), "change": round(chg, 2), "pct": round(pct, 2), "volume": vol_str})
        except Exception as e:
            print(f"Error fetching {name}({ticker}): {e}")
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
            name_col = cols[1] if len(cols) > 1 else cols[0]
            code_col = cols[0]
            pct_col = None
            vol_col = None
            for c in cols:
                if "涨跌幅" in str(c):
                    pct_col = c
                if "成交额" in str(c):
                    vol_col = c
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
    prompt = f"""You are a senior CLSA equity trader covering China/HK markets. Based on today's market data below, pick 5-8 stocks that deserve special attention today. For each stock, provide:
1. Stock code (A-share: 6-digit like 600519, HK: like 0700.HK)
2. Stock name (English)
3. One-line reason (English)

Focus on: unusual volume, big price moves, sector rotation, news catalysts.

Market data:
{movers_text}

Reply ONLY with a JSON array, no other text:
[{{"code": "600519", "name": "Kweichow Moutai", "reason": "..."}}, ...]"""
    raw = llm_call(prompt, max_tokens=600)
    if not raw:
        return []
    try:
        start = raw.index("[")
        end = raw.rindex("]") + 1
        return json.loads(raw[start:end])[:8]
    except Exception as e:
        print(f"Spotlight parse error: {e}")
        return []


def fetch_spotlight_prices(picks):
    results = []
    for p in picks:
        code = p.get("code", "")
        name = p.get("name", code)
        reason = p.get("reason", "")
        if code.endswith(".HK"):
            ticker = code
        elif code.startswith("6") or code.startswith("688"):
            ticker = code + ".SS"
        else:
            ticker = code + ".SZ"
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if len(hist) >= 2:
                close = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                pct = (close - prev) / prev * 100
                results.append({"name": name, "ticker": ticker, "close": round(close, 2), "pct": round(pct, 2), "reason": reason})
            elif len(hist) == 1:
                results.append({"name": name, "ticker": ticker, "close": round(hist["Close"].iloc[-1], 2), "pct": 0, "reason": reason})
        except Exception as e:
            results.append({"name": name, "ticker": ticker, "close": 0, "pct": 0, "reason": reason})
    return results


def generate_ai_summary(indices, core_stocks, spotlight, movers_data):
    idx_text = "\n".join([f"{k}: {v['close']} ('{'+' if v['pct']>=0 else ''}{v['pct']}%)" for k, v in indices.items()])
    core_text = "\n".join([f"{s['name']}({s['ticker']}): {s['close']} ('{'+' if s['pct']>=0 else ''}{s['pct']}%)" for s in core_stocks])
    spot_text = "\n".join([f"{s['name']}({s['ticker']}): {s['close']} ('{'+' if s['pct']>=0 else ''}{s['pct']}%) - {s['reason']}" for s in spotlight]) if spotlight else "No spotlight picks today"
    movers_text = json.dumps(movers_data, ensure_ascii=False, default=str) if movers_data else "No movers data"
    prompt = f"""You are a senior CLSA equity strategist. Write a daily market brief in English covering both HK and A-share markets.

Market Indices:
{idx_text}

Core Watchlist:
{core_text}

Today's Spotlight:
{spot_text}

A-Share Movers Summary:
{movers_text}

Provide TWO sections:

## Market News & Highlights
3-5 bullet points summarizing the key market events, sector moves, and notable developments based on the data above.

## Market Brief
A concise analysis (200 words max) structured as: 1) Market Overview 2) Key Movers & Themes 3) Outlook & Risks"""
    return llm_call(prompt, max_tokens=1000)


def send_email(subject, html_body):
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_addr or not gmail_pass:
        print("Gmail credentials not set")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_addr
    msg["To"] = gmail_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_addr, gmail_pass)
            server.sendmail(gmail_addr, gmail_addr, msg.as_string())
        print("Email sent successfully")
    except Exception as e:
        print(f"Email error: {e}")


def build_html(today, indices, core_stocks, spotlight, ai_output):
    tpl_path = Path(__file__).parent / "templates" / "email.html"
    with open(tpl_path, "r", encoding="utf-8") as f:
        template = f.read()
    td = 'style="padding:8px;border-bottom:1px solid #eee"'
    def cc(pct):
        return "#e74c3c" if pct >= 0 else "#27ae60"
    def ss(pct):
        return "+" if pct >= 0 else ""
    idx_rows = ""
    for name, d in indices.items():
        c = cc(d["pct"])
        idx_rows += f'<tr><td {td}>{name}</td><td {td}>{d["close"]}</td><td {td} style="padding:8px;border-bottom:1px solid #eee;color:{c}">{ss(d["pct"])}{d["change"]}</td><td {td} style="padding:8px;border-bottom:1px solid #eee;color:{c}">{ss(d["pct"])}{d["pct"]}%</td></tr>'
    core_rows = ""
    for s in core_stocks:
        c = cc(s["pct"])
        core_rows += f'<tr><td {td}>{s["name"]}</td><td {td}>{s["ticker"]}</td><td {td}>{s["close"]}</td><td {td} style="padding:8px;border-bottom:1px solid #eee;color:{c}">{ss(s["pct"])}{s["change"]}</td><td {td} style="padding:8px;border-bottom:1px solid #eee;color:{c}">{ss(s["pct"])}{s["pct"]}%</td><td {td}>{s["volume"]}</td></tr>'
    spot_rows = ""
    for s in spotlight:
        c = cc(s["pct"])
        spot_rows += f'<tr><td {td}>{s["name"]}</td><td {td}>{s["ticker"]}</td><td {td}>{s["close"]}</td><td {td} style="padding:8px;border-bottom:1px solid #eee;color:{c}">{ss(s["pct"])}{s["pct"]}%</td><td {td}>{s["reason"]}</td></tr>'
    html = template.replace("{{DATE}}", today).replace("{{INDEX_ROWS}}", idx_rows).replace("{{CORE_ROWS}}", core_rows).replace("{{SPOTLIGHT_ROWS}}", spot_rows).replace("{{AI_OUTPUT}}", ai_output)
    return html


def main():
    wl_path = Path(__file__).parent / "watchlist.json"
    with open(wl_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)
    print("Fetching market indices...")
    indices = fetch_market_indices()
    print("Fetching core watchlist...")
    core_stocks = fetch_stocks(watchlist)
    print("Fetching market movers...")
    movers = fetch_market_movers()
    print("AI picking spotlight stocks...")
    picks = pick_spotlight(movers)
    print("Fetching spotlight prices...")
    spotlight = fetch_spotlight_prices(picks)
    print("Generating AI summary + news...")
    ai_output = generate_ai_summary(indices, core_stocks, spotlight, movers)
    today = datetime.now().strftime("%Y-%m-%d")
    html = build_html(today, indices, core_stocks, spotlight, ai_output)
    subject = f"CLSA Daily | HK & A-Share Market Brief - {today}"
    print("Sending email...")
    send_email(subject, html)
    print("Done!")


if __name__ == "__main__":
    main()
