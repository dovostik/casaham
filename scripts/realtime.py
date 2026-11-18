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

import update as U

INTERVAL = int(os.environ.get("INTERVAL", "60"))
UNTIL = os.environ.get("UNTIL", "16:15")

# field yang bisa dihitung langsung dari quote (tanpa riwayat)
QUICK = {"ret1d", "close", "volume", "value", "mcap", "per", "pbv", "araPct"}


def quick_row(q):
    price, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
    if not price or not prev:
        return None
    ret1d = (price / prev - 1) * 100
    lim = U.ara_limit(prev)
    vol = q.get("regularMarketVolume") or 0
    return {"ret1d": ret1d, "close": price, "volume": vol, "value": price * vol,
            "mcap": q.get("marketCap"), "per": q.get("trailingPE"), "pbv": q.get("priceToBook"),
            "araPct": ret1d / lim * 100}


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

    quotes = U.fetch_quotes()
    print("Unduh riwayat", len(quotes), "saham…")
    frames = U.fetch_history(sorted(quotes))
    print("riwayat siap:", len(frames))

    sent = U.load_sent(today)
    STATE_LOG = U.STATE / "alerts_log.json"
    last_mkt_time, rounds = None, 0

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
                if qr and any(passes_quick(qr, p["rules"]) for p in scr):
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
            rounds += 1
            print(f"{datetime.now(U.WIB):%H:%M:%S} data {mkt_dt:%H:%M} · kandidat {len(cand)} · "
                  f"alert {len(log)} · {time.time() - t0:.1f}s")
        time.sleep(max(5, INTERVAL - (time.time() - t0)))
    print("Sesi selesai,", rounds, "putaran scan")


if __name__ == "__main__":
    main()
