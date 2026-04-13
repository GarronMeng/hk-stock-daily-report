import yfinance as yf
import akshare as ak
import smtplib
import json
import os
import sys
import socket
import requests
import math
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
if not BASE_URL.endswith("/v1"):
    BASE_URL = BASE_URL.rstrip("/") + "/v1"
MODEL = "claude-opus-4-6"
REVIEW_MODE = "--review" in sys.argv

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
    """HSI/HSCEI via yfinance; SSE/SZSE/ChiNext via akshare spot (more reliable)."""
    results = {}
    # HK indices via yfinance
    for t, name in {"^HSI": "HSI", "^HSCE": "HSCEI"}.items():
        try:
            h = yf.Ticker(t).history(period="10d")
            closes = h["Close"].dropna()
            if len(closes) >= 2:
                c, p = float(closes.iloc[-1]), float(closes.iloc[-2])
                results[name] = {"close": round(c, 2), "change": round(c-p, 2), "pct": round((c-p)/p*100, 2)}
            elif len(closes) == 1:
                results[name] = {"close": round(float(closes.iloc[-1]), 2), "change": 0, "pct": 0}
        except Exception as e: print(f"Index error {name}: {e}")
    # A-share indices via akshare (avoids yfinance 1-row issue for ChiNext)
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(20)
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        socket.setdefaulttimeout(old_to)
        if df is not None and not df.empty:
            cols = df.columns.tolist()
            print(f"  A-index cols: {cols}")
            # Map: 上证指数->SSE, 深证成指->SZSE, 创业板指->ChiNext
            name_map = {"上证指数": "SSE", "深证成指": "SZSE", "创业板指": "ChiNext"}
            for _, row in df.iterrows():
                idx_name = str(row.get("名称", "") or row.get(cols[1], ""))
                if idx_name in name_map:
                    key = name_map[idx_name]
                    try:
                        close_val = float(row.get("最新价", 0) or row.get(cols[2], 0) or 0)
                        pct_val = float(row.get("涨跌幅", 0) or 0)
                        chg_val = float(row.get("涨跌额", 0) or 0)
                        results[key] = {"close": round(close_val, 2), "change": round(chg_val, 2), "pct": round(pct_val, 2)}
                    except Exception as e2: print(f"  A-index parse error {idx_name}: {e2}")
    except Exception as e: print(f"A-share index error: {e}")
    # Fallback: if SSE/SZSE/ChiNext still missing, try yfinance
    for t, name in {"000001.SS": "SSE", "399001.SZ": "SZSE", "399006.SZ": "ChiNext"}.items():
        if name not in results:
            try:
                h = yf.Ticker(t).history(period="10d")
                closes = h["Close"].dropna()
                if len(closes) >= 2:
                    c, p = float(closes.iloc[-1]), float(closes.iloc[-2])
                    results[name] = {"close": round(c, 2), "change": round(c-p, 2), "pct": round((c-p)/p*100, 2)}
                elif len(closes) == 1:
                    results[name] = {"close": round(float(closes.iloc[-1]), 2), "change": 0, "pct": 0}
            except Exception as e: print(f"Index fallback error {name}: {e}")
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
    """Use stock_hsgt_fund_flow_summary_em for today real-time data.
    北向 net buy is 0 during trading hours (policy change since Aug 2024).
    We show southbound (港股通) which still has live data, plus trading status.
    """
    try:
        old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(20)
        df = ak.stock_hsgt_fund_flow_summary_em()
        socket.setdefaulttimeout(old_to)
        if df is None or df.empty:
            print("  NB: summary_em returned empty")
            return {}
        cols = df.columns.tolist()
        print(f"  NB summary cols: {cols}")
        result = {"detail": {}, "date": "", "status_note": ""}
        nb_total = 0.0
        sb_total = 0.0
        trade_date = ""
        for _, row in df.iterrows():
            board = str(row.get("板块", ""))
            direction = str(row.get("资金方向", ""))
            status = row.get("交易状态", 0)
            net_buy = float(row.get("成交净买额", 0) or 0)
            net_flow = float(row.get("资金净流入", 0) or 0)
            trade_date = str(row.get("交易日", ""))
            if direction == "北向":
                result["detail"][board] = round(net_buy, 2)
                nb_total += net_buy
            elif direction == "南向":
                result["detail"][board] = round(net_buy, 2)
                sb_total += net_buy
        result["northbound_total_bn"] = round(nb_total / 100, 2)  # 亿 -> 百亿 display
        result["southbound_total_bn"] = round(sb_total / 100, 2)
        result["northbound_raw_yi"] = round(nb_total, 2)   # raw 亿 CNY
        result["southbound_raw_yi"] = round(sb_total, 2)
        result["date"] = trade_date
        # Note: since Aug 2024, northbound net buy is no longer published intraday
        if nb_total == 0:
            result["status_note"] = "Northbound net buy not published (policy change Aug 2024). Southbound (HK Connect) data available."
        print(f"  NB summary: date={trade_date}, northbound={nb_total:.2f}亿, southbound={sb_total:.2f}亿")
        return result
    except Exception as e:
        print(f"Northbound flow error: {e}")
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
    prompt = f"""Based on today market data, pick 5-8 notable stocks from BOTH HK and A-share markets. For each provide code, English name, one-line reason.

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
    sep = "\n"
    news_text = sep.join([f"[{n.get('source','')}][{n.get('tag','')}] {n['text']}" for n in real_news[:15]])
    stocks_ctx = ", ".join([f"{s['name']}({s['ticker']}) {s['pct']}%" for s in core_stocks[:10]])
    nb_note = northbound.get("status_note", "") if northbound else ""
    nb_sb = f"Southbound (HK Connect) net buy: {northbound.get('southbound_raw_yi', 'N/A')} yi CNY" if northbound else ""
    prompt = (
        "You are a CLSA equity sales trader writing the CORPS section of a morning brief.\n"
        "Translate and curate these REAL Chinese market news into 6-10 concise English one-line bullets.\n\n"
        "Format each line as: COMPANY_NAME (CODE): One sentence about the news.\n"
        "If news is about macro/policy, format as: MACRO/POLICY: One sentence.\n\n"
        f"Real news (translate accurately, do NOT fabricate):\n{news_text}\n\n"
        f"Context - key stocks today: {stocks_ctx}\n"
        f"{nb_sb}\n"
        f"{nb_note}\n\n"
        "Rules:\n"
        "- Translate accurately from the Chinese news source\n"
        "- Be specific with numbers and facts\n"
        "- Skip vague or duplicate items\n"
        "- Output ONLY the bullet lines, one per line, no numbering, no markdown"
    )
    return llm_call(prompt, max_tokens=900)


def generate_ai_brief(indices, core_stocks, movers, northbound, real_news):
    idx = ", ".join([f"{k} {v['pct']}%" for k,v in indices.items()])
    stocks = ", ".join([f"{s['name']} {s['pct']}%" for s in core_stocks[:10]])
    nb_note = northbound.get("status_note", "") if northbound else ""
    sb_val = northbound.get("southbound_raw_yi", "N/A") if northbound else "N/A"
    nb_text = f"Southbound (HK Connect) net buy: {sb_val} yi CNY. {nb_note}" if northbound else "Flow data unavailable."
    sep = " | "
    news_text = sep.join([n["text"][:60] for n in real_news[:8]]) if real_news else "No news available."
    prompt = (
        "You are a CLSA strategist. Write a 3-4 sentence market overview for today HK/China markets.\n\n"
        f"Indices: {idx}\n"
        f"Key stocks: {stocks}\n"
        f"Capital flows: {nb_text}\n"
        f"Key headlines: {news_text}\n\n"
        "Be direct, specific, professional. Reference southbound flows and key catalysts. Plain text only."
    )
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
                row += "<td></td><td></td>"
        row += "</tr>"
        sg += row
    flow_html = ""
    if northbound:
        sb_val = northbound.get("southbound_raw_yi", 0) or 0
        nb_val = northbound.get("northbound_raw_yi", 0) or 0
        trade_date = northbound.get("date", "")
        status_note = northbound.get("status_note", "")
        flow_html = '<div style="margin:16px 0">'
        flow_html += '<div style="color:#888;font-size:11px;margin-bottom:6px">CROSS-BORDER FLOWS'
        if trade_date: flow_html += f' ({trade_date})'
        flow_html += '</div>'
        sb_c = "#e8a838" if sb_val >= 0 else "#5cb85c"
        sb_s = "+" if sb_val >= 0 else ""
        flow_html += f'<div style="margin-bottom:4px"><span style="color:#aaa;font-size:11px">Southbound (HK Connect): </span><span style="color:{sb_c};font-weight:bold">{sb_s}{sb_val:.1f} 亿 CNY</span></div>'
        if status_note:
            flow_html += f'<div style="color:#666;font-size:10px;font-style:italic">{status_note}</div>'
        elif nb_val != 0:
            nb_c = "#e8a838" if nb_val >= 0 else "#5cb85c"
            nb_s = "+" if nb_val >= 0 else ""
            flow_html += f'<div style="margin-bottom:4px"><span style="color:#aaa;font-size:11px">Northbound: </span><span style="color:{nb_c};font-weight:bold">{nb_s}{nb_val:.1f} 亿 CNY</span></div>'
        flow_html += "</div>"
    corps_html = ""
    if corps_text:
        for line in corps_text.strip().split("\n"):
            line = line.strip()
            if line:
                corps_html += f'<div style="margin-bottom:6px">&bull; {line}</div>'
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


def generate_review_report(indices, core_stocks, movers, northbound, real_news, corps_text, spotlight, brief_text):
    lines = []
    lines.append("=" * 70)
    lines.append("DATA REVIEW REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    lines.append("\n[INDICES]")
    idx_ok = 0
    for name, d in indices.items():
        ok = d["pct"] != 0 or d["change"] != 0
        mark = "OK" if ok else "WARN: pct=0 (may be 1-row data)"
        lines.append(f"  {name:10} {d['close']:>10,.2f}  {d['pct']:>+7.2f}%  [{mark}]")
        if ok: idx_ok += 1
    lines.append(f"  => {idx_ok}/{len(indices)} indices have valid change%")

    lines.append("\n[CROSS-BORDER FLOWS]")
    if northbound:
        lines.append(f"  Date: {northbound.get('date', 'N/A')}")
        lines.append(f"  Southbound (HK Connect): {northbound.get('southbound_raw_yi', 'N/A')} 亿 CNY")
        lines.append(f"  Northbound: {northbound.get('northbound_raw_yi', 'N/A')} 亿 CNY")
        if northbound.get("status_note"):
            lines.append(f"  Note: {northbound['status_note']}")
        for board, val in northbound.get("detail", {}).items():
            lines.append(f"    {board}: {val} 亿")
    else:
        lines.append("  WARN: No flow data")

    lines.append(f"\n[WATCHLIST] {len(core_stocks)} stocks")
    for s in core_stocks:
        ok = s["pct"] != 0 or s["close"] > 0
        mark = "OK" if ok else "WARN"
        lines.append(f"  {s['name'][:18]:18} ({s['ticker']:12}) {s['close']:>10.2f}  {s['pct']:>+7.2f}%  [{mark}]")

    lines.append("\n[A-SHARE MOVERS]")
    if movers:
        gainers = movers.get("top_gainers", [])
        losers = movers.get("top_losers", [])
        lines.append(f"  Top gainer: {gainers[0]['name']} ({gainers[0]['code']}) +{gainers[0]['pct']}%" if gainers else "  No gainers")
        lines.append(f"  Top loser:  {losers[0]['name']} ({losers[0]['code']}) {losers[0]['pct']}%" if losers else "  No losers")
    else:
        lines.append("  WARN: No movers data (API timeout?)")

    lines.append(f"\n[NEWS] {len(real_news)} items")
    caixin = [n for n in real_news if n.get("source") == "Caixin"]
    em = [n for n in real_news if n.get("source") == "EastMoney"]
    lines.append(f"  Caixin: {len(caixin)}, EastMoney: {len(em)}")
    for n in real_news[:5]:
        lines.append(f"  [{n.get('source','')}] {n['text'][:100]}")

    lines.append("\n[CORPS - AI TRANSLATION]")
    if corps_text:
        corps_lines = [l.strip() for l in corps_text.strip().split("\n") if l.strip()]
        lines.append(f"  {len(corps_lines)} bullets generated")
        for cl in corps_lines:
            lines.append(f"  > {cl[:120]}")
    else:
        lines.append("  WARN: No CORPS content generated")

    lines.append(f"\n[SPOTLIGHT] {len(spotlight)} picks")
    for s in spotlight:
        lines.append(f"  {s['name'][:20]:20} ({s['ticker']:12}) {s['pct']:>+7.2f}%  {s['reason'][:80]}")

    lines.append("\n[AI BRIEF]")
    if brief_text:
        for line in brief_text.strip().split("\n"):
            if line.strip():
                lines.append(f"  {line[:120]}")
    else:
        lines.append("  WARN: No AI brief generated")

    issues = []
    for name, d in indices.items():
        if d["pct"] == 0 and d["change"] == 0: issues.append(f"{name} pct=0")
    if not northbound: issues.append("No flow data")
    if not real_news: issues.append("No news")
    if not corps_text: issues.append("No CORPS")
    if not spotlight: issues.append("No spotlight")
    if not brief_text: issues.append("No AI brief")
    lines.append("\n" + "=" * 70)
    lines.append(f"ISSUES ({len(issues)}): {', '.join(issues) if issues else 'None - all good!'}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    wl = json.load(open(Path(__file__).parent/"watchlist.json","r",encoding="utf-8"))
    print(f"Mode: {'REVIEW' if REVIEW_MODE else 'SEND'}")

    print("Fetching market indices...")
    indices = fetch_market_indices()
    print(f"  Indices: {json.dumps(indices, default=str)}")

    print("Fetching core watchlist...")
    core_stocks = fetch_stocks(wl)

    print("Fetching market movers...")
    movers = fetch_market_movers()

    print("Fetching northbound/southbound flow...")
    northbound = fetch_northbound_flow()
    print(f"  Flow: {northbound}")

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

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"HTML saved: {html_path}")

    review = generate_review_report(indices, core_stocks, movers, northbound, real_news, corps, spotlight, brief)
    review_path = out_dir / "review.txt"
    review_path.write_text(review, encoding="utf-8")
    print(f"Review saved: {review_path}")
    print("\n" + review)

    if REVIEW_MODE:
        print("\nREVIEW MODE: email not sent. Check output/report.html and output/review.txt")
    else:
        print("Sending email...")
        send_email(f"HK/China Morning Brief - {today}", html)
    print("Done!")


if __name__ == "__main__":
    main()
