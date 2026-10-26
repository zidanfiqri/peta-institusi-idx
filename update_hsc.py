"""
update_hsc.py
Membangun hsc.json dari daftar pengumuman HSC resmi BEI.

Sumber: idx.co.id > Perusahaan Tercatat > Kepemilikan Saham Terkonsentrasi Tinggi
Input : data_src/bei_hsc_pengumuman.csv
        kolom: jenis, kode, tanggal, persen, posisi_per, catatan, file
        jenis      = Pengenaan (masuk HSC) atau Pencabutan (keluar HSC)
        persen     = porsi saham yang dikuasai sejumlah tertentu pemegang saham
                     secara agregat, sesuai PDF pengumuman (kosong untuk Pencabutan)
        posisi_per = tanggal posisi kepemilikan yang dipakai BEI
Output: hsc.json

Cara update: tambahkan baris pengumuman baru ke CSV, lalu jalankan
    python update_hsc.py
"""
import csv
import json
import sys
from datetime import datetime, timezone, timedelta

SRC = "data_src/bei_hsc_pengumuman.csv"
OUT = "hsc.json"
PAGE = "https://www.idx.co.id/id/perusahaan-tercatat/kepemilikan-saham-terkonsentrasi-tinggi/"
MEDIA = "https://www.idx.co.id/Media/"


def main(diambil=None):
    with open(SRC, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["jenis"] not in ("Pengenaan", "Pencabutan"):
            sys.exit(f"Jenis tidak dikenal: {r}")
        r["link"] = MEDIA + r["file"]
    # urut kronologis; pada tanggal yang sama, Pencabutan diproses dulu
    rows.sort(key=lambda r: (r["tanggal"], 0 if r["jenis"] == "Pencabutan" else 1))

    status = {}   # kode -> dict status terkini
    riwayat = []
    for r in rows:
        k = r["kode"]
        persen = float(r["persen"]) if r.get("persen") else None
        posisi = r.get("posisi_per") or None
        catatan = r.get("catatan") or None
        riwayat.append({"kode": k, "jenis": r["jenis"], "tanggal": r["tanggal"],
                        "persen": persen, "posisi_per": posisi, "link": r["link"]})
        if r["jenis"] == "Pengenaan":
            if persen is None:
                sys.exit(f"Persen kosong untuk pengumuman Pengenaan: {k} {r['tanggal']}")
            status[k] = {"kode": k, "aktif": True, "sejak": r["tanggal"], "persen": persen,
                         "posisi_per": posisi, "link": r["link"]}
            if catatan:
                status[k]["catatan"] = catatan
        else:
            prev = status.get(k, {"kode": k})
            status[k] = {"kode": k, "aktif": False, "masuk": prev.get("sejak"),
                         "persen_terakhir": prev.get("persen"), "keluar": r["tanggal"],
                         "posisi_keluar": posisi, "link_keluar": r["link"]}

    aktif = sorted([s for s in status.values() if s["aktif"]], key=lambda s: s["kode"])
    keluar = sorted([s for s in status.values() if not s["aktif"]], key=lambda s: s["kode"])
    wib = timezone(timedelta(hours=7))
    out = {
        "sumber": "BEI: Perusahaan Tercatat > Kepemilikan Saham Terkonsentrasi Tinggi",
        "url_halaman": PAGE,
        "diambil": diambil or datetime.now(wib).strftime("%Y-%m-%d %H.%M WIB"),
        "pengumuman_terakhir": max(r["tanggal"] for r in rows),
        "jumlah_pengumuman": len(rows),
        "jumlah_aktif": len(aktif),
        "aktif": aktif,
        "keluar": keluar,
        "riwayat": sorted(riwayat, key=lambda r: (r["tanggal"], r["kode"]), reverse=True),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(aktif)} saham HSC aktif, {len(keluar)} sudah keluar, "
          f"{len(rows)} pengumuman (terakhir {out['pengumuman_terakhir']}).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
