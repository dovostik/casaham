"""Broker summary hari bursa sebelumnya dari GoAPI (secret GOAPI_KEY).
Syarat watchlist PARTY: rata-rata harga top 3 broker net buy kemarin harus dekat
(<= 5%) dengan harga open hari ini. Dicek hanya saat sinyal memantul (hemat kuota:
paket gratis 30 permintaan/hari, 500/bulan). Hasil di-cache per saham per hari."""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

import update as U

BASE = "https://api.goapi.io/stock/idx"
CACHE = U.STATE / "broksum.json"
MAX_GAP = 5.0          # % jarak rata-rata harga top 3 buyer ke harga open
DAILY_LIMIT = 25       # sisakan cadangan dari kuota 30/hari


def _get(path, **params):
    key = os.environ.get("GOAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("GOAPI_KEY belum diset")
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-API-KEY": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch(code, day):
    """Daftar broker BUY (net) untuk satu hari."""
    js = _get(f"/{code}/broker_summary", date=day.isoformat())
    rows = (js.get("data") or {}).get("results") or []
    buys = [x for x in rows if str(x.get("side", "")).upper() == "BUY"]
    buys.sort(key=lambda x: -(float(x.get("value") or 0)))
    return buys, rows


def _load():
    try:
        return json.loads(CACHE.read_text())
    except Exception:  # noqa
        return {}


def _save(c):
    U.STATE.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c))


def top3_prev(code, today_str):
    """{"date","top","avg"} hari bursa sebelumnya, None bila kosong, "ERR" bila gagal/kuota habis."""
    c = _load()
    if c.get("_day") != today_str:
        c = {"_day": today_str, "_calls": 0}
    if code in c:
        return c[code]
    res = None
    day = date.fromisoformat(today_str)
    for back in range(1, 6):
        d = day - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        if c["_calls"] >= DAILY_LIMIT:
            return "ERR"
        c["_calls"] += 1
        _save(c)
        try:
            buys, rows = fetch(code, d)
        except Exception as e:  # noqa
            print("broksum gagal", code, e)
            return "ERR"
        if rows:
            top = [x for x in buys[:3] if x.get("avg") and x.get("value")]
            if top:
                v = [float(x["value"]) for x in top]
                avg = sum(float(x["avg"]) * w for x, w in zip(top, v)) / sum(v)
                res = {"date": d.isoformat(), "avg": round(avg, 1),
                       "top": [(x.get("broker") or {}).get("code") or x.get("code") for x in top]}
            break
    c[code] = res
    _save(c)
    return res


def check(code, today_str, day_open):
    """(lolos, teks). Data tidak tersedia -> lolos=True (tidak memblokir), teks minta cek manual."""
    band = f"{day_open * 0.95:,.0f}–{day_open * 1.05:,.0f}".replace(",", ".") if day_open else "-"
    info = top3_prev(code, today_str)
    if info == "ERR" or info is None or not day_open:
        return True, f"Broksum kemarin belum tersedia, cek manual: rata-rata top 3 buyer harus di {band} (open ±5%)"
    gap = (info["avg"] / day_open - 1) * 100
    ok = abs(gap) <= MAX_GAP
    avg_s = f"{info['avg']:,.0f}".replace(",", ".")
    txt = (f"Broksum {info['date']}: top 3 buyer {', '.join(info['top'])} rata-rata {avg_s} "
           f"({gap:+.1f}% dari open {int(day_open)}) {'✅' if ok else '❌'}")
    return ok, txt


if __name__ == "__main__":   # uji: GOAPI_KEY=... python broksum.py SOLA 2026-11-18
    import sys
    t = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    print(top3_prev(sys.argv[1], t))
