"""Broker summary harian dari halaman publik Indo Premier (tanpa login).
Dipakai untuk syarat watchlist PARTY: rata-rata harga top 3 broker net buy kemarin
harus dekat (<= 5%) dengan harga open hari ini. Hasil di-cache per saham per tanggal."""
import json
import re
import urllib.request
from datetime import date, timedelta

import update as U

ENDPOINT = "https://www.indopremier.com/module/saham/include/data-brokersummary.php"
CACHE = U.STATE / "broksum.json"
MAX_GAP = 5.0   # % jarak rata-rata harga top 3 buyer ke harga open


def _num(t):
    t = re.sub(r"<[^>]*>", "", t or "").replace("&nbsp;", " ").replace(",", "").strip()
    if not t:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(t[-1:].upper())
    try:
        return float(t[:-1] if mult else t) * (mult or 1)
    except ValueError:
        return None


def parse(html):
    if re.search(r"invalid stock code", html, re.I):
        return None
    a, b = html.find("<tbody>"), html.find("</tbody>")
    if a < 0 or b < 0:
        return None
    buyers, sellers = [], []
    for row in html[a:b].split("</tr>"):
        cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)
        if len(cells) < 9:
            continue
        for side, at in ((buyers, 0), (sellers, 5)):
            code = re.sub(r"<[^>]*>", "", cells[at]).strip().upper()
            if code:
                side.append({"code": code, "lots": _num(cells[at + 1]), "value": _num(cells[at + 2]),
                             "avg": _num(cells[at + 3])})
    return {"buyers": buyers, "sellers": sellers}


def fetch(code, day):
    d = f"{day:%m/%d/%Y}"
    url = f"{ENDPOINT}?code={code}&start={d}&end={d}&fd=all&board=all"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (casaham; personal use)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse(r.read().decode("utf-8", "replace"))


def _cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:  # noqa
        return {}


def top3_prev(code, today_str):
    """Ringkasan broker hari bursa sebelumnya (mundur maks 5 hari kalender). Cache per hari."""
    c = _cache()
    key = f"{code}:{today_str}"
    if key in c:
        return c[key]
    res = None
    day = date.fromisoformat(today_str)
    for back in range(1, 6):
        d = day - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        try:
            p = fetch(code, d)
        except Exception as e:  # noqa
            print("broksum gagal", code, e)
            return None          # sumber tidak bisa diakses -> jangan cache
        if p and p["buyers"]:
            top = [x for x in p["buyers"][:3] if x["avg"] and x["value"]]
            if top:
                avg = sum(x["avg"] * x["value"] for x in top) / sum(x["value"] for x in top)
                res = {"date": d.isoformat(), "top": [x["code"] for x in top], "avg": round(avg, 1),
                       "value": sum(x["value"] for x in top)}
            break
    c = {k: v for k, v in c.items() if k.endswith(today_str)}   # simpan hanya hari ini
    c[key] = res
    U.STATE.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c))
    return res


def check(code, today_str, day_open):
    """(lolos, info). Kalau data tidak tersedia -> lolos=True dengan info 'tidak ada data' (tidak memblokir)."""
    info = top3_prev(code, today_str)
    if not info or not day_open:
        return True, None
    gap = (info["avg"] / day_open - 1) * 100
    info = dict(info, gap=round(gap, 1))
    return abs(gap) <= MAX_GAP, info


if __name__ == "__main__":   # uji: python broksum.py SOLA BBCA
    import sys
    t = date.today().isoformat()
    for c in sys.argv[1:]:
        print(c, top3_prev(c, t))
