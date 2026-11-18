"""Scan screener & alert harga tiap ±1 menit selama satu sesi bursa.

Riwayat harian diunduh sekali di awal; tiap putaran hanya mengambil quote
terbaru semua saham (4 request), menimpa bar hari ini, lalu menghitung
indikator hanya untuk saham kandidat. Data Yahoo untuk BEI tertunda ±10 menit.

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, PAGE_URL, UNTIL (HH:MM WIB, default 16:15),
     INTERVAL (detik, default 60)
"""
import json
import os
import time
from datetime import datetime

import pandas as pd

import positions as P
import watch as W
import update as U

INTERVAL = int(os.environ.get("INTERVAL", "60"))
UNTIL = os.environ.get("UNTIL", "16:15")

# field yang bisa dihitung langsung dari quote (tanpa riwayat)
QUICK = {"ret1d", "close", "volume", "value", "mcap", "per", "pbv", "araPct", "issi", "fromHighD", "highRet1d", "nearHighBBS"}


def quick_row(q):
    price, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
    if not price or not prev:
        return None
    ret1d = (price / prev - 1) * 100
    lim = U.ara_limit(prev)
    vol = q.get("regularMarketVolume") or 0
    hi = q.get("regularMarketDayHigh") or price
    return {"ret1d": ret1d, "close": price, "volume": vol, "value": price * vol,
            "mcap": q.get("marketCap"), "per": q.get("trailingPE"), "pbv": q.get("priceToBook"),
            "araPct": ret1d / lim * 100, "issi": int(q["symbol"][:-3] in U.ISSI),
            "fromHighD": (price / hi - 1) * 100, "highRet1d": (hi / prev - 1) * 100,
            "nearHighBBS": U.near_high_bbs(price, hi)}


def passes_quick(qr, rules):
    for f, op, val in rules:
        if f in QUICK:
            x = qr.get(f)
            if x is None or not U.OPS[op](x, val):
                return False
    return True


def with_today(d, q, today):
    """Riwayat + bar hari ini dari quote."""
    bar = pd.DataFrame([{
        "Open": q.get("regularMarketOpen") or q["regularMarketPrice"],
        "High": q.get("regularMarketDayHigh") or q["regularMarketPrice"],
        "Low": q.get("regularMarketDayLow") or q["regularMarketPrice"],
        "Close": q["regularMarketPrice"],
        "Volume": q.get("regularMarketVolume") or 0,
    }], index=[pd.Timestamp(today)])
    d = d[d.index.normalize() < pd.Timestamp(today)]
    return pd.concat([d, bar])


def trigger_web_update():
    """Picu workflow 'Update data web' (jadwal GitHub tidak bisa diandalkan)."""
    import urllib.request
    tok, repo = os.environ.get("GH_TOKEN"), os.environ.get("GH_REPO")
    if not tok or not repo:
        return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/update.yml/dispatches",
        data=b'{"ref":"main"}', method="POST",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
        print("update web dipicu")
    except Exception as e:  # noqa
        print("gagal memicu update web:", e)


def main():
    now = datetime.now(U.WIB)
    today = os.environ.get("TEST_TODAY") or now.strftime("%Y-%m-%d")  # TEST_TODAY: uji coba
    max_rounds = int(os.environ.get("MAX_ROUNDS", "0"))
    end = datetime.strptime(f"{today} {UNTIL}", "%Y-%m-%d %H:%M").replace(tzinfo=U.WIB)
    page_url = os.environ.get("PAGE_URL", "")
    presets = {p["id"]: p for p in json.loads((U.DOCS / "screeners.json").read_text())["presets"]}
    cfg = U.load_alert_cfg()
    watch_codes = {str(i.get("kode", "")).upper() for i in cfg.get("saham") or []}
    scr = [presets[p] for p in cfg.get("screeners") or [] if p in presets]
    print(f"Screener aktif: {[p['id'] for p in scr]}, alert saham: {sorted(watch_codes)}")

    P.poll(0)  # proses perintah Telegram yang masuk di luar jam bursa
    quotes = None
    for attempt in range(20):           # Yahoo kadang menolak sementara (429/401): coba ulang ±10 menit
        try:
            quotes = U.fetch_quotes()
            break
        except Exception as e:  # noqa
            print("daftar saham gagal, coba lagi:", e)
            P.poll(timeout=30)
    if not quotes:
        raise SystemExit("Yahoo tidak bisa diakses")
    print("Unduh riwayat", len(quotes), "saham…")
    frames = U.fetch_history(sorted(quotes))
    print("riwayat siap:", len(frames))

    sent = U.load_sent(today)
    SUM_PATH = U.STATE / "summary_sent.json"
    sum_sent = json.loads(SUM_PATH.read_text()) if SUM_PATH.exists() else {}
    STATE_LOG = U.STATE / "alerts_log.json"
    last_mkt_time, rounds = None, 0
    last_web = 0.0

    while datetime.now(U.WIB) < end or (max_rounds and rounds < max_rounds):
        t0 = time.time()
        try:
            quotes = U.fetch_quotes()
        except Exception as e:  # noqa
            print("quote gagal:", e)
            time.sleep(INTERVAL)
            continue
        mkt_time = max((q.get("regularMarketTime") or 0) for q in quotes.values())
        mkt_dt = datetime.fromtimestamp(mkt_time, U.WIB)
        if mkt_dt.strftime("%Y-%m-%d") != today:
            print(datetime.now(U.WIB).strftime("%H:%M"), "pasar belum buka")
            rounds += max_rounds > 0
        elif mkt_time == last_mkt_time:
            rounds += max_rounds > 0  # tidak ada data baru (jeda sesi)
        else:
            last_mkt_time = mkt_time
            # pilih kandidat: lolos filter cepat salah satu screener, atau ada di alert saham
            cand = set()
            for s, q in quotes.items():
                code = s.replace(".JK", "")
                q_ok = q.get("regularMarketTime") and datetime.fromtimestamp(
                    q["regularMarketTime"], U.WIB).strftime("%Y-%m-%d") == today
                if not q_ok or s not in frames:
                    continue
                if code in watch_codes:
                    cand.add(s)
                    continue
                qr = quick_row(q)
                if qr and any(U.in_window(p) and passes_quick(qr, p["rules"]) for p in scr):
                    cand.add(s)
            rows = []
            for s in cand:
                try:
                    rows.append(U.build_row(s, with_today(frames[s], quotes[s], today), quotes[s]))
                except Exception as e:  # noqa
                    print("skip", s, e)
            stamp = f" · data {mkt_dt:%H:%M}"
            log = U.do_alerts(rows, presets, today, page_url, sent, cfg, stamp)
            U.save_sent(sent)
            if log:
                old = json.loads(STATE_LOG.read_text()) if STATE_LOG.exists() else []
                old.insert(0, {"time": datetime.now(U.WIB).strftime("%Y-%m-%d %H:%M WIB"), "items": log})
                STATE_LOG.write_text(json.dumps(old[:50], ensure_ascii=False))
            # monitor posisi + ringkasan buka/tutup
            slot = None
            if mkt_dt.hour < 12 and sum_sent.get("open") != today:
                slot, sum_sent["open"] = "Ringkasan posisi — pembukaan", today
            elif (mkt_dt.hour, mkt_dt.minute) >= (15, 50) and sum_sent.get("close") != today:
                slot, sum_sent["close"] = "Ringkasan posisi — penutupan", today
            n_pos = P.monitor(quotes, frames, f"{mkt_dt:%H:%M}", force_summary=slot)

            def full_row(sym):
                if sym not in frames:
                    return None
                try:
                    return U.build_row(sym, with_today(frames[sym], quotes[sym], today), quotes[sym])
                except Exception:  # noqa
                    return None
            try:
                n_pos += W.step(quotes, today, mkt_dt, presets["party"]["rules"], full_row, page_url,
                                daily_frame=lambda sym: frames.get(sym))
            except Exception as e:  # noqa
                print("watch error:", e)
            SUM_PATH.write_text(json.dumps(sum_sent))
            rounds += 1
            print(f"{datetime.now(U.WIB):%H:%M:%S} data {mkt_dt:%H:%M} · kandidat {len(cand)} · "
                  f"alert {len(log)} · notif posisi {n_pos} · {time.time() - t0:.1f}s")
        if last_mkt_time and time.time() - last_web >= 1800 and not max_rounds:
            trigger_web_update()
            last_web = time.time()
        # jeda sambil mendengarkan perintah Telegram (long-poll)
        while True:
            left = INTERVAL - (time.time() - t0)
            if left < 2 or max_rounds:
                time.sleep(max(0, left)) if max_rounds else None
                break
            P.poll(timeout=min(left - 1, 50))
    if UNTIL >= "15:50" and sum_sent.get("close") != today and P.load_pos():
        P.U.tg_send(P.summary_text().replace("📋 <b>Posisi</b>", "📋 <b>Ringkasan posisi — penutupan</b>"))
        sum_sent["close"] = today
        SUM_PATH.write_text(json.dumps(sum_sent))
    # rekap penutupan BBS: status ARA saat tutup untuk saham yang dialertkan hari ini
    bbs_codes = [k.split(":")[2] for k, v in sent.items() if k.startswith("scr:bbs:") and v == today]
    if UNTIL >= "15:50" and bbs_codes and sum_sent.get("bbs_close") != today:
        try:
            qs = U.fetch_quotes()
            lines = [f"📋 <b>Rekap penutupan BBS</b> · {today}"]
            for c in bbs_codes:
                q = qs.get(c + ".JK") or {}
                px, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
                if not px or not prev:
                    continue
                ara = U.ara_price(prev)
                if px >= ara:
                    lines.append(f"• <b>{c}</b> {px:,.0f} = ARA ✅ terkunci saat tutup → cek antrean jual (harus kosong) & IEP besok")
                else:
                    lines.append(f"• <b>{c}</b> {px:,.0f} (ARA {ara:,.0f}, {((px / prev) - 1) * 100:+.1f}%) ❌ tidak terkunci → tidak valid ditahan")
            lines.append("Antrean jual di buku penutupan tidak terlihat di data gratis — pastikan di Stockbit.")
            U.tg_send("\n".join(lines))
            sum_sent["bbs_close"] = today
            SUM_PATH.write_text(json.dumps(sum_sent))
        except Exception as e:  # noqa
            print("rekap BBS gagal:", e)
    if not max_rounds:
        trigger_web_update()   # data web final sesudah sesi
    print("Sesi selesai,", rounds, "putaran scan")


if __name__ == "__main__":
    main()
