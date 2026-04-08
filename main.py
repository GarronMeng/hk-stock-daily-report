import yfinance as yf
import akshare as ak
import smtplib
import json
import os
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path


def fetch_market_indices():
    tickers = {"^HSI": "Hang Seng Index", "^HSCE": "Hang Seng China Enterprises"}
    results = {}
    for ticker, name in tickers.items():
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if len(hist) >= 2:
                close = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = close - prev
                pct = chg / prev * 100
                results[name] = {"close": round(close, 2), "change": round(chg, 2), "pct": round(pct, 2)}
            elif len(hist) == 1:
                close = hist["Close"].iloc[-1]
                results[name] = {"close": round(close, 2), "change": 0, "pct": 0}
        except Exception as e:
            print(f"Error fetching {name}: {e}")
    return results


def fetch_stocks(watchlist):
    results = []
    for item in watchlist:
        ticker = item["ticker"]
        name = item["name"]
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="5d")
            if len(hist) >= 2:
                close = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                chg = close - prev
                pct = chg / prev * 100
                vol = hist["Volume"].iloc[-1]
                if vol >= 1e9:
                    vol_str = f"{vol/1e9:.2f}B"
                elif vol >= 1e6:
                    vol_str = f"{vol/1e6:.1f}M"
                else:
                    vol_str = f"{vol/1e3:.0f}K"
                results.append({"name": name, "ticker": ticker, "close": round(close, 2), "change": round(chg, 2), "pct": round(pct, 2), "volume": vol_str})
            elif len(hist) == 1:
                close = hist["Close"].iloc[-1]
                vol = hist["Volume"].iloc[-1]
                vol_str = f"{vol/1e6:.1f}M" if vol >= 1e6 else f"{vol/1e3:.0f}K"
                results.append({"name": name, "ticker": ticker, "close": round(close, 2), "change": 0, "pct": 0, "volume": vol_str})
        except Exception as e:
            print(f"Error fetching {name}({ticker}): {e}")
    return results


def fetch_news():
    news_list = []
    try:
        df = ak.stock_hk_ggt_components_em()
        if df is not None and not df.empty:
            for _, row in df.head(5).iterrows():
                news_list.append(str(row.iloc[0]) + " - " + str(row.iloc[1]))
    except Exception as e:
        print(f"akshare ggt error: {e}")
    if not news_list:
        news_list.append("No notable news today")
    return news_list


def generate_ai_summary(indices, stocks, news):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    if not api_key:
        return "OpenAI API Key not configured, skipping AI summary"
    idx_text = "\n".join([f"{k}: {v['close']} ('{'+' if v['pct']>=0 else ''}{v['pct']}%)" for k, v in indices.items()])
    stock_lines = []
    for s in stocks:
        stock_lines.append(f"{s['name']}({s['ticker']}): {s['close']} ('{'+' if s['pct']>=0 else ''}{s['pct']}%) Vol:{s['volume']}")
    stock_text = "\n".join(stock_lines)
    news_text = "\n".join(news)
    prompt = f"You are a professional HK stock market analyst. Based on the following data, generate a concise daily market summary (within 200 words) in English:\n\nMarket Indices:\n{idx_text}\n\nStock Performance:\n{stock_text}\n\nMarket Info:\n{news_text}\n\nPlease include: market trend, hot sectors, key stocks, and outlook."
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "claude-opus-4-6", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"AI summary error: {e}")
        return f"AI summary generation failed: {e}"


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


def main():
    wl_path = Path(__file__).parent / "watchlist.json"
    with open(wl_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)
    print("Fetching market indices...")
    indices = fetch_market_indices()
    print("Fetching stocks...")
    stocks = fetch_stocks(watchlist)
    print("Fetching news...")
    news = fetch_news()
    print("Generating AI summary...")
    summary = generate_ai_summary(indices, stocks, news)
    today = datetime.now().strftime("%Y-%m-%d")
    tpl_path = Path(__file__).parent / "templates" / "email.html"
    with open(tpl_path, "r", encoding="utf-8") as f:
        template = f.read()
    idx_rows = ""
    for name, d in indices.items():
        color = "#e74c3c" if d["pct"] >= 0 else "#27ae60"
        sign = "+" if d["pct"] >= 0 else ""
        idx_rows += f'<tr><td style="padding:8px;border-bottom:1px solid #eee">{name}</td><td style="padding:8px;border-bottom:1px solid #eee">{d["close"]}</td><td style="padding:8px;border-bottom:1px solid #eee;color:{color}">{sign}{d["change"]}</td><td style="padding:8px;border-bottom:1px solid #eee;color:{color}">{sign}{d["pct"]}%</td></tr>'
    stock_rows = ""
    for s in stocks:
        color = "#e74c3c" if s["pct"] >= 0 else "#27ae60"
        sign = "+" if s["pct"] >= 0 else ""
        stock_rows += f'<tr><td style="padding:8px;border-bottom:1px solid #eee">{s["name"]}</td><td style="padding:8px;border-bottom:1px solid #eee">{s["ticker"]}</td><td style="padding:8px;border-bottom:1px solid #eee">{s["close"]}</td><td style="padding:8px;border-bottom:1px solid #eee;color:{color}">{sign}{s["change"]}</td><td style="padding:8px;border-bottom:1px solid #eee;color:{color}">{sign}{s["pct"]}%</td><td style="padding:8px;border-bottom:1px solid #eee">{s["volume"]}</td></tr>'
    news_items = "".join([f"<li>{n}</li>" for n in news])
    html = template.replace("{{DATE}}", today).replace("{{INDEX_ROWS}}", idx_rows).replace("{{STOCK_ROWS}}", stock_rows).replace("{{NEWS_ITEMS}}", news_items).replace("{{AI_SUMMARY}}", summary)
    subject = f"HK Stock Daily Report - {today}"
    print("Sending email...")
    send_email(subject, html)
    print("Done!")


if __name__ == "__main__":
    main()
