"""Monitor posisi saham lewat Telegram (diadaptasi dari bot lama, versi data gratis).

Perintah (hanya dari TELEGRAM_CHAT_ID):
  /buy KODE HARGA LOT [SL TP1 TP2]   catat beli (beli lagi = average otomatis)
  /sell KODE [LOT] [HARGA]           jual sebagian / semua
  /setpos KODE SL TP1 TP2            ubah SL/TP
  /posisi                            ringkasan semua posisi
  /help                              daftar perintah

Status dicek tiap putaran scan realtime, notifikasi hanya saat status berubah.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

import update as U

BUY_FEE = float(os.environ.get("BUY_FEE_RATE", "0.0015"))
SELL_FEE = float(os.environ.get("SELL_FEE_RATE", "0.0025"))
DEF_SL, DEF_TP1, DEF_TP2 = 0.975, 1.025, 1.045      # sama dengan bot lama
SOFT_SL_PCT, HARD_SL_PCT = 0.6, 1.6                  # smart SL: zona sapuan
NEAR_SL_PCT = 1.0

POS_PATH = U.STATE / "positions.json"
OFFSET_PATH = U.STATE / "tg_offset.json"

LAST_QUOTES = {}      # diisi realtime.py tiap putaran
LAST_DATA_TIME = ""


# ------------------------------------------------------------------ util
def tick_round(p):
    p = float(p)
    if p <= 0:
        return 0
    t = 1 if p < 200 else 2 if p < 500 else 5 if p < 2000 else 10 if p < 5000 else 25
    return int(round(p / t) * t)


def nf(x, d=0):
    """Format angka gaya Indonesia: 1.234,56"""
    s = f"{x:,.{d}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def pc(x):
    return f"{x:+.2f}%".replace(".", ",")


def rp(x):
    return "Rp" + nf(x)


def tick_floor(p):
    t = 1 if p < 200 else 2 if p < 500 else 5 if p < 2000 else 10 if p < 5000 else 25
    return int(p // t * t)


def load_pos():
    try:
        return json.loads(POS_PATH.read_text())
    except Exception:  # noqa
        return {}


def save_pos(p):
    U.STATE.mkdir(parents=True, exist_ok=True)
    POS_PATH.write_text(json.dumps(p, indent=1))


def now_s():
    return datetime.now(U.WIB).strftime("%Y-%m-%d %H:%M")


def metrics(entry, price, lots):
    sh = lots * 100
    buy = entry * sh
    mkt = price * sh
    fee = buy * BUY_FEE + mkt * SELL_FEE
    net = mkt - buy - fee
    return {"buy": buy, "mkt": mkt, "net": net, "net_pct": net / buy * 100 if buy else 0,
            "gross_pct": (price / entry - 1) * 100 if entry else 0}


def price_now(code):
    q = LAST_QUOTES.get(code + ".JK")
    if q and q.get("regularMarketPrice"):
        return float(q["regularMarketPrice"]), q
    try:
        import yfinance as yf
        p = float(yf.Ticker(code + ".JK").fast_info["last_price"])
        return p, None
    except Exception:  # noqa
        return None, None


# ------------------------------------------------------------------ telegram
def _tg(method, params, timeout=20):
    tok = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not tok:
        return None
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/{method}", data, timeout=timeout) as r:
        return json.loads(r.read())


def poll(timeout=0):
    """Ambil & proses perintah Telegram. timeout>0 = long-poll (sekalian jadi jeda)."""
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat:
        return
    try:
        off = json.loads(OFFSET_PATH.read_text()).get("offset", 0) if OFFSET_PATH.exists() else 0
        res = _tg("getUpdates", {"offset": off + 1, "timeout": int(timeout),
                                 "allowed_updates": '["message"]'}, timeout=timeout + 15) or {}
    except Exception as e:  # noqa
        print("poll gagal:", e)
        return
    for upd in res.get("result", []):
        off = max(off, upd["update_id"])
        U.STATE.mkdir(parents=True, exist_ok=True)
        OFFSET_PATH.write_text(json.dumps({"offset": off}))
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != chat:
            continue
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            try:
                reply = handle(text)
            except Exception as e:  # noqa
                reply = f"⚠️ Error: {e}"
            if reply:
                U.tg_send(reply)


# ------------------------------------------------------------------ perintah
HELP = (
    "<b>Casaham — monitor posisi</b>\n"
    "/buy KODE HARGA LOT [SL TP1 TP2]\n  contoh: <code>/buy INDF 6810 25</code>\n"
    "  (tanpa SL/TP → SL −2,5%, TP1 +2,5%, TP2 +4,5%; beli lagi = average)\n"
    "/sell KODE [LOT] [HARGA]\n  contoh: <code>/sell INDF 10 7125</code> · <code>/sell INDF</code> = jual semua\n"
    "/setpos KODE SL TP1 TP2\n/posisi — ringkasan\n"
    "/watch — watchlist PARTY hari ini & tahapnya\n/evaluasi — statistik jurnal sinyal (PARTY & BBS)\n"
    "/bid KODE LOT — catat antrian bid ARA saat tutup\n  contoh: <code>/bid TGUK 350000</code>\n\n"
    "Perintah dibaca selama jam bursa (±08:40–16:10 WIB). Di luar itu diproses saat sesi berikutnya mulai."
)


def handle(text):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/posisi":
        return summary_text()
    if cmd in ("/watch", "/watchlist"):
        import watch as W
        return W.watch_text(datetime.now(U.WIB).strftime("%Y-%m-%d"))
    if cmd == "/evaluasi":
        import jurnal as J
        return J.stats_text()
    if cmd == "/bid":
        return cmd_bid(parts)
    if cmd == "/buy":
        return cmd_buy(parts)
    if cmd == "/sell":
        return cmd_sell(parts)
    if cmd == "/setpos":
        return cmd_setpos(parts)
    return "Perintah tidak dikenal. Ketik /help"


def cmd_bid(parts):
    import jurnal as J
    if len(parts) < 3:
        return "Format: <code>/bid KODE LOT</code>\ncontoh: <code>/bid TGUK 350000</code> (antrian bid di harga ARA saat tutup, dalam lot)"
    code = parts[1].upper().replace(".JK", "")
    try:
        lot = int(float(parts[2].replace(".", "").replace(",", "").lower().replace("rb", "000").replace("k", "000")))
    except ValueError:
        return "Jumlah lot tidak terbaca. Contoh: <code>/bid TGUK 350000</code>"
    r = J.set_bid(code, lot, datetime.now(U.WIB).strftime("%Y-%m-%d"))
    if not r:
        return f"{code} tidak ada di jurnal BBS. /bid hanya untuk saham yang tercatat sinyal BBS."
    t = f"✅ Bid {code} ({r['date']}): {nf(lot)} lot"
    if r.get("bid_val"):
        t += f" ≈ Rp{nf(r['bid_val'] / 1e9, 2)} M"
    if r.get("bid_ratio") is not None:
        t += f" · {nf(r['bid_ratio'], 1)}% dari volume hari itu"
    lk = r.get("locked")
    t += "\nStatus tutup: " + ("terkunci ARA 🔒" if lk else "tidak terkunci ARA" if lk == 0 else "belum diisi (diisi akhir sesi)")
    t += "\nHasil open besok dicatat otomatis; rekap di /evaluasi."
    return t


def cmd_buy(parts):
    if len(parts) < 4:
        return "Format: <code>/buy KODE HARGA LOT [SL TP1 TP2]</code>\nContoh: <code>/buy INDF 6810 25</code>"
    code = parts[1].upper().replace(".JK", "")
    try:
        price, lots = float(parts[2]), int(float(parts[3]))
        sl = float(parts[4]) if len(parts) > 4 else None
        tp1 = float(parts[5]) if len(parts) > 5 else None
        tp2 = float(parts[6]) if len(parts) > 6 else None
    except ValueError:
        return "Harga/lot tidak valid. Contoh: <code>/buy INDF 6810 25</code>"
    if lots <= 0 or price <= 0:
        return "Harga dan lot harus lebih dari 0."
    pos = load_pos()
    old = pos.get(code)
    if old:
        tot = old["lots"] + lots
        entry = (old["entry"] * old["lots"] + price * lots) / tot
        p = old
        p.update(entry=round(entry, 2), lots=tot, buy_count=old.get("buy_count", 1) + 1)
        if sl: p["sl"] = tick_round(sl)
        if tp1: p["tp1"] = tick_round(tp1)
        if tp2: p["tp2"] = tick_round(tp2)
    else:
        p = {"entry": price, "lots": lots, "buy_count": 1, "created": now_s(),
             "sl": tick_round(sl or price * DEF_SL), "tp1": tick_round(tp1 or price * DEF_TP1),
             "tp2": tick_round(tp2 or price * DEF_TP2), "tp1_hit": False, "status": "", "history": []}
    p["history"] = (p.get("history") or [])[-30:] + [{"side": "BUY", "price": price, "lots": lots, "t": now_s()}]
    pos[code] = p
    save_pos(pos)
    now, _ = price_now(code)
    m = metrics(p["entry"], now or p["entry"], p["lots"])
    return (f"✅ {'Average diperbarui' if old else 'Posisi dicatat'}: <b>{code}</b>\n"
            f"Avg {nf(p['entry'], 2)} · {p['lots']} lot · modal {rp(m['buy'])}\n"
            f"SL {p['sl']} · TP1 {p['tp1']} · TP2 {p['tp2']}\n"
            f"Harga kini {nf(now) if now else '-'} · P/L net {pc(m['net_pct'])}\n"
            "Dipantau tiap ±1 menit, notif saat status berubah.")


def cmd_sell(parts):
    if len(parts) < 2:
        return "Format: <code>/sell KODE [LOT] [HARGA]</code>"
    code = parts[1].upper().replace(".JK", "")
    pos = load_pos()
    if code not in pos:
        return f"{code} tidak ada di posisi."
    p = pos[code]
    try:
        lots = int(float(parts[2])) if len(parts) > 2 else p["lots"]
        price = float(parts[3]) if len(parts) > 3 else (price_now(code)[0] or 0)
    except ValueError:
        return "Lot/harga tidak valid."
    if not price:
        return "Harga jual belum diketahui, tulis manual: <code>/sell KODE LOT HARGA</code>"
    lots = max(1, min(lots, p["lots"]))
    cost = p["entry"] * lots * 100
    val = price * lots * 100
    net = val - cost - cost * BUY_FEE - val * SELL_FEE
    p["lots"] -= lots
    p["history"] = (p.get("history") or [])[-30:] + [{"side": "SELL", "price": price, "lots": lots, "t": now_s()}]
    full = p["lots"] <= 0
    if full:
        pos.pop(code)
    save_pos(pos)
    return (f"✅ SELL {'FULL' if full else 'PARTIAL'} <b>{code}</b>: {lots} lot @ {nf(price, 0)}\n"
            f"P/L net: {pc(net / cost * 100)} · {rp(net)}\n"
            + ("Posisi ditutup." if full else f"Sisa {p['lots']} lot · avg {nf(p['entry'], 2)}"))


def cmd_setpos(parts):
    if len(parts) < 5:
        return "Format: <code>/setpos KODE SL TP1 TP2</code>"
    code = parts[1].upper().replace(".JK", "")
    pos = load_pos()
    if code not in pos:
        return f"{code} belum ada di posisi."
    try:
        pos[code].update(sl=tick_round(float(parts[2])), tp1=tick_round(float(parts[3])),
                         tp2=tick_round(float(parts[4])), status="")
    except ValueError:
        return "Angka tidak valid."
    save_pos(pos)
    p = pos[code]
    return f"✅ {code}: SL {p['sl']} · TP1 {p['tp1']} · TP2 {p['tp2']}"


# ------------------------------------------------------------------ evaluasi
def vwap_info(code):
    """VWAP hari ini & candle 5m terakhir dari Yahoo."""
    try:
        import yfinance as yf
        d = yf.Ticker(code + ".JK").history(period="1d", interval="5m")
        d = d[d["Volume"] > 0]
        if d.empty:
            return None
        tp = (d["High"] + d["Low"] + d["Close"]) / 3
        vwap = float((tp * d["Volume"]).sum() / d["Volume"].sum())
        last = d.iloc[-1]
        return {"vwap": vwap, "bull": bool(last["Close"] > last["Open"])}
    except Exception:  # noqa
        return None


def evaluate(code, p, q, frame=None):
    price = float(q["regularMarketPrice"])
    hi = q.get("regularMarketDayHigh") or price
    lo = q.get("regularMarketDayLow") or price
    vol = q.get("regularMarketVolume") or 0
    prev = q.get("regularMarketPreviousClose") or price
    chg = (price / prev - 1) * 100 if prev else 0
    v = vwap_info(code) or {}
    vwap = v.get("vwap")
    below_vwap = bool(vwap and price < vwap * 0.997)
    pos_in_range = (price - lo) / (hi - lo) if hi > lo else 0.5
    vol_ratio = None
    ret5 = None
    if frame is not None and len(frame) > 21:
        avgv = frame["Volume"].iloc[-21:-1].mean()
        vol_ratio = vol / avgv if avgv else None
        ret5 = (price / frame["Close"].iloc[-6] - 1) * 100
    sl, tp1, tp2 = p["sl"], p["tp1"], p["tp2"]
    notes = []

    if p.get("tp1_hit") and sl < p["entry"]:
        p["sl"] = sl = tick_floor(p["entry"])          # trailing ke impas setelah TP1
        notes.append("SL dinaikkan ke harga beli (impas) karena TP1 sudah tercapai")

    hard = sl * (1 - HARD_SL_PCT / 100)
    if price <= hard:
        st, act = "CUT", f"🔴 CUT — harga tembus hard SL ({nf(hard, 0)})"
    elif price < sl:
        if price >= sl * (1 - SOFT_SL_PCT / 100) and v.get("bull") and not below_vwap:
            st, act = "SWEEP", "🟠 SL tersentuh tipis, candle 5m hijau — potensi sapuan. Tunggu 1–2 candle; CUT kalau tidak balik ke atas SL"
        else:
            st, act = "CUT", "🔴 CUT — SL kena"
    elif tp2 and price >= tp2:
        st, act = "TP2", "🟢 TP2 tercapai — amankan profit (jual semua / sisakan sedikit)"
    elif tp1 and price >= tp1:
        p["tp1_hit"] = True
        st, act = "TP1", "🟢 TP1 tercapai — jual sebagian, SL naik ke harga beli"
    elif p.get("tp1_hit") and below_vwap:
        st, act = "FADE", "🟠 Sudah lewat TP1 tapi balik di bawah VWAP — profit menipis, pertimbangkan jual"
    elif price <= sl * (1 + NEAR_SL_PCT / 100) and below_vwap:
        st, act = "NEAR_SL", "🟠 Dekat SL & di bawah VWAP — siap cut, jangan tambah"
    elif below_vwap and pos_in_range < 0.3 and chg < 0 and (vol_ratio or 0) >= 1.5:
        st, act = "DIST", "🟠 Waspada distribusi — turun dekat low hari ini, volume besar, di bawah VWAP"
    else:
        st, act = "HOLD", "🟢 AMAN — HOLD sesuai rencana"

    if ret5 is not None and ret5 >= 8:
        notes.append(f"sudah naik {nf(ret5, 1)}% dalam 5 hari, rawan ambil untung")
    m = metrics(p["entry"], price, p["lots"])
    text = (f"<b>{code}</b> · {act}\n"
            f"Harga {nf(price, 0)} ({pc(chg)}) · Avg {nf(p['entry'], 2)} · {p['lots']} lot\n"
            f"P/L net {pc(m['net_pct'])} · {rp(m['net'])}\n"
            f"SL {p['sl']} · TP1 {p['tp1']} · TP2 {p['tp2']}"
            + (f" · VWAP {nf(vwap, 0)}" if vwap else "")
            + ("\n• " + "\n• ".join(notes) if notes else ""))
    return st, text, m


def monitor(quotes, frames, data_time, force_summary=None):
    """Panggil tiap putaran. Kirim notif bila status berubah. Return jumlah notif."""
    global LAST_QUOTES, LAST_DATA_TIME
    LAST_QUOTES, LAST_DATA_TIME = quotes, data_time
    pos = load_pos()
    if not pos:
        return 0
    sent = 0
    blocks = []
    for code, p in pos.items():
        q = quotes.get(code + ".JK")
        if not q or not q.get("regularMarketPrice"):
            continue
        st, text, _ = evaluate(code, p, q, frames.get(code + ".JK"))
        if st != p.get("status"):
            if p.get("status") or st != "HOLD":   # HOLD pertama tidak perlu notif
                U.tg_send(f"🔔 Status posisi berubah · data {data_time}\n{text}")
                sent += 1
            p["status"] = st
        blocks.append(text)
    save_pos(pos)
    if force_summary and blocks:
        U.tg_send(f"📋 <b>{force_summary}</b> · data {data_time}\n\n" + "\n\n".join(blocks))
    return sent


def summary_text():
    pos = load_pos()
    if not pos:
        return "Belum ada posisi. Pakai <code>/buy KODE HARGA LOT</code>"
    blocks, tot_buy, tot_net = [], 0, 0
    for code, p in pos.items():
        price, q = price_now(code)
        if q:
            _, text, m = evaluate(code, dict(p), q)
        else:
            m = metrics(p["entry"], price or p["entry"], p["lots"])
            text = (f"<b>{code}</b> · harga {nf(price) if price else '-'} · Avg {nf(p['entry'], 2)} · {p['lots']} lot\n"
                    f"P/L net {pc(m['net_pct'])} · SL {p['sl']} · TP1 {p['tp1']} · TP2 {p['tp2']}")
        tot_buy += m["buy"]
        tot_net += m["net"]
        blocks.append(text)
    head = (f"📋 <b>Posisi</b> · {now_s()}" + (f" · data {LAST_DATA_TIME}" if LAST_DATA_TIME else "") +
            f"\nModal {rp(tot_buy)} · P/L net {pc(tot_net / tot_buy * 100 if tot_buy else 0)} ({rp(tot_net)})")
    return head + "\n\n" + "\n\n".join(blocks)
