"""Watchlist PARTY bertahap (candle 5 menit):

  1. 📌 Masuk watchlist  : saham lolos screener PARTY & masih dekat high (kapan pun di jam bursa)
  ⚠️ Bandar keluar       : candle 15m tutup di bawah tengah lonjakan (open -> high) -> keluar watchlist
  2. 🟢 Koreksi memantul : koreksi >= 35% lonjakan (dari open), lalu 2 candle 15m tanpa low baru,
                           volume mengering, candle hijau, harga masih >= +3%  -> kirim SL/TP1/TP2
  3. Watchlist lanjutan  : 🔴 koreksi gagal (harga <= SL) · 🎯 TP1/TP2 · 🚀 tembus high lonjakan
Watchlist dihapus tiap hari (state per tanggal). Setiap sinyal dicatat untuk evaluasi (/evaluasi).
"""
import json
from datetime import datetime

import pandas as pd

import update as U

RETR = 0.35         # koreksi minimal: porsi lonjakan (open -> high) yang terhapus
HALF = 0.5          # harga tutup candle di bawah tengah lonjakan = bandar dianggap keluar
DRY = 0.35          # volume 2 candle pantul <= 35% volume candle lonjakan terbesar
MIN_UP = 1.03       # harga tetap >= +3% dari harga kemarin
NEAR_HIGH = -5.0    # syarat masuk watchlist: harga maksimal 5% di bawah high hari ini (masih di fase lonjak)
TF = "15m"           # hasil uji 59 hari: 15 menit lebih baik dari 5 menit untuk aturan ini

PATH = U.STATE / "watch.json"
LOG = U.STATE / "watch_log.json"


def tick(p):
    return 1 if p < 200 else 2 if p < 500 else 5 if p < 2000 else 10 if p < 5000 else 25


def nf(x, d=0):
    s = f"{x:,.{d}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def pc(x):
    return f"{x:+.1f}%".replace(".", ",")


def load(today):
    try:
        st = json.loads(PATH.read_text())
    except Exception:  # noqa
        st = {}
    if st.get("date") != today:          # malam dihapus: mulai baru tiap hari
        st = {"date": today, "items": {}}
    return st


def save(st):
    U.STATE.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(st, indent=1))


def load_log():
    try:
        return json.loads(LOG.read_text())
    except Exception:  # noqa
        return []


def save_log(log):
    LOG.write_text(json.dumps(log[-1000:], ensure_ascii=False))


# ------------------------------------------------------------------ analisa candle
def intraday(codes):
    import yfinance as yf
    if not codes:
        return {}
    syms = [c + ".JK" for c in codes]
    df = yf.download(syms, period="1d", interval=TF, group_by="ticker", progress=False, threads=True)
    out = {}
    for c, s in zip(codes, syms):
        try:
            d = (df[s] if len(syms) > 1 else df).dropna(subset=["Close"])
            d = d[d["Volume"] > 0]
            if len(d):
                d.index = d.index.tz_convert("Asia/Jakarta")
                out[c] = d.iloc[:-1]          # buang candle terakhir (belum selesai)
        except Exception:  # noqa
            pass
    return out


def find_bounce(bars, prev, added_at, day_open=None):
    """Cari koreksi + pantulan sejak saham masuk watchlist.
    Lonjakan diukur dari harga open. Return dict (time= sinyal pantul, out= bandar keluar) atau None."""
    if len(bars) < 3:
        return None
    base = float(day_open or bars["Open"].iloc[0])
    t0 = pd.Timestamp(added_at).floor("15min")
    pre = bars[bars.index <= t0]
    H = float(pre["High"].max()) if len(pre) else float(bars["High"].iloc[0])
    spike_vol = float(pre["Volume"].max()) if len(pre) else 0.0
    corr_i, L = None, None
    idx = list(bars.index)
    start = len(pre)
    for i in range(start, len(bars)):
        b = bars.iloc[i]
        if corr_i is None:
            H = max(H, float(b.High))
            spike_vol = max(spike_vol, float(b.Volume))
            if (H - b.Close) >= RETR * (H - base) and b.Close >= prev * MIN_UP:
                corr_i, L = i, float(b.Low)
            continue
        if b.Close < base + HALF * (H - base):
            return {"H": H, "out": True, "price": float(b.Close), "mid": base + HALF * (H - base),
                    "time_out": idx[i].strftime("%H:%M")}
        if i >= corr_i + 2:
            b1 = bars.iloc[i - 1]
            if (b1.Low > L and b.Low > L and b.Close > b.Open and b.Close >= prev * MIN_UP
                    and (b1.Volume + b.Volume) / 2 <= DRY * spike_vol):
                return {"H": H, "L": L, "time": idx[i].strftime("%H:%M"), "close": float(b.Close),
                        "retr": (H - L) / (H - base) * 100 if H > base else 0}
        L = min(L, float(b.Low))
    return {"H": H, "corr": corr_i is not None, "L": L} if corr_i is not None else {"H": H}


# ------------------------------------------------------------------ langkah tiap putaran
def _on(kind):
    """Saklar alert dari alerts.yml (bagian party:). Default menyala."""
    try:
        return bool((U.load_alert_cfg().get("party") or {}).get(kind, True))
    except Exception:  # noqa
        return True


def send(kind, text):
    if _on(kind):
        return U.tg_send(text)
    return False


def round_levels(lo, hi):
    """Harga bulat psikologis di antara lo..hi."""
    step_ = 10 if hi < 200 else 50 if hi < 1000 else 100 if hi < 5000 else 500
    k = int(lo // step_) + 1
    out = []
    while k * step_ <= hi:
        out.append(float(k * step_))
        k += 1
    return out


def resist_tp(price, sl, H, ara, daily=None):
    """TP di level resisten (perkiraan letak offer tebal):
    high lonjakan hari ini, high 20 hari terakhir, harga bulat, harga ARA.
    TP1 = resisten terdekat yang memberi minimal 0,8R; TP2 = resisten berikutnya (>= TP1 + 0,5R)."""
    R = price - sl
    lv = {float(H), float(ara)}
    if daily is not None and len(daily) > 2:
        d = daily.iloc[:-1].tail(20)          # tanpa hari ini
        for h in d["High"]:
            lv.add(float(h))
        # puncak lokal (high yang lebih tinggi dari tetangganya) lebih kuat
    lv.update(round_levels(price, ara))
    lv = sorted(x for x in lv if price + 0.8 * R <= x <= ara)
    tp1 = lv[0] if lv else min(price + R, ara)
    rest = [x for x in lv if x >= tp1 + 0.5 * R]
    tp2 = rest[0] if rest else min(max(price + 2 * R, tp1 + tick(tp1)), ara)
    # pasang sedikit di bawah level resisten (antre di depan offer tebal)
    tp1 = tp1 - tick(tp1) if tp1 - tick(tp1) > price else tp1
    tp2 = tp2 - tick(tp2) if tp2 - tick(tp2) > tp1 else tp2
    return int(round(tp1)), int(round(tp2))


def step(quotes, today, data_dt, party_rules, full_row, page_url="", daily_frame=None):
    """quotes: hasil fetch_quotes; full_row(sym) -> row lengkap (indikator) atau None."""
    st = load(today)
    items = st["items"]
    for c in [c for c, it in items.items() if it["stage"] == "WATCH" and "from_high_add" not in it]:
        items.pop(c)   # masuk sebelum aturan "dekat high" berlaku
    sent = 0
    data_time = data_dt.strftime("%H:%M")
    now_s = data_time  # jam data (Yahoo telat ±10 mnt), supaya selaras dengan candle

    # 1) saham baru lolos PARTY -> watchlist
    new = []
    for s, q in quotes.items():
        code = s[:-3]
        if code in items or code not in U.ISSI:
            continue
        p, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
        if not p or not prev or p < prev * 1.07 or p >= 400:
            continue
        r = full_row(s)
        if not r:
            continue
        hi = q.get("regularMarketDayHigh") or p
        from_high = (p / hi - 1) * 100 if hi else 0
        if from_high < NEAR_HIGH:      # sudah jauh dari high = lonjakan sudah lewat, jangan dimasukkan
            continue
        if all(r.get(f) is not None and U.OPS[op](r[f], v) for f, op, v in party_rules):
            items[code] = {"stage": "WATCH", "added": now_s, "added_at": data_dt.isoformat(),
                           "prev": float(prev), "price_add": float(p), "from_high_add": round(from_high, 1), "flags": []}
            new.append((code, r))
    if new:
        lines = [f"📌 <b>Masuk watchlist PARTY</b> — {len(new)} saham · data {data_time}"]
        for code, r in sorted(new, key=lambda x: -x[1]["ret1d"]):
            lines.append(f"• <b>{code}</b> {nf(r['close'])} ({pc(r['ret1d'])}) val {U.fmt_idr(r['value'])}")
        lines.append("Tunggu koreksi & pantulan, alert dikirim otomatis.")
        send("watchlist", "\n".join(lines))
        sent += 1

    # 2) cari pantulan untuk yang masih WATCH
    watch = [c for c, it in items.items() if it["stage"] == "WATCH"]
    bars = intraday(watch)
    for c in watch:
        it = items[c]
        b = bars.get(c)
        if b is None:
            continue
        q = quotes.get(c + ".JK", {})
        res = find_bounce(b, it["prev"], it["added_at"], q.get("regularMarketOpen"))
        if not res:
            continue
        it["H"] = res["H"]
        if res.get("out"):
            it["stage"] = "OUT"
            send("bandar_keluar",
                 f"⚠️ <b>BANDAR KELUAR? · {c}</b> · data {data_time}\n"
                 f"Candle {res['time_out']} tutup {nf(res['price'])}, di bawah tengah lonjakan {nf(res['mid'])} "
                 f"(open → high {nf(res['H'])}). Dikeluarkan dari watchlist.")
            sent += 1
            continue
        if "time" not in res:
            if res.get("corr") and "CORR" not in it["flags"]:
                it["flags"].append("CORR")
            continue
        price = float(q.get("regularMarketPrice") or res["close"])
        L = res["L"]
        sl = int(round(L - tick(L)))
        if price <= sl:
            it["stage"] = "DONE"
            continue
        lim = U.ara_limit(it["prev"])
        ara = U.ara_price(it["prev"])
        tp1, tp2 = resist_tp(price, sl, res["H"], ara, daily_frame(c + ".JK") if daily_frame else None)
        it.update(stage="BOUNCE", entry=price, sl=sl, tp1=tp1, tp2=tp2, L=L, bounce_at=now_s)
        send("memantul",
            f"🟢 <b>KOREKSI MEMANTUL · {c}</b> · data {data_time}\n"
            f"Harga {nf(price)} ({pc((price / it['prev'] - 1) * 100)}) · dasar {nf(L)} · koreksi {res['retr']:.0f}% lonjakan (candle 15m)\n"
            f"<b>SL {nf(sl)}</b> ({pc((sl / price - 1) * 100)}) · <b>TP1 {nf(tp1)}</b> ({pc((tp1 / price - 1) * 100)}) · "
            f"<b>TP2 {nf(tp2)}</b> ({pc((tp2 / price - 1) * 100)}) · TP di bawah resisten (perkiraan offer tebal)\n"
            f"High lonjakan {nf(res['H'])} · jarak ke ARA {pc((ara / price - 1) * 100)}\n"
            f"Kalau beli: <code>/buy {c} {nf(price).replace('.', '')} LOT {sl} {tp1} {tp2}</code>"
            + (f'\n<a href="{page_url}#chart={c}">Chart</a>' if page_url else ""))
        sent += 1
        log = load_log()
        log.append({"date": today, "code": c, "time": now_s, "entry": price, "sl": sl, "tp1": tp1, "tp2": tp2,
                    "H": res["H"], "prev": it["prev"], "sl_hit": 0, "tp1_hit": 0, "tp2_hit": 0, "brk": 0, "last": price})
        save_log(log)

    # 3) watchlist lanjutan: gagal / TP / tembus high
    log = load_log()
    for c, it in items.items():
        if it["stage"] != "BOUNCE":
            continue
        q = quotes.get(c + ".JK", {})
        p = q.get("regularMarketPrice")
        if not p:
            continue
        p = float(p)
        rec = next((r for r in reversed(log) if r["date"] == today and r["code"] == c), None)
        if rec:
            rec["last"] = p
        msg, kind = None, None
        if p <= it["sl"]:
            kind = "gagal"
            it["stage"] = "FAILED"
            msg = (f"🔴 <b>KOREKSI GAGAL · {c}</b> · data {data_time}\n"
                   f"Harga {nf(p)} tembus SL {nf(it['sl'])} (dasar {nf(it['L'])}). "
                   f"Dari harga sinyal {nf(it['entry'])}: {pc((p / it['entry'] - 1) * 100)}. Pertimbangkan cut.")
            if rec:
                rec["sl_hit"] = 1
        else:
            if p >= it["tp1"] and "TP1" not in it["flags"]:
                it["flags"].append("TP1")
                kind = "tp"
                msg = f"🎯 <b>{c} TP1 {nf(it['tp1'])} tercapai</b> · data {data_time}\nHarga {nf(p)}. Jual sebagian, SL naik ke harga sinyal {nf(it['entry'])}."
                it["sl"] = it["entry"]
                if rec:
                    rec["tp1_hit"] = 1
            if p >= it["tp2"] and "TP2" not in it["flags"]:
                it["flags"].append("TP2")
                kind = "tp"
                msg = f"🎯 <b>{c} TP2 {nf(it['tp2'])} tercapai</b> · data {data_time}\nHarga {nf(p)}. Amankan profit."
                if rec:
                    rec["tp2_hit"] = 1
            if p > it.get("H", 1e18) and "BRK" not in it["flags"]:
                it["flags"].append("BRK")
                lim = U.ara_limit(it["prev"])
                ara = it["prev"] * (1 + lim / 100)
                kind = "tembus_high"
                msg = (f"🚀 <b>{c} TEMBUS HIGH LONJAKAN {nf(it['H'])}</b> · data {data_time}\n"
                       f"Harga {nf(p)} · jarak ke ARA {pc((ara / p - 1) * 100)}. Bisa jadi titik tambah muatan — "
                       f"SL tambahan di bawah {nf(it['H'])}. Hati-hati, data telat ±10 menit.")
                if rec:
                    rec["brk"] = 1
        if msg:
            send(kind, msg)
            sent += 1
    save_log(log)
    save(st)
    return sent


# ------------------------------------------------------------------ perintah Telegram
LABEL = {"WATCH": "⏳ menunggu koreksi/pantulan", "BOUNCE": "🟢 sudah memantul", "FAILED": "🔴 gagal",
         "DONE": "⚪ selesai", "OUT": "⚠️ bandar keluar"}


def watch_text(today):
    st = load(today)
    if not st["items"]:
        return "Watchlist PARTY hari ini masih kosong."
    lines = [f"📋 <b>Watchlist PARTY</b> · {today}"]
    for c, it in st["items"].items():
        extra = ""
        if it["stage"] == "BOUNCE":
            extra = f" · SL {nf(it['sl'])} TP1 {nf(it['tp1'])} TP2 {nf(it['tp2'])}"
        elif "CORR" in it["flags"] and it["stage"] == "WATCH":
            extra = " · sedang koreksi"
        lines.append(f"• <b>{c}</b> masuk {it['added']} — {LABEL.get(it['stage'], it['stage'])}{extra}")
    return "\n".join(lines)


def eval_text():
    log = load_log()
    if not log:
        return "Belum ada sinyal pantulan yang tercatat."
    n = len(log)
    s = lambda k: sum(r[k] for r in log)
    avg_last = sum((r["last"] / r["entry"] - 1) * 100 for r in log) / n
    days = len({r["date"] for r in log})
    return (f"📊 <b>Evaluasi sinyal KOREKSI MEMANTUL</b> ({n} sinyal, {days} hari)\n"
            f"Kena SL: {s('sl_hit') / n * 100:.0f}% · TP1: {s('tp1_hit') / n * 100:.0f}% · "
            f"TP2: {s('tp2_hit') / n * 100:.0f}% · tembus high: {s('brk') / n * 100:.0f}%\n"
            f"Rata-rata harga terakhir vs harga sinyal: {pc(avg_last)} (belum fee ±0,4%)\n"
            f"Pembanding uji 59 hari (aturan 15m): SL 15–20%, TP1 20–32%, TP2 7–8%, tembus high 37–47%.")
