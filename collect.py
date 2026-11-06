"""
新手晨報：每日資料收集
-----------------------------------------
由 GitHub Actions 每天台北時間 05:30 執行，輸出 data/latest.json。
每個資料來源都獨立 try/except：某一個來源失敗，其他照常產出，該欄位為 null。
"""
import datetime as dt
import io
import json
import math
import os
import time
import traceback

import numpy as np
import pandas as pd
import requests

TZ = dt.timezone(dt.timedelta(hours=8))
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CLOSES_FILE = os.path.join(DATA_DIR, "twse_closes.csv.gz")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
KEEP_DAYS = 140          # 個股收盤價保留的交易日數（算 120 日均線需要）
ERRORS = []              # 失敗紀錄，會寫進 latest.json 方便除錯

US_INDEX = [
    ("^DJI", "道瓊工業指數", "Dow Jones", "美國 30 家老牌大公司"),
    ("^GSPC", "標普 500", "S&P 500", "美國 500 家大公司，最常拿來代表美股"),
    ("^IXIC", "那斯達克", "Nasdaq", "科技股比較多"),
    ("^SOX", "費城半導體", "SOX", "晶片公司，跟台股關係最密切"),
]
STOCKS = [
    ("NVDA", "輝達", "做 AI 晶片的龍頭"),
    ("TSM", "台積電 ADR", "台積電在美國掛牌的股票"),
    ("AAPL", "蘋果", "iPhone、Mac"),
    ("INTC", "英特爾", "電腦處理器、晶圓代工"),
    ("ARM", "安謀", "設計晶片架構，授權給別人用"),
    ("PLTR", "Palantir", "數據分析與 AI 軟體"),
]
QUOTES = {  # key: yahoo 代號
    "vix": "^VIX", "vxn": "^VXN", "move": "^MOVE", "hyg": "HYG",
    "y10": "^TNX", "dxy": "DX-Y.NYB", "brent": "BZ=F", "wti": "CL=F",
    "gold": "GC=F", "btc": "BTC-USD",
}


def log_err(where):
    msg = f"{where}: {traceback.format_exc(limit=2).strip().splitlines()[-1]}"
    print("ERROR", msg)
    ERRORS.append(msg)


def r(x, n=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, n)


# ---------------- Yahoo Finance ----------------
def yf_close(sym, period="1mo"):
    import yfinance as yf
    df = yf.download(sym, period=period, interval="1d", auto_adjust=False,
                     progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    if len(s) < 2:
        raise ValueError(f"{sym} 資料不足")
    return s


def quote(sym):
    s = yf_close(sym)
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    return {"v": r(last, 4), "chg": r(last - prev, 4), "pct": r((last / prev - 1) * 100, 2),
            "date": s.index[-1].strftime("%Y-%m-%d")}


# ---------------- 證交所：全部上市股票每日收盤 ----------------
def twse_day(date):
    """回傳 {代號: 收盤價}；非交易日回傳 None"""
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    resp = requests.get(url, params={"date": date.strftime("%Y%m%d"), "type": "ALLBUT0999",
                                     "response": "json"}, headers=UA, timeout=30)
    js = resp.json()
    if js.get("stat") != "OK":
        return None
    for t in js.get("tables", []):
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            ic, ip = f.index("證券代號"), f.index("收盤價")
            out = {}
            for row in t.get("data", []):
                code = str(row[ic]).strip()
                # 只留一般股票：4 碼數字、不是 0 開頭（排除 ETF）
                if len(code) == 4 and code.isdigit() and code[0] != "0":
                    try:
                        out[code] = float(str(row[ip]).replace(",", ""))
                    except ValueError:
                        pass  # "--" 代表當天沒成交
            return out
    return None


def update_closes(trading_days):
    """把缺的交易日補齊，存成長表 date,code,close"""
    if os.path.exists(CLOSES_FILE):
        df = pd.read_csv(CLOSES_FILE, dtype={"code": str})
    else:
        df = pd.DataFrame(columns=["date", "code", "close"])
    have = set(df["date"].unique())
    want = [d for d in trading_days[-KEEP_DAYS:] if d.strftime("%Y-%m-%d") not in have]
    print(f"證交所：需要補 {len(want)} 天")
    new = []
    for i, d in enumerate(want):
        try:
            day = twse_day(d)
            if day:
                ds = d.strftime("%Y-%m-%d")
                new.append(pd.DataFrame({"date": ds, "code": list(day), "close": list(day.values())}))
        except Exception:
            log_err(f"twse {d:%Y-%m-%d}")
        if i < len(want) - 1:
            time.sleep(3)  # 證交所有流量限制，別抓太快
    if new:
        df = pd.concat([df] + new, ignore_index=True)
    keep = sorted(df["date"].unique())[-KEEP_DAYS:]
    df = df[df["date"].isin(keep)].sort_values(["date", "code"])
    df.to_csv(CLOSES_FILE, index=False, compression="gzip")
    return df


def breadth_series(closes_long):
    """每天的多空排列比例（%）。短：5/10/20 日均線；長：收盤 / 60 / 120 日均線"""
    w = closes_long.pivot(index="date", columns="code", values="close").sort_index()
    w = w.ffill(limit=5)
    ma = {n: w.rolling(n, min_periods=n).mean() for n in (5, 10, 20, 60, 120)}
    s_ok = ma[20].notna()
    s_bear = ((ma[5] < ma[10]) & (ma[10] < ma[20]) & s_ok).sum(axis=1)
    s_bull = ((ma[5] > ma[10]) & (ma[10] > ma[20]) & s_ok).sum(axis=1)
    s_n = s_ok.sum(axis=1)
    l_ok = ma[120].notna() & w.notna()
    l_bear = ((w < ma[60]) & (ma[60] < ma[120]) & l_ok).sum(axis=1)
    l_bull = ((w > ma[60]) & (ma[60] > ma[120]) & l_ok).sum(axis=1)
    l_n = l_ok.sum(axis=1)
    out = pd.DataFrame({
        "shortBear": s_bear / s_n * 100, "shortBull": s_bull / s_n * 100,
        "longBear": l_bear / l_n * 100, "longBull": l_bull / l_n * 100,
        "n": s_n,
    })
    out.index = pd.to_datetime(out.index)
    return out.replace([np.inf, -np.inf], np.nan)


# ---------------- 抓底規則（來自〈台股多空排列廣度指標：轉折向上完整驗證報告〉） ----------------
def rule_table(tw, br):
    """tw: 加權指數收盤 Series；br: breadth DataFrame。回傳每天每條規則是否成立"""
    df = pd.DataFrame({"close": tw})
    df["high250"] = df["close"].rolling(250, min_periods=60).max()
    df["high60"] = df["close"].rolling(60, min_periods=20).max()
    df["low60"] = df["close"].rolling(60, min_periods=20).min()
    df["dd250"] = (df["close"] / df["high250"] - 1) * 100
    df["dd60"] = (df["close"] / df["high60"] - 1) * 100
    df = df.join(br[["shortBear"]], how="left")
    sb = df["shortBear"]
    rules = pd.DataFrame(index=df.index)
    rules["watch"] = sb >= 40
    rules["AB_warn"] = (sb >= 45) & (df["dd250"] <= -20)
    rules["HC2"] = (sb >= 80) & (df["dd250"] <= -25)
    rules["C"] = (sb >= 45) & (df["dd60"] <= -10)
    rules["C_plus"] = rules["C"] & (df["close"] <= df["low60"])
    rules["D"] = (sb >= 65) & (df["dd250"] <= -15)
    rules["E"] = (sb >= 65) & (df["dd250"] <= -20)
    rules = rules[sb.notna()]
    return df, rules


def since_info(col):
    """目前連續成立的起始日，以及最近 40 個交易日內最後一次成立的日期"""
    col = col.dropna()
    if col.empty:
        return {"on": False, "since": None, "last_on": None}
    on = bool(col.iloc[-1])
    since = None
    if on:
        i = len(col) - 1
        while i > 0 and col.iloc[i - 1]:
            i -= 1
        since = col.index[i].strftime("%Y-%m-%d")
    recent = col.iloc[-40:]
    last_on = recent[recent].index.max() if recent.any() else None
    return {"on": on, "since": since,
            "last_on": last_on.strftime("%Y-%m-%d") if last_on is not None else None}


# ---------------- 期交所 ----------------
def taifex_csv(url, data):
    resp = requests.post(url, data=data, headers=UA, timeout=30)
    for enc in ("cp950", "big5", "utf-8"):
        try:
            txt = resp.content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(txt), index_col=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def foreign_oi(today):
    start = (today - dt.timedelta(days=14)).strftime("%Y/%m/%d")
    df = taifex_csv("https://www.taifex.com.tw/cht/3/futContractsDateDown",
                    {"queryStartDate": start, "queryEndDate": today.strftime("%Y/%m/%d"),
                     "commodityId": "TXF"})
    df = df[df["身份別"].astype(str).str.contains("外資")]
    col = [c for c in df.columns if "未平倉" in c and "淨額" in c and "口數" in c][0]
    df = df.sort_values("日期")
    net = df[col].astype(str).str.replace(",", "").astype(float)
    return {"net": int(net.iloc[-1]), "chg": int(net.iloc[-1] - net.iloc[-2]) if len(net) > 1 else None,
            "date": str(df["日期"].iloc[-1]).replace("/", "-")}


def night_session(today):
    start = (today - dt.timedelta(days=5)).strftime("%Y/%m/%d")
    df = taifex_csv("https://www.taifex.com.tw/cht/3/futDataDown",
                    {"down_type": "1", "commodity_id": "TX",
                     "queryStartDate": start, "queryEndDate": today.strftime("%Y/%m/%d")})
    df = df[df["交易時段"].astype(str).str.strip() == "盤後"]
    month_col = [c for c in df.columns if c.startswith("到期月份")][0]
    df = df[df[month_col].astype(str).str.strip().str.fullmatch(r"\d{6}")]
    df = df.sort_values(["交易日期", month_col])
    last_date = df["交易日期"].iloc[-1]
    row = df[df["交易日期"] == last_date].iloc[0]  # 最近月
    px = float(str(row["收盤價"]).replace(",", ""))
    chg = float(str(row["漲跌價"]).replace(",", ""))
    return {"price": r(px, 0), "chg": r(chg, 0), "pct": r(chg / (px - chg) * 100, 2),
            "contract": str(row[month_col]).strip(), "date": str(last_date).replace("/", "-")}


# ---------------- 主程式 ----------------
def risk_score(y10, dxy, vix):
    items, total = [], 0
    if y10:
        s = 1 if y10["chg"] <= -0.02 else (-1 if y10["chg"] >= 0.02 else 0)
        items.append(["10 年期美債殖利率", f'{y10["v"]:.2f}%（{"降" if y10["chg"] < 0 else "升"} {abs(y10["chg"]):.2f}）', s]); total += s
    if dxy:
        s = 1 if dxy["pct"] <= -0.1 else (-1 if dxy["pct"] >= 0.1 else 0)
        items.append(["美元指數", f'{dxy["v"]:.1f}（{dxy["pct"]:+.2f}%）', s]); total += s
    if vix:
        s = 1 if vix["pct"] <= -3 else (-1 if vix["pct"] >= 3 else 0)
        items.append(["VIX 恐慌指數", f'{vix["v"]:.1f}（{vix["pct"]:+.1f}%）', s]); total += s
    return {"total": total, "items": items}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = dt.datetime.now(TZ)
    out = {"generated_at": now.isoformat(timespec="minutes"), "errors": ERRORS}

    # 台股加權指數（同時當作交易日曆）
    tw = None
    try:
        tw = yf_close("^TWII", "2y")
        c, p = float(tw.iloc[-1]), float(tw.iloc[-2])
        last250, last60 = tw.iloc[-250:], tw.iloc[-60:]
        rets = np.log(tw / tw.shift(1)).dropna().iloc[-250:]
        out["taiex"] = {
            "date": tw.index[-1].strftime("%Y-%m-%d"),
            "close": r(c), "chg": r(c - p), "pct": r((c / p - 1) * 100),
            "high250": r(last250.max()), "high60": r(last60.max()),
            "low60": r(last60.min()), "low60_prev": r(tw.iloc[-61:-1].min()),
            "new_low60": bool(c <= tw.iloc[-61:-1].min()),
            "vol250": r(rets.std() * math.sqrt(250) * 100, 1),
            "hist": [[d.strftime("%m-%d"), r(v)] for d, v in tw.iloc[-12:].items()],
        }
    except Exception:
        log_err("taiex")

    # 美股、個股、各種報價
    out["us"] = []
    for sym, name, en, what in US_INDEX:
        try:
            q = quote(sym)
            out["us"].append({"sym": sym, "name": name, "en": en, "what": what,
                              "close": r(q["v"]), "pct": q["pct"], "date": q["date"]})
        except Exception:
            log_err(sym)
    out["stocks"] = []
    for t, n, what in STOCKS:
        try:
            q = quote(t)
            out["stocks"].append({"t": t, "n": n, "what": what, "close": r(q["v"]), "pct": q["pct"]})
        except Exception:
            log_err(t)
    for k, sym in QUOTES.items():
        try:
            out[k] = quote(sym)
        except Exception:
            out[k] = None
            log_err(sym)
    if out.get("y10"):  # ^TNX 的單位就是 %，chg 是百分點
        out["y10"]["chg"] = r(out["y10"]["chg"], 3)
    out["us_date"] = out["us"][0]["date"] if out["us"] else None
    out["risk"] = risk_score(out.get("y10"), out.get("dxy"), out.get("vix"))

    # 多空排列廣度 + 抓底訊號
    out["breadth"], out["signals"] = None, None
    if tw is not None:
        try:
            closes = update_closes(list(tw.index))
            br = breadth_series(closes)
            last = br.dropna(subset=["shortBear"]).iloc[-1]
            out["breadth"] = {"date": br.dropna(subset=["shortBear"]).index[-1].strftime("%Y-%m-%d"),
                              "shortBear": r(last["shortBear"]), "shortBull": r(last["shortBull"]),
                              "longBear": r(last["longBear"]), "longBull": r(last["longBull"]),
                              "n": int(last["n"]),
                              "hist": [[d.strftime("%m-%d"), r(v)] for d, v in br["shortBear"].dropna().iloc[-20:].items()]}
            _, rules = rule_table(tw, br)
            out["signals"] = {k: since_info(rules[k]) for k in rules.columns}
        except Exception:
            log_err("breadth")

    # 期交所
    try:
        out["foreign"] = foreign_oi(now)
    except Exception:
        out["foreign"] = None
        log_err("foreign_oi")
    try:
        out["night"] = night_session(now)
    except Exception:
        out["night"] = None
        log_err("night_session")

    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    hist_dir = os.path.join(DATA_DIR, "history")
    os.makedirs(hist_dir, exist_ok=True)
    with open(os.path.join(hist_dir, f"{now:%Y-%m-%d}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"完成，錯誤 {len(ERRORS)} 筆")


if __name__ == "__main__":
    main()
