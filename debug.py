"""Full data source diagnostic"""
import math, socket, json

def safe(val):
    if val is None: return False
    try: return not math.isnan(float(val))
    except: return val not in (None, "", "nan")

def test_indices():
    import yfinance as yf
    print("=" * 70)
    print("1. MARKET INDICES")
    print("=" * 70)
    for t, name in {"^HSI": "HSI", "^HSCE": "HSCEI", "000001.SS": "SSE", "399001.SZ": "SZSE", "399006.SZ": "ChiNext"}.items():
        try:
            h = yf.Ticker(t).history(period="10d")
            closes = h["Close"].dropna()
            print(f"\n  {name} ({t}): raw={len(h)}, valid={len(closes)}")
            for i, (dt, val) in enumerate(closes.items()):
                m = " <--" if i == len(closes)-1 else ""
                print(f"    {str(dt.date())}: {float(val):.2f}{m}")
            if len(closes) >= 2:
                c, p = float(closes.iloc[-1]), float(closes.iloc[-2])
                print(f"    => {c:.2f}, chg={c-p:+.2f}, pct={((c-p)/p*100):+.2f}%")
            else:
                print(f"    => PROBLEM: {len(closes)} valid rows")
        except Exception as e: print(f"  {name}: ERROR {e}")

def test_northbound():
    import akshare as ak
    print("\n" + "=" * 70)
    print("2. NORTHBOUND FLOW")
    print("=" * 70)
    
    print("\n  --- hist_em(北向资金) last 10 rows ---")
    try:
        socket.setdefaulttimeout(30)
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        print(f"  Cols: {df.columns.tolist()}")
        print(f"  Total: {len(df)} rows")
        for _, row in df.tail(10).iterrows():
            d = row.get("日期", "?")
            nb = row.get("当日成交净买额", "?")
            fi = row.get("当日资金流入", "?")
            bal = row.get("当日余额", "?")
            cum = row.get("历史累计净买额", "?")
            hold = row.get("持股市值", "?")
            print(f"    {d} | 净买={str(nb):>10} {'V' if safe(nb) else 'X'} | 流入={str(fi):>10} {'V' if safe(fi) else 'X'} | 余额={str(bal):>10} {'V' if safe(bal) else 'X'} | 累计={str(cum):>10} | 持股={str(hold):>10}")
    except Exception as e: print(f"  ERROR: {e}")

    print("\n  --- fund_flow_summary_em() ---")
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        print(f"  Cols: {df.columns.tolist()}")
        for _, row in df.iterrows():
            print(f"    {dict(row)}")
    except Exception as e: print(f"  ERROR: {e}")

    for sym in ["沪股通", "深股通"]:
        print(f"\n  --- hist_em({sym}) last 5 ---")
        try:
            df = ak.stock_hsgt_hist_em(symbol=sym)
            for _, row in df.tail(5).iterrows():
                d = row.get("日期", "?")
                nb = row.get("当日成交净买额", "?")
                fi = row.get("当日资金流入", "?")
                print(f"    {d} | 净买={str(nb):>10} {'V' if safe(nb) else 'X'} | 流入={str(fi):>10} {'V' if safe(fi) else 'X'}")
        except Exception as e: print(f"    ERROR: {e}")

    # Also try stock_hsgt_board_rank_em
    print("\n  --- board_rank_em ---")
    try:
        for sym in ["北向资金增持", "北向资金减持"]:
            try:
                df = ak.stock_hsgt_board_rank_em(symbol=sym)
                if df is not None and not df.empty:
                    print(f"  {sym}: {len(df)} rows, cols={df.columns.tolist()[:5]}")
                    print(f"    sample: {df.head(2).to_dict(orient='records')}")
            except: pass
    except Exception as e: print(f"  ERROR: {e}")

def test_movers():
    import akshare as ak
    print("\n" + "=" * 70)
    print("3. A-SHARE MOVERS")
    print("=" * 70)
    try:
        socket.setdefaulttimeout(60)
        df = ak.stock_zh_a_spot_em()
        cols = df.columns.tolist()
        print(f"  Cols: {cols}")
        print(f"  Total: {len(df)}")
        pct_col = None
        for c in cols:
            if "涨跌幅" in str(c): pct_col = c
        if pct_col:
            raw = df.nlargest(5, pct_col)
            print(f"\n  Raw top 5:")
            for _, r in raw.iterrows():
                print(f"    {r[cols[0]]} {r[cols[1]]} {r[pct_col]}%")
            valid = df[(df[pct_col].abs() <= 20) & (df[pct_col].notna())]
            print(f"\n  Filtered top 5 ({len(valid)} stocks):")
            for _, r in valid.nlargest(5, pct_col).iterrows():
                print(f"    {r[cols[0]]} {r[cols[1]]} {r[pct_col]}%")
    except Exception as e: print(f"  ERROR: {e}")

def test_news():
    import akshare as ak
    print("\n" + "=" * 70)
    print("4. NEWS")
    print("=" * 70)
    try:
        socket.setdefaulttimeout(20)
        df = ak.stock_news_main_cx()
        print(f"  Caixin: {len(df)} items, cols={df.columns.tolist()}")
        for _, row in df.head(3).iterrows():
            print(f"    [{row.get('tag','')}] {str(row.get('summary',''))[:100]}")
    except Exception as e: print(f"  Caixin ERROR: {e}")
    for sym in ["600519", "300750"]:
        try:
            socket.setdefaulttimeout(15)
            df = ak.stock_news_em(symbol=sym)
            if df is not None and not df.empty:
                print(f"  {sym}: {df.iloc[0].get('新闻标题','')}")
        except Exception as e: print(f"  {sym}: ERROR {e}")

if __name__ == "__main__":
    from datetime import datetime
    print(f"Diagnostic at {datetime.now()}")
    test_indices()
    test_northbound()
    test_movers()
    test_news()
    print("\n" + "=" * 70)
    print("DONE")
