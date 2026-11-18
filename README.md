# Casaham — screener, chart & alert saham BEI (gratis)

Aplikasi web pribadi ala Stockbit: **screener**, **chart teknikal** (candlestick, MA20/50, volume, RSI),
**watchlist**, dan **alert Telegram**. Jalan 100% gratis: data diolah GitHub Actions, web di-host Cloudflare Pages (casaham.pages.dev).

- Data: Yahoo Finance, ±835 saham BEI, tertunda ±10–15 menit.
- **Alert realtime**: screener & alert harga dicek **tiap ±1 menit** sepanjang sesi bursa
  (workflow *Screener realtime*). Data Yahoo untuk BEI tertunda ±10 menit; jam data tertera di tiap notifikasi.
- Data web (chart, tabel) diperbarui tiap ±30 menit (workflow *Update data web*).
  Jadwal GitHub kadang molor 5–20 menit.
- Repo harus **public** agar menit GitHub Actions tidak terbatas.
- Bisa dipasang di layar utama HP (buka di Chrome → ⋮ → *Tambahkan ke layar utama*).

## Pasang (sekali saja)

1. **Repo GitHub**: buat repo baru `casaham` (public atau private), isi dengan semua file ini.
2. **Cloudflare** (akun gratis di dash.cloudflare.com):
   - *Account ID*: ada di halaman Workers & Pages (kolom kanan) → salin.
   - *API token*: My Profile → API Tokens → Create Token → Custom token →
     izin **Account · Cloudflare Pages · Edit** → salin token.
3. **Telegram** (untuk alert):
   - Chat ke **@BotFather** → `/newbot` → salin *token*.
   - Kirim pesan ke bot baru Anda, lalu buka
     `https://api.telegram.org/bot<TOKEN>/getUpdates` → salin angka `"chat":{"id": ...}`.
4. **Secrets** di repo → Settings → Secrets and variables → Actions → *New repository secret*:
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
5. **Jalankan pertama**: tab Actions → *Update data web* → **Run workflow**.
   Setelah ±5 menit aplikasi ada di **https://casaham.pages.dev**
   (kalau nama itu sudah dipakai orang, Cloudflare memberi alamat lain — cek di dashboard,
   lalu ganti `PAGE_URL` di `.github/workflows/update.yml`).

Kuota gratis: Cloudflare 500 deploy/bulan (pemakaian ±375), GitHub Actions repo private 2.000 menit/bulan
(pemakaian ±1.500; repo public tidak dibatasi).

## Monitor posisi (Telegram)
Kirim ke bot Telegram Anda:
| Perintah | Fungsi |
|---|---|
| `/buy KODE HARGA LOT [SL TP1 TP2]` | catat beli; tanpa SL/TP → SL −2,5%, TP1 +2,5%, TP2 +4,5%; beli lagi = average |
| `/sell KODE [LOT] [HARGA]` | jual sebagian/semua, hitung P/L net |
| `/setpos KODE SL TP1 TP2` | ubah SL/TP |
| `/posisi` | ringkasan semua posisi |

Posisi dicek tiap ±1 menit; notifikasi hanya saat status berubah
(AMAN/HOLD → TP1 → TP2, dekat SL, SL kena/CUT, sapuan SL, waspada distribusi).
Setelah TP1 tercapai SL otomatis naik ke harga beli. Ringkasan dikirim saat buka & tutup bursa.
Perintah dibaca selama jam bursa; di luar itu diproses saat sesi berikutnya mulai.
Data posisi disimpan di cache GitHub Actions (tidak ikut terlihat di repo publik).

## Mengatur
| Yang diubah | File |
|---|---|
| Alert harga/RSI/% per saham, screener yang dikirim ke Telegram | `alerts.yml` |
| Preset screener (dipakai web & alert) | `docs/screeners.json` |
| Screener buatan sendiri dari aplikasi | tombol *Simpan sebagai…* (tersimpan di HP) |

## Keterbatasan
- **Frequency** dan **Frequency Analyzer** (data Stockbit) tidak ada di data gratis,
  jadi preset "BBS" memakai aturan lainnya saja.
- Bukan real-time dan tidak bisa transaksi jual-beli.
- Data fundamental (PER, PBV, ROE) dari Yahoo, sebaiknya dicek ulang.
