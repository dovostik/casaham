"""Jurnal sinyal Casaham: setiap saham yang masuk watchlist PARTY atau lolos BBS dibukukan,
lalu dilengkapi hasilnya (harga tutup, terkunci ARA, open besok, hasil % sesuai aturan).

File: jurnal/jurnal.json (di repo, di-commit oleh workflow realtime) + jurnal/jurnal.csv.
Hanya sinyal screener — TIDAK berisi posisi/transaksi pengguna.
"""
import csv
import json
from datetime import date, datetime

import update as U

DIR = U.ROOT / "jurnal"
PATH = DIR / "jurnal.json"
FEE = 0.4

CSV_COLS = ["id", "setup", "date", "time_in", "code", "band", "session", "price_in", "prev", "ret_in", "value_in",
            "ara", "ara_pct_in", "stage", "t_corr", "t_bounce", "bounce_price", "sl", "tp1", "tp2", "H",
            "broksum_ok", "broksum", "t_out", "t_fail", "tp1_hit", "tp2_hit", "brk",
            "close", "locked", "high_after", "low_after", "n_date", "n_open", "result", "win", "note"]


def load():
    try:
        return json.loads(PATH.read_text())
    except Exception:  # noqa
        return []


def save(rows):
    DIR.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=0))
    with open(DIR / "jurnal.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def band(prev):
    return "<200" if prev < 200 else "200-5000" if prev <= 5000 else ">5000"


def add(setup, code, today, time_in, price, prev, value=None, extra=None):
    rows = load()
    rid = f"{today}:{setup}:{code}"
    if any(r["id"] == rid for r in rows):
        return rid
    r = {"id": rid, "setup": setup, "date": today, "time_in": time_in, "code": code,
         "band": band(prev), "session": "pagi" if time_in < "12:00" else "siang",
         "price_in": price, "prev": prev, "ret_in": round((price / prev - 1) * 100, 2) if prev else None,
         "value_in": value, "ara": U.ara_price(prev), "ara_pct_in": round((price / prev - 1) * 100 / U.ara_limit(prev) * 100, 1) if prev else None,
         "stage": "WATCH" if setup == "PARTY" else "SIGNAL"}
    r.update(extra or {})
    rows.append(r)
    save(rows)
    return rid


def update(rid, **fields):
    rows = load()
    for r in rows:
        if r["id"] == rid:
            r.update(fields)
            break
    save(rows)


# ------------------------------------------------------------------ hasil
def _party_result(r):
    """Hasil mekanis PARTY: hanya bila sinyal memantul diambil (broksum lolos)."""
    if not r.get("bounce_price") or r.get("broksum_ok") is False:
        return None
    e, close = r["bounce_price"], r.get("close")
    if r.get("t_fail") and not r.get("tp1_hit"):
        px = r["sl"]
    elif r.get("tp1_hit"):
        rest = r["tp2"] if r.get("tp2_hit") else (e if r.get("t_fail") else close or e)
        px = 0.5 * r["tp1"] + 0.5 * rest
    else:
        px = close or e
    return round((px / e - 1) * 100 - FEE, 2)


def _bbs_result(r):
    if r.get("close") is None:
        return None
    if r.get("locked"):
        if r.get("n_open") is None:
            return None
        px = r["n_open"]
    else:
        px = r["close"]
    return round((px / r["price_in"] - 1) * 100 - FEE, 2)


def finalize(r):
    res = _party_result(r) if r["setup"] == "PARTY" else _bbs_result(r)
    r["result"] = res
    r["win"] = None if res is None else int(res > 0)


def close_day(today, quotes, intraday=None):
    """Akhir sesi 2: isi harga tutup, terkunci ARA, high/low setelah sinyal."""
    rows = load()
    for r in rows:
        if r["date"] != today:
            continue
        q = quotes.get(r["code"] + ".JK") or {}
        px = q.get("regularMarketPrice")
        if not px:
            continue
        r["close"] = float(px)
        r["locked"] = int(px >= r["ara"])
        b = (intraday or {}).get(r["code"])
        t_ref = r.get("t_bounce") or r["time_in"]
        if b is not None and len(b):
            after = b[b.index.strftime("%H:%M") >= t_ref]
            if len(after):
                r["high_after"] = float(after["High"].max())
                r["low_after"] = float(after["Low"].min())
        finalize(r)
    save(rows)


def fill_next_open(today, quotes):
    """Awal sesi: isi harga open hari ini untuk sinyal hari bursa sebelumnya."""
    rows = load()
    changed = False
    prev_days = sorted({r["date"] for r in rows if r["date"] < today})
    if not prev_days:
        return
    last = prev_days[-1]
    for r in rows:
        if r["date"] != last or r.get("n_open") is not None:
            continue
        q = quotes.get(r["code"] + ".JK") or {}
        o = q.get("regularMarketOpen")
        if o:
            r["n_open"], r["n_date"] = float(o), today
            finalize(r)
            changed = True
    if changed:
        save(rows)


# ------------------------------------------------------------------ statistik
def _grp(rows, key):
    out = {}
    for r in rows:
        if r.get("result") is None:
            continue
        out.setdefault(r.get(key), []).append(r["result"])
    return {k: (len(v), sum(x > 0 for x in v) / len(v) * 100, sum(v) / len(v)) for k, v in out.items()}


def _fmt(g):
    return " · ".join(f"{k}: {n} sinyal, menang {w:.0f}%, rata2 {a:+.2f}%".replace(".", ",")
                      for k, (n, w, a) in sorted(g.items(), key=lambda x: str(x[0])))


def stats_text(since=None, title="Evaluasi jurnal"):
    rows = [r for r in load() if not since or r["date"] >= since]
    if not rows:
        return "Jurnal masih kosong."
    lines = [f"📊 <b>{title}</b>" + (f" (sejak {since})" if since else "") + f" · {len(rows)} catatan"]
    for setup in ("PARTY", "BBS"):
        rs = [r for r in rows if r["setup"] == setup]
        if not rs:
            continue
        done = [r for r in rs if r.get("result") is not None]
        lines.append(f"\n<b>{setup}</b>: {len(rs)} masuk, {len(done)} sudah ada hasil")
        if setup == "PARTY":
            n_b = sum(1 for r in rs if r.get("t_bounce"))
            n_out = sum(1 for r in rs if r.get("t_out"))
            n_skip = sum(1 for r in rs if r.get("broksum_ok") is False)
            lines.append(f"memantul {n_b} · bandar keluar {n_out} · dilewati broksum {n_skip}")
        else:
            lk = [r for r in rs if r.get("locked") is not None]
            if lk:
                lines.append(f"terkunci ARA saat tutup: {sum(r['locked'] for r in lk) / len(lk) * 100:.0f}%")
        if done:
            res = [r["result"] for r in done]
            lines.append(f"menang {sum(x > 0 for x in res) / len(res) * 100:.0f}% · rata2 {sum(res) / len(res):+.2f}% (sudah fee)".replace(".", ","))
            lines.append("per band: " + _fmt(_grp(done, "band")))
            lines.append("per sesi: " + _fmt(_grp(done, "session")))
    lines.append("\nPembanding uji: BBS 59 hari +1,38% (menang 51%); PARTY candle 1m 19 hari −0,85% (menang 38%).")
    lines.append("Detail: casaham.pages.dev/#jurnal")
    return "\n".join(lines)


def weekly_since(today):
    d = date.fromisoformat(today)
    return (d.fromordinal(d.toordinal() - d.weekday())).isoformat()   # Senin minggu ini
