"""Ambil data saham BEI dari Yahoo Finance, hitung indikator, tulis JSON
untuk web, lalu kirim alert Telegram.

Env opsional: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, STATE_DIR (default .state)
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from yfinance import EquityQuery

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
HIST = DATA / "hist"
STATE = Path(os.environ.get("STATE_DIR", ROOT / ".state"))
WIB = timezone(timedelta(hours=7))
LIMIT = int(os.environ.get("LIMIT", "0"))  # untuk uji cepat


# ---------------------------------------------------------------- data
def fetch_quotes():
    q = EquityQuery("eq", ["region", "id"])
    quotes, off = {}, 0
    while True:
        for attempt in range(3):
            try:
                r = yf.screen(q, offset=off, size=250, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as e:  # noqa
                print("screen retry", e)
                time.sleep(5)
        else:
            raise RuntimeError("Gagal ambil daftar saham")
        qs = r.get("quotes", [])
        for x in qs:
            if x.get("symbol", "").endswith(".JK"):
                quotes[x["symbol"]] = x
        off += 250
        if len(qs) < 250 or off >= r.get("total", 0):
            break
    return quotes


def fetch_history(symbols):
    frames = {}
    chunk = 200
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        df = yf.download(part, period="1y", interval="1d", group_by="ticker",
                         auto_adjust=False, threads=True, progress=False)
        for s in part:
            try:
                d = df[s] if len(part) > 1 else df
                d = d[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                if len(d):
                    frames[s] = d
            except KeyError:
                pass
    return frames


# ---------------------------------------------------------------- indikator
def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def stoch_k(h, l, c, n=14):
    ll, hh = l.rolling(n).min(), h.rolling(n).max()
    return 100 * (c - ll) / (hh - ll).replace(0, np.nan)


def ara_limit(prev):
    if prev <= 200:
        return 35.0
    if prev <= 5000:
        return 25.0
    return 20.0


def num(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def build_row(sym, d, q):
    c, h, l, v = d["Close"], d["High"], d["Low"], d["Volume"]
    last = c.iloc[-1]
    prev = c.iloc[-2] if len(c) > 1 else last
    ma = {n: c.rolling(n).mean() for n in (5, 20, 50, 200)}
    r = rsi(c)
    k = stoch_k(h, l, c)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    val = c * v
    avgv20 = v.rolling(20).mean()
    ret = lambda n: (last / c.iloc[-1 - n] - 1) * 100 if len(c) > n else None
    ret1 = (last / prev - 1) * 100 if prev else 0
    gc = 0
    if len(c) >= 53:
        diff = (ma[20] - ma[50]).iloc[-4:]
        if (diff.iloc[0] <= 0 and diff.iloc[-1] > 0) or any(
                diff.iloc[i] <= 0 < diff.iloc[i + 1] for i in range(len(diff) - 1)):
            gc = 1
    hi52, lo52 = h.max(), l.min()
    eps, bv = q.get("epsTrailingTwelveMonths"), q.get("bookValue")
    roe = (eps / bv * 100) if eps and bv and bv > 0 else None
    lim = ara_limit(prev)
    return {
        "code": sym.replace(".JK", ""),
        "name": q.get("longName") or q.get("shortName") or "",
        "close": num(last, 0),
        "prev": num(prev, 0),
        "ret1d": num(ret1),
        "ret5d": num(ret(5)),
        "ret20d": num(ret(20)),
        "ret60d": num(ret(60)),
        "open": num(d["Open"].iloc[-1], 0),
        "high": num(h.iloc[-1], 0),
        "low": num(l.iloc[-1], 0),
        "volume": int(v.iloc[-1]) if not math.isnan(v.iloc[-1]) else 0,
        "value": num(val.iloc[-1], 0),
        "avgValue20": num(val.rolling(20).mean().iloc[-1], 0),
        "volRatio": num(v.iloc[-1] / avgv20.iloc[-1]) if avgv20.iloc[-1] else None,
        "ma5": num(ma[5].iloc[-1], 1), "ma20": num(ma[20].iloc[-1], 1),
        "ma50": num(ma[50].iloc[-1], 1), "ma200": num(ma[200].iloc[-1], 1),
        "aboveMA20": int(last > ma[20].iloc[-1]) if not math.isnan(ma[20].iloc[-1]) else 0,
        "aboveMA50": int(last > ma[50].iloc[-1]) if not math.isnan(ma[50].iloc[-1]) else 0,
        "gc2050": gc,
        "rsi14": num(r.iloc[-1], 1),
        "stochK": num(k.iloc[-1], 1),
        "macd": num(macd.iloc[-1], 2), "macdSig": num(sig.iloc[-1], 2),
        "macdHist": num(macd.iloc[-1] - sig.iloc[-1], 2),
        "high52": num(hi52, 0), "low52": num(lo52, 0),
        "fromHigh52": num((last / hi52 - 1) * 100) if hi52 else None,
        "araLimit": lim,
        "araPct": num(ret1 / lim * 100, 1) if lim else None,
        "mcap": num(q.get("marketCap"), 0),
        "per": num(q.get("trailingPE")),
        "pbv": num(q.get("priceToBook")),
        "roe": num(roe),
        "eps": num(eps),
        "divYield": num(q.get("dividendYield") or (q.get("trailingAnnualDividendYield") or 0) * 100),
    }


def hist_json(d):
    d = d.tail(260)
    out = []
    for ts, row in d.iterrows():
        out.append([ts.strftime("%Y-%m-%d"), num(row.Open, 0), num(row.High, 0),
                    num(row.Low, 0), num(row.Close, 0), int(row.Volume) if not math.isnan(row.Volume) else 0])
    return out


# ---------------------------------------------------------------- screener
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
       "<=": lambda a, b: a <= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def run_screen(rows, rules):
    hits = []
    for r in rows:
        ok = True
        for f, op, val in rules:
            x = r.get(f)
            if x is None or not OPS[op](x, val):
                ok = False
                break
        if ok:
            hits.append(r)
    return sorted(hits, key=lambda r: -(r.get("ret1d") or 0))


# ---------------------------------------------------------------- alert
def tg_send(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[telegram belum diset]\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20)
    except Exception as e:  # noqa
        print("Gagal kirim Telegram:", e)


def fmt_idr(x):
    if x is None:
        return "-"
    for div, suf in ((1e12, " T"), (1e9, " M"), (1e6, " jt")):
        if abs(x) >= div:
            return f"{x / div:.1f}{suf}"
    return f"{x:,.0f}"


def do_alerts(rows, presets, today, page_url):
    cfg_path = ROOT / "alerts.yml"
    if not cfg_path.exists():
        return []
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    STATE.mkdir(parents=True, exist_ok=True)
    sent_path = STATE / "sent.json"
    sent = json.loads(sent_path.read_text()) if sent_path.exists() else {}
    sent = {k: v for k, v in sent.items() if v == today}  # buang hari lalu
    by_code = {r["code"]: r for r in rows}
    log = []

    for pid in cfg.get("screeners") or []:
        p = presets.get(pid)
        if not p:
            continue
        hits = run_screen(rows, p["rules"])
        new = [h for h in hits if f"scr:{pid}:{h['code']}" not in sent]
        if not new:
            continue
        lines = [f"📊 <b>{p['name']}</b> — {len(new)} saham baru"]
        for h in new[:25]:
            lines.append(f"• <b>{h['code']}</b> {h['close']:,.0f} ({h['ret1d']:+.1f}%) val {fmt_idr(h['value'])}")
            sent[f"scr:{pid}:{h['code']}"] = today
        if page_url:
            lines.append(f'\n<a href="{page_url}#screener={pid}">Buka screener</a>')
        tg_send("\n".join(lines))
        log.append({"type": "screener", "id": pid, "name": p["name"], "codes": [h["code"] for h in new]})

    conds = {
        "above": ("close", ">", "Harga di atas"), "below": ("close", "<", "Harga di bawah"),
        "rsi_above": ("rsi14", ">", "RSI di atas"), "rsi_below": ("rsi14", "<", "RSI di bawah"),
        "change_above": ("ret1d", ">", "Naik lebih dari %"), "change_below": ("ret1d", "<", "Turun lebih dari %"),
    }
    for item in cfg.get("saham") or []:
        code = str(item.get("kode", "")).upper().replace(".JK", "")
        r = by_code.get(code)
        if not r:
            continue
        for key, (field, op, label) in conds.items():
            if key not in item:
                continue
            target = float(item[key])
            x = r.get(field)
            if x is None or not OPS[op](x, target):
                continue
            sid = f"px:{code}:{key}:{target}"
            if sid in sent:
                continue
            msg = (f"🔔 <b>{code}</b> — {label} {target:g}\n"
                   f"Harga {r['close']:,.0f} ({r['ret1d']:+.2f}%) · RSI {r['rsi14']}")
            if page_url:
                msg += f'\n<a href="{page_url}#chart={code}">Lihat chart</a>'
            tg_send(msg)
            sent[sid] = today
            log.append({"type": "saham", "code": code, "cond": key, "target": target, "close": r["close"]})

    sent_path.write_text(json.dumps(sent))
    return log


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    now = datetime.now(WIB)
    print("Ambil daftar saham…")
    quotes = fetch_quotes()
    syms = sorted(quotes)
    if LIMIT:
        syms = syms[:LIMIT]
    print(len(syms), "saham. Ambil riwayat harga…")
    frames = fetch_history(syms)
    print("riwayat:", len(frames))

    HIST.mkdir(parents=True, exist_ok=True)
    rows = []
    for s, d in frames.items():
        if len(d) < 15:
            continue
        try:
            row = build_row(s, d, quotes.get(s, {}))
        except Exception as e:  # noqa
            print("skip", s, e)
            continue
        rows.append(row)
        (HIST / f"{row['code']}.json").write_text(json.dumps(hist_json(d), separators=(",", ":")))

    last_date = max((d.index[-1] for d in frames.values()), default=None)
    meta = {
        "updated": now.strftime("%Y-%m-%d %H:%M WIB"),
        "lastBar": last_date.strftime("%Y-%m-%d") if last_date is not None else None,
        "count": len(rows),
        "source": "Yahoo Finance (tertunda ±10–15 menit)",
        "repo": os.environ.get("REPO", ""),
    }
    presets = {p["id"]: p for p in json.loads((DOCS / "screeners.json").read_text())["presets"]}
    page_url = os.environ.get("PAGE_URL", "")
    log = do_alerts(rows, presets, now.strftime("%Y-%m-%d"), page_url)

    # riwayat alert disimpan di STATE (bertahan antar-run lewat cache Actions)
    STATE.mkdir(parents=True, exist_ok=True)
    alog_state = STATE / "alerts_log.json"
    old = json.loads(alog_state.read_text()) if alog_state.exists() else []
    if log:
        old.insert(0, {"time": meta["updated"], "items": log})
    txt = json.dumps(old[:50], ensure_ascii=False)
    alog_state.write_text(txt)
    (DATA / "alerts_log.json").write_text(txt)

    (DATA / "summary.json").write_text(json.dumps({"meta": meta, "rows": rows},
                                                  separators=(",", ":"), ensure_ascii=False))
    (DATA / "alerts_config.json").write_text(json.dumps(
        yaml.safe_load((ROOT / "alerts.yml").read_text()) or {}, ensure_ascii=False))
    print(f"Selesai: {len(rows)} saham, {len(log)} alert, {time.time() - t0:.0f} dtk")


if __name__ == "__main__":
    sys.exit(main())
