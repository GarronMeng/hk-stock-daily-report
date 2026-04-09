import yfinance as yf
import akshare as ak
import json
import socket

print("=" * 60)
print("DATA SOURCE DIAGNOSTIC")
print("=" * 60)

# 1. Test indices with period=5d
print("\n--- INDICES (period=5d) ---")
for ticker, name in {"^HSI": "HSI", "^HSCE": "HSCEI", "000001.SS": "SSE", "399001.SZ": "SZSE", "399006.SZ": "ChiNext"}.items():
    try:
        h = yf.Ticker(ticker).history(period="5d")
        dates = [str(d.date()) for d in h.index]
        closes = [round(float(c), 2) for c in h["Close"]]
        print(f"  {name} ({ticker}): {len(h)} rows")
        for i, (d, c) in enumerate(zip(dates, closes)):
            print(f"    {d}: {c}")
        if len(h) >= 2:
            chg = round(closes[-1] - closes[-2], 2)
            pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
            print(f"    => change={chg}, pct={pct}%")
    except Exception as e:
        print(f"  {name}: ERROR {e}")

# 2. Test northbound flow
print("\n--- NORTHBOUND FLOW ---")
try:
    socket.setdefaulttimeout(30)
    df = ak.stock_hsgt_fund_flow_summary_em()
    print(f"  Rows: {len(df)}")
    print(f"  Columns: {df.columns.tolist()}")
    for _, row in df.iterrows():
        print(f"  {dict(row)}")
except Exception as e:
    print(f"  ERROR: {e}")

# 3. Test Caixin news
print("\n--- CAIXIN NEWS ---")
try:
    socket.setdefaulttimeout(20)
    df = ak.stock_news_main_cx()
    print(f"  Total: {len(df)} items")
    for _, row in df.head(5).iterrows():
        print(f"  [{row.get('tag','')}] {str(row.get('summary',''))[:80]}")
except Exception as e:
    print(f"  ERROR: {e}")

# 4. Test individual stock news
print("\n--- STOCK NEWS (eastmoney) ---")
for sym in ["600519", "300750"]:
    try:
        socket.setdefaulttimeout(15)
        df = ak.stock_news_em(symbol=sym)
        if df is not None and not df.empty:
            title = df.iloc[0].get("新闻标题", "")
            print(f"  {sym}: {title}")
        else:
            print(f"  {sym}: empty")
    except Exception as e:
        print(f"  {sym}: ERROR {e}")

# 5. Test A-share movers
print("\n--- A-SHARE MOVERS ---")
try:
    socket.setdefaulttimeout(30)
    df = ak.stock_zh_a_spot_em()
    if df is not None and not df.empty:
        cols = df.columns.tolist()
        print(f"  Columns: {cols}")
        print(f"  Total: {len(df)} stocks")
        # Find pct column
        pct_col = None
        for c in cols:
            if "涨跌幅" in str(c):
                pct_col = c
        if pct_col:
            top3 = df.nlargest(3, pct_col)
            print(f"  Top 3 gainers:")
            for _, r in top3.iterrows():
                print(f"    {r[cols[0]]} {r[cols[1]]} {r[pct_col]}%")
    else:
        print("  empty")
except Exception as e:
    print(f"  ERROR: {e}")

# 6. Test a sample stock
print("\n--- SAMPLE STOCK (Tencent 0700.HK, period=5d) ---")
try:
    h = yf.Ticker("0700.HK").history(period="5d")
    for d, c in zip([str(d.date()) for d in h.index], [round(float(c),2) for c in h["Close"]]):
        print(f"  {d}: {c}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "=" * 60)
print("DIAGNOSTIC COMPLETE")
