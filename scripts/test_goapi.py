"""Uji GoAPI vs Yahoo pada saham fluktuatif.
Pembanding utama: volume kumulatif hari ini (selalu naik) -> volume lebih besar = data lebih baru.
Hemat kuota: 1 panggilan GoAPI per putaran."""
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
import yfinance as yf

KEY = os.environ["GOAPI_KEY"].strip()
WIB = timezone(timedelta(hours=7))
SYMS = os.environ.get("SYMS", "TRIN,BBYB,TRUE,TINS,AMRT,BBCA,EMAS,INET,WIFI,FOLK").split(",")
ROUNDS = int(os.environ.get("ROUNDS", "8"))


def note(msg):
    print("::notice::" + msg.replace(KEY, "***")[:4000], flush=True)


def goapi():
    url = "https://api.goapi.io/stock/idx/prices?" + urllib.parse.urlencode({"symbols": ",".join(SYMS), "api_key": KEY})
    with urllib.request.urlopen(url, timeout=20) as r:
        return {x["symbol"]: x for x in json.loads(r.read())["data"]["results"]}


ahead = {"goapi": 0, "yahoo": 0, "sama": 0}
for i in range(ROUNDS):
    t = datetime.now(WIB).strftime("%H:%M:%S")
    try:
        g = goapi()
    except Exception as e:
        note(f"[{t}] GoAPI gagal: {e}")
        break
    parts = []
    for s in SYMS:
        try:
            fi = yf.Ticker(s + ".JK").fast_info
            yp, yv = fi["last_price"], fi["last_volume"]
        except Exception:
            yp, yv = None, None
        r = g.get(s, {})
        gv = r.get("volume")
        if gv and yv:
            k = "goapi" if gv > yv * 1.001 else "yahoo" if yv > gv * 1.001 else "sama"
            ahead[k] += 1
        parts.append(f"{s} G {r.get('close')}/{(gv or 0)/1e6:.1f}jt · Y {yp}/{(yv or 0)/1e6:.1f}jt")
    note(f"[{t}] " + " ; ".join(parts))
    if i < ROUNDS - 1:
        time.sleep(60)
note(f"RINGKASAN volume lebih besar (data lebih baru): GoAPI {ahead['goapi']}x, Yahoo {ahead['yahoo']}x, sama {ahead['sama']}x")
