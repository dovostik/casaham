"""Alert ARA terkunci (titik masuk ala trader BBS) + pengingat keluar pagi.

Uji 59 hari: entry tepat di harga ARA rata-rata +7,3% (menang 77%, 88% terkunci saat tutup),
entry di bawah ARA (progres 70–99%) rata-rata −0,2%.
Pola trader yang untung: beli di harga ARA, jual besok 08:58–09:05.
"""
import json
from datetime import datetime

import update as U
import jurnal as J
import watch as W

PATH = U.STATE / "ara_lock.json"
CONFIRM = 3            # berapa putaran data (±1 menit) harga menempel di ARA sebelum alert
MAX_MCAP = 6e12
MIN_VALUE = 1e9
LAST_ALERT = "15:45"


def _load():
    try:
        return json.loads(PATH.read_text())
    except Exception:  # noqa
        return {}


def _save(st):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(st, ensure_ascii=False))


def _eligible(code, q):
    px, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
    if not px or not prev or prev < 50 or code not in U.ISSI:
        return None
    if (q.get("marketCap") or 0) > MAX_MCAP:
        return None
    if px * (q.get("regularMarketVolume") or 0) < MIN_VALUE:
        return None
    return float(px), float(prev), U.ara_price(prev)


def scan(quotes, today, mkt_dt, page_url=""):
    """Dipanggil tiap ada data baru. Kirim alert saat saham syariah menempel di ARA ≥ CONFIRM putaran,
    dan peringatan bila kunci ARA terbuka setelah dialertkan."""
    st = _load()
    if st.get("date") != today:
        st = {"date": today, "streak": {}, "alerted": {}, "broke": [],
              "morning": st.get("morning"), "locked_close": st.get("locked_close")}
    hm = f"{mkt_dt:%H:%M}"
    new = []
    for s, q in quotes.items():
        code = s.replace(".JK", "")
        t = q.get("regularMarketTime")
        if not t or datetime.fromtimestamp(t, U.WIB).strftime("%Y-%m-%d") != today:
            continue
        e = _eligible(code, q)
        if not e:
            st["streak"].pop(code, None)
            continue
        px, prev, ara = e
        if px >= ara:
            st["streak"][code] = st["streak"].get(code, 0) + 1
            if st["streak"][code] >= CONFIRM and code not in st["alerted"] and hm <= LAST_ALERT:
                new.append((code, px, prev, ara, q))
        else:
            st["streak"].pop(code, None)
            a = st["alerted"].get(code)
            if a and code not in st["broke"]:
                st["broke"].append(code)
                U.tg_send(f"⚠️ <b>{code}</b> kunci ARA terbuka: {W.nf(px)} (ARA {W.nf(ara)}) · data {hm}\n"
                          f"Kalau sudah dapat barang di ARA: waspada, ARA tidak valid bila tidak terkunci lagi "
                          f"sampai tutup → jual hari ini.")
    if new:
        bars = {}
        try:
            bars = W.intraday([c for c, *_ in new])
        except Exception as ex:  # noqa
            print("intraday ARA gagal:", ex)
        for code, px, prev, ara, q in new:
            g = vwap_line(bars.get(code), ara)
            val = px * (q.get("regularMarketVolume") or 0) / 1e9
            txt = (f"🔒 <b>TERKUNCI ARA: {code}</b> {W.nf(ara)} ({W.pc((ara / prev - 1) * 100)}) · "
                   f"value {W.nf(val, 1)} M · data {hm}\n")
            if g:
                txt += g + "\n"
            txt += ("<b>Masuk</b>: antre beli di harga ARA (lot kecil, belum tentu terisi).\n"
                    "<b>Valid</b> bila tetap terkunci sampai tutup tanpa antrean jual; bid penutupan tebal lebih baik.\n"
                    "<b>Keluar</b>: besok pre-opening 08:45–08:58 (cek IEP) atau 09:00–09:05. "
                    "Kunci terbuka & tidak kembali → jual hari ini.\n"
                    "Uji 59 hari entry di ARA: rata-rata +7,3%, menang 77%. Data tertunda ±10 mnt.")
            if page_url:
                txt += f"\n{page_url}#chart={code}"
            if U.tg_send(txt):
                st["alerted"][code] = hm
                try:
                    J.add("ARA", code, today, hm, float(ara), float(prev), value=round(val * 1e9))
                except Exception as ex:  # noqa
                    print("jurnal ARA gagal:", ex)
    _save(st)
    return len(new)


def vwap_line(b, ara):
    """VWAP hari ini vs ARA (KUAT ≤2%, SEDANG 2–5%, LEMAH >5%)."""
    if b is None or not len(b) or float(b["Volume"].sum()) <= 0:
        return None
    tp = (b["High"] + b["Low"] + b["Close"]) / 3
    vwap = float((tp * b["Volume"]).sum() / b["Volume"].sum())
    gap = (ara / vwap - 1) * 100
    grade = ("💪 KUAT" if gap <= 2 else "👍 SEDANG" if gap <= 5 else "⚠️ LEMAH")
    return f"VWAP {W.nf(vwap)} ({W.nf(gap, 1)}% di bawah ARA) → {grade}"


def intraday_day(codes, day):
    """Candle 1 menit untuk tanggal tertentu (aman dipakai sesudah tutup / akhir pekan)."""
    import yfinance as yf
    if not codes:
        return {}
    syms = [c + ".JK" for c in codes]
    df = yf.download(syms, period="5d", interval="1m", group_by="ticker", progress=False, threads=True)
    out = {}
    for c, s in zip(codes, syms):
        try:
            d = (df[s] if len(syms) > 1 else df).dropna(subset=["Close"])
            d = d[d["Volume"] > 0]
            d.index = d.index.tz_convert("Asia/Jakarta")
            d = d[d.index.strftime("%Y-%m-%d") == day]
            if len(d):
                out[c] = d
        except Exception:  # noqa
            pass
    return out


def ciri(b, ara):
    """Ciri hari ARA dari candle 1 menit: selisih VWAP, jam pertama sentuh ARA, porsi volume di ARA."""
    if b is None or not len(b) or float(b["Volume"].sum()) <= 0:
        return {}
    tp = (b["High"] + b["Low"] + b["Close"]) / 3
    vw = float((tp * b["Volume"]).sum() / b["Volume"].sum())
    at = b[b["High"] >= ara]
    return {"vwap": round(vw, 2), "vwap_gap": round((ara / vw - 1) * 100, 2),
            "t_ara": at.index[0].strftime("%H:%M") if len(at) else None,
            "vol_ara_pct": round(float(b.loc[b["Close"] >= ara, "Volume"].sum() / b["Volume"].sum() * 100), 1),
            "broke": int(len(at) > 0 and bool((b.loc[at.index[0]:, "Close"] < ara).any()))}


def verdict(c):
    """Kaidah uji 1 bulan (76 ARA, semua saham): kunci tidak pernah jebol sejak pertama sentuh ARA
    + ≥30% volume di harga ARA → IEP besok naik 94%, rata-rata +14%, tidak ada yang minus (16 kasus).
    Kunci jebol + volume di ARA <30% → naik 61%, rata-rata +4,5%, 23% minus."""
    if not c or c.get("broke") is None:
        return None
    v = c.get("vol_ara_pct") or 0
    if not c["broke"] and v >= 30:
        return "✅ VALID KUAT (kunci tak pernah jebol, volume di ARA ≥ 30%)"
    if not c["broke"]:
        return "🟡 cukup (kunci tak jebol, tapi volume di ARA < 30%)"
    if v >= 30:
        return "🟡 cukup (kunci sempat jebol, volume di ARA ≥ 30%)"
    return "⚠️ LEMAH (kunci sempat jebol & volume di ARA < 30%)"


def record_all(qs, today):
    """Catat SEMUA saham yang terkunci ARA saat tutup (syariah maupun tidak, harga berapa pun)
    ke jurnal sebagai ARA_TUTUP, untuk menguji ciri ARA valid (bid, VWAP, jam kunci) vs IEP besok."""
    items = []
    for s, q in qs.items():
        code = s.replace(".JK", "")
        t = q.get("regularMarketTime")
        px, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
        if not t or not px or not prev or datetime.fromtimestamp(t, U.WIB).strftime("%Y-%m-%d") != today:
            continue
        ara = U.ara_price(prev)
        if px >= ara and (q.get("regularMarketVolume") or 0) > 0:
            items.append((code, float(px), float(prev), ara, q))
    if not items:
        return 0
    try:
        bars = intraday_day([c for c, *_ in items], today)
    except Exception as ex:  # noqa
        print("intraday ARA tutup gagal:", ex)
        bars = {}
    for code, px, prev, ara, q in items:
        c = ciri(bars.get(code), ara)
        J.add("ARA_TUTUP", code, today, c.get("t_ara") or "15:59", float(ara), prev,
              value=round(px * (q.get("regularMarketVolume") or 0)),
              extra={"syariah": int(code in U.ISSI), "mcap": q.get("marketCap"), **c})
    return len(items)


def close_recap(qs, today):
    """Akhir sesi: simpan semua saham syariah yang terkunci ARA (untuk pengingat pagi besok),
    kembalikan baris teks untuk rekap penutupan."""
    try:
        print("ARA tutup dicatat:", record_all(qs, today))
    except Exception as ex:  # noqa
        print("catat ARA tutup gagal:", ex)
    st = _load()
    items = []
    for s, q in qs.items():
        code = s.replace(".JK", "")
        t = q.get("regularMarketTime")
        if not t or datetime.fromtimestamp(t, U.WIB).strftime("%Y-%m-%d") != today:
            continue
        e = _eligible(code, q)
        if e and e[0] >= e[2]:
            items.append({"code": code, "ara": e[2], "prev": e[1]})
    if items:
        try:
            bars = intraday_day([i["code"] for i in items], today)
        except Exception:  # noqa
            bars = {}
        for i in items:
            c = ciri(bars.get(i["code"]), i["ara"])
            v = verdict(c)
            i["grade"] = " · ".join(x for x in [vwap_line(bars.get(i["code"]), i["ara"]), v] if x)
    st["locked_close"] = {"date": today, "items": items}
    _save(st)
    if not items:
        return []
    lines = [f"\n🔒 <b>Syariah terkunci ARA saat tutup</b> ({len(items)})"]
    for i in items:
        lines.append(f"• <b>{i['code']}</b> {W.nf(i['ara'])}" + (f" · {i['grade']}" if i.get("grade") else ""))
    lines.append("Cek antrean jual (harus kosong) & tebal bid penutupan di Stockbit · catat dengan /bid KODE LOT.")
    return lines


def morning(today):
    """Pengingat keluar pagi untuk saham yang terkunci ARA di hari bursa sebelumnya (sekali per hari)."""
    st = _load()
    lc = st.get("locked_close") or {}
    if st.get("morning") == today or not lc.get("items") or lc.get("date", "") >= today:
        return False
    held = {r["code"] for r in J.load() if r["date"] == lc["date"] and r["setup"] in ("BBS", "ARA")}
    lines = [f"🌅 <b>Rencana keluar ARA kemarin</b> ({lc['date']})",
             "Jual di pre-opening 08:45–08:58 atau 09:00–09:05 — high besok biasanya di 15 menit pertama."]
    for i in lc["items"]:
        mark = " ⭐ sinyal Casaham" if i["code"] in held else ""
        lines.append(f"• <b>{i['code']}</b> ARA {W.nf(i['ara'])}{mark}" + (f"\n   {i['grade']}" if i.get("grade") else ""))
    lines.append("Cek IEP: ≥ 70% jarak ARA berikutnya = berpotensi ARA lagi · IEP di bawah harga kemarin = jual di open.")
    ok = U.tg_send("\n".join(lines))
    if ok:
        st["morning"] = today
        _save(st)
    return ok
