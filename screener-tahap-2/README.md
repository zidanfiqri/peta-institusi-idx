# Peta Kepemilikan Institusi IDX

Dashboard statis komposisi kepemilikan saham Bursa Efek Indonesia per tipe investor, dari data resmi KSEI dan BEI.

## Halaman

| File | Isi |
|---|---|
| `index.html` | Peta Kepemilikan Institusi: komposisi 18 tipe investor lokal dan asing per emiten, radar net flow asing |
| `smart_money_tracker.html` | Smart Money Tracker: inflow asing, pola distribusi, watchlist |
| `screener.html` | Screener saham non HSC berdasarkan porsi asing: filter market cap, free float, sektor; breakdown tipe investor asing; daftar HSC; ekspor Excel |

## Sumber data

| Data | Sumber | Frekuensi | File di repo |
|---|---|---|---|
| Balance position lokal dan asing, 9 tipe | KSEI, Publikasi > Data dan Statistik > Kepemilikan Efek | Bulanan | `data.json`, `emiten_info.json` |
| Pemegang saham di atas 1% | BEI, Perusahaan Tercatat > Data Kepemilikan Saham | Bulanan | `data_src/1pct/` |
| Kepemilikan per 39 klasifikasi investor | BEI, Perusahaan Tercatat > Data Kepemilikan Saham | Bulanan | `data_src/klasifikasi/` |
| Daftar HSC (High Shareholding Concentration) | BEI, Perusahaan Tercatat > Kepemilikan Saham Terkonsentrasi Tinggi | Setiap ada pengumuman | `data_src/bei_hsc_pengumuman.csv`, `hsc.json` |

## Update data

Semua sumber diperbarui oleh satu skrip, `update_data.py`. Skrip hanya mengambil yang baru, lalu commit dan push sendiri.

**Persiapan (sekali saja):**

1. Pasang Git for Windows atau GitHub Desktop, login ke GitHub, lalu clone repo ini ke komputer.
2. Pastikan Google Chrome terpasang (dipakai untuk membuka situs BEI yang dilindungi Cloudflare).
3. Di folder repo, jalankan: `py -m pip install -r requirements.txt`
4. Klik dua kali `jalankan_update.bat` untuk uji coba pertama.
5. Klik dua kali `pasang_jadwal.bat` supaya update jalan otomatis setiap hari jam 08.00. Kalau komputer mati atau tidur pada jam itu, update dijalankan begitu komputer menyala lagi.

**Menjalankan manual:** klik dua kali `jalankan_update.bat`, atau dari terminal:

```
py update_data.py              # semua sumber, lalu commit dan push
py update_data.py --no-push    # tanpa git
py update_data.py --only hsc   # hanya HSC (pilihan: ksei, idx, hsc)
py update_data.py --show       # tampilkan jendela Chrome kalau mode tersembunyi ditolak BEI
```

Riwayat setiap update tersimpan di `update_log.txt` (hanya disimpan di komputer, tidak dikirim ke GitHub).

**Kalau persen HSC tidak terbaca otomatis:** skrip menulis catatan di kolom `catatan` pada `data_src/bei_hsc_pengumuman.csv`. Isi kolom `persen` dari PDF BEI, lalu jalankan `py update_hsc.py`.

## Struktur folder

```
data.json                      Balancepos per emiten per bulan (dibaca index.html dan Smart Money)
emiten_info.json               Sec. Num dan harga bulan terakhir (untuk % asing dan market cap)
hsc.json                       Daftar HSC aktif, yang sudah keluar, dan riwayatnya
data_src/                      File mentah dari sumber resmi
  bei_hsc_pengumuman.csv
  1pct/  klasifikasi/
  balancepos/                  Disimpan di komputer saja (lihat .gitignore)
update_data.py                 Update semua sumber
update_hsc.py                  Membangun hsc.json dari CSV pengumuman
build_screener.py              Membangun screener.json untuk screener.html
screener.json                  Data Screener (dibangun ulang otomatis setiap ada data baru)
jalankan_update.bat            Jalankan update dengan klik dua kali
pasang_jadwal.bat              Pasang jadwal update harian di Windows
```

`update_ksei.py` masih bisa dipakai untuk memasukkan satu file Balancepos secara manual, tapi sudah digantikan `update_data.py`.

## Disclaimer

Data disajikan apa adanya dari KSEI dan BEI untuk riset. Bukan rekomendasi jual atau beli. Selalu cek ulang ke sumber resmi.
