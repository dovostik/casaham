"""Uji GoAPI: bentuk respons, cakupan, dan jeda dibanding Yahoo. Hasil ditulis sebagai anotasi Actions."""
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
import yfinance as yf

KEY = os.environ["GOAPI_KEY"].strip()
WIB = timezone(timedelta(hours=7))
SYMS = ["BBCA", "BBRI", "TLKM", "ANTM", "GOTO"]


def note(msg):
    print("::notice::" + msg.replace("\n", " | ").replace(KEY, "***")[:3500], flush=True)


def get(path, **params):
    params["api_key"] = KEY
    url = "https://api.goapi.io" + path + "?" + urllib.parse.urlencode(params)
    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read().decode()
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = e.read().decode(), e.code
    return code, body, time.time() - t


# 1. bentuk respons tiap endpoint
for path, params in [("/stock/idx/prices", {"symbols": ",".join(SYMS)}),
                     ("/stock/idx/trending", {}),
                     ("/stock/idx/top_gainer", {}),
                     ("/stock/idx/companies", {}),
                     ("/stock/idx/BBCA/historical", {"from": "2026-11-10", "to": "2026-11-18"})]:
    code, body, dt = get(path, **params)
    note(f"{path} HTTP {code} {dt:.1f}s len={len(body)} :: {body[:600]}")
    time.sleep(2)

# 2. batas jumlah simbol per panggilan
companies = []
try:
    _, body, _ = get("/stock/idx/companies")
    companies = [c.get("symbol") for c in json.loads(body)["data"]["results"]]
except Exception as e:
    note(f"companies parse gagal: {e}")
if companies:
    big = companies[:200]
    code, body, dt = get("/stock/idx/prices", symbols=",".join(big))
    try:
        n = len(json.loads(body)["data"]["results"])
    except Exception:
        n = "?"
    note(f"prices 200 simbol: HTTP {code} {dt:.1f}s hasil={n} :: {body[:300]}")

# 3. jeda: bandingkan tiap 60 dtk selama 6 menit
for i in range(6):
    code, body, _ = get("/stock/idx/prices", symbols=",".join(SYMS))
    g = {}
    try:
        for r in json.loads(body)["data"]["results"]:
            g[r["symbol"]] = r
    except Exception:
        pass
    y = {}
    for s in SYMS:
        try:
            fi = yf.Ticker(s + ".JK").fast_info
            y[s] = fi["last_price"]
        except Exception:
            y[s] = None
    yd = yf.download([s + ".JK" for s in SYMS], period="1d", interval="1m", progress=False, group_by="ticker")
    ylast = {}
    for s in SYMS:
        try:
            d = yd[s + ".JK"].dropna()
            ylast[s] = d.index[-1].tz_convert(WIB).strftime("%H:%M")
        except Exception:
            ylast[s] = "-"
    now = datetime.now(WIB).strftime("%H:%M:%S")
    parts = []
    for s in SYMS:
        r = g.get(s, {})
        parts.append(f"{s} goapi={r.get('close')} (t={r.get('last_update') or r.get('date') or r.get('time')}) yahoo={y[s]} (bar {ylast[s]})")
    note(f"[{now}] HTTP {code} :: " + " ; ".join(parts))
    time.sleep(60)
