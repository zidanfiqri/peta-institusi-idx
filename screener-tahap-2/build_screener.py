"""
build_screener.py
Membangun screener.json untuk screener.html dari data yang sudah ada di repo.

Input:
  Balancepos terbaru (root atau data_src/balancepos/)  kepemilikan scripless per 9 tipe, Sec. Num, Price
  data.json                                           riwayat bulanan untuk perubahan kepemilikan asing
  data_src/1pct/satu_persen_YYYYMMDD.xlsx              pemegang di atas 1%, termasuk saham warkat (scrip)
  data_src/klasifikasi/klasifikasi_YYYYMMDD.xlsx       39 klasifikasi investor (opsional)
  hsc.json                                            daftar HSC resmi BEI
  data_src/sektor.csv                                 sektor dan industri

Catatan penting: Balancepos KSEI hanya mencatat saham tanpa warkat (scripless). Pemegang pengendali
sering memegang saham warkat (contoh: Jardine di ASII, Heineken di MLBI), jadi porsi asing yang benar
= asing scripless (Balancepos) + saham warkat milik pemegang asing di atas 1% (file 1%).

Jalankan: py build_screener.py
"""
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
WIB = timezone(timedelta(hours=7))
TIPE = ["IS", "CP", "PF", "IB", "ID", "MF", "SC", "FD", "OT"]
OUT = "screener.json"


def tgl_file(path):
    m = re.search(r"(\d{8})", os.path.basename(path))
    return m.group(1) if m else ""


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def file_terbaru(pola, paling_lambat=None):
    files = sorted(glob.glob(pola), key=tgl_file)
    if paling_lambat:
        cocok = [f for f in files if tgl_file(f) <= paling_lambat]
        files = cocok or files
    return files[-1] if files else None


def baca_balancepos(path):
    df = pd.read_csv(path, sep="|", low_memory=False)
    df = df[df["Type"] == "EQUITY"].copy()
    cols = [f"Local {t}" for t in TIPE] + [f"Foreign {t}" for t in TIPE] + ["Sec. Num", "Price"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["Code"] = df["Code"].astype(str).str.strip().str.upper()
    return df[df["Code"].str.len() >= 4].set_index("Code")


def baca_xlsx_header(path, kunci):
    """File BEI punya beberapa baris penafian di atas; cari baris header yang memuat `kunci`."""
    raw = pd.read_excel(path, header=None, dtype=str)
    for i in range(min(30, len(raw))):
        if any(str(v).strip().upper() == kunci for v in raw.iloc[i].values):
            df = raw.iloc[i + 1:].copy()
            df.columns = [str(c).strip() for c in raw.iloc[i].values]
            return df.dropna(how="all")
    raise ValueError(f"Header {kunci} tidak ditemukan di {path}")


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def main():
    # ---------------- Balancepos terbaru ----------------
    bp_files = glob.glob("Balancepos*.txt") + glob.glob("data_src/balancepos/Balancepos*.txt")
    bp_path = max(bp_files, key=tgl_file)
    bp_tgl = tgl_file(bp_path)
    bp = baca_balancepos(bp_path)

    # ---------------- riwayat asing dari data.json ----------------
    with open("data.json") as f:
        db = json.load(f)
    bulan = sorted({r["Date"] for v in db.values() for r in v})
    bln_now = f"{bp_tgl[:4]}-{bp_tgl[4:6]}"

    def asing_bulan(kode, ym):
        for r in db.get(kode, []):
            if r["Date"] == ym:
                return sum(r.get(f"Foreign {t}", 0) for t in TIPE)
        return None

    idx_now = bulan.index(bln_now) if bln_now in bulan else len(bulan) - 1
    bln_1 = bulan[idx_now - 1] if idx_now >= 1 else None
    bln_3 = bulan[idx_now - 3] if idx_now >= 3 else None

    # ---------------- pemegang di atas 1% ----------------
    p1_path = file_terbaru("data_src/1pct/satu_persen_*.xlsx", paling_lambat=bp_tgl)
    pemegang = {}
    if p1_path:
        d = baca_xlsx_header(p1_path, "SHARE_CODE")
        d["SHARE_CODE"] = d["SHARE_CODE"].astype(str).str.strip().str.upper()
        for c in ["HOLDINGS_SCRIPLESS", "HOLDINGS_SCRIP", "TOTAL_HOLDING_SHARES", "PERCENTAGE"]:
            d[c] = d[c].map(num)
        d["LOCAL_FOREIGN"] = d["LOCAL_FOREIGN"].fillna("").astype(str).str.strip().str.upper()
        for kode, g in d.groupby("SHARE_CODE"):
            pemegang[kode] = g
    # ---------------- klasifikasi 39 tipe (opsional) ----------------
    kl_path = file_terbaru("data_src/klasifikasi/klasifikasi_*.xlsx", paling_lambat=bp_tgl)
    klas = {}
    if kl_path:
        k = baca_xlsx_header(kl_path, "SHARE_CODE")
        k["SHARE_CODE"] = k["SHARE_CODE"].astype(str).str.strip().str.upper()
        fcols = [c for c in k.columns if c.startswith("Foreign_")]
        for _, r in k.iterrows():
            vals = {c.replace("Foreign_", "").strip(): num(r[c]) for c in fcols}
            tot = sum(vals.values())
            if tot > 0:
                top = sorted(((n, v) for n, v in vals.items() if v > 0), key=lambda x: -x[1])
                klas[r["SHARE_CODE"]] = [[n, round(v / tot * 100, 2)] for n, v in top]

    # ---------------- HSC dan sektor ----------------
    with open("hsc.json", encoding="utf-8") as f:
        hsc = json.load(f)
    hsc_aktif = {x["kode"]: x for x in hsc["aktif"]}
    sektor = {}
    if os.path.exists("data_src/sektor.csv"):
        s = pd.read_csv("data_src/sektor.csv", dtype=str)
        sektor = {r.kode: r for r in s.itertuples()}

    rows = []
    dilewati = []
    for kode, r in bp.iterrows():
        sec = r["Sec. Num"]
        if sec <= 0 or r["Price"] <= 0:
            # harga 0 di Balancepos: efek yang sudah delisting atau tidak diperdagangkan
            dilewati.append(kode)
            continue
        f_sl = {t: r[f"Foreign {t}"] for t in TIPE}
        l_sl = {t: r[f"Local {t}"] for t in TIPE}
        g = pemegang.get(kode)
        f_scrip = l_scrip = 0.0
        holders_f = []
        konsentrasi1 = ff5 = None
        nama = None
        if g is not None:
            f_scrip = g.loc[g["LOCAL_FOREIGN"] == "F", "HOLDINGS_SCRIP"].sum()
            l_scrip = g.loc[g["LOCAL_FOREIGN"] != "F", "HOLDINGS_SCRIP"].sum()
            nama = str(g["ISSUER_NAME"].iloc[0])
            per_inv = g.groupby("INVESTOR_NAME")["PERCENTAGE"].sum()
            konsentrasi1 = round(float(per_inv.sum()), 2)
            ff5 = round(max(0.0, 100 - float(per_inv[per_inv >= 5].sum())), 2)
            gf = g[g["LOCAL_FOREIGN"] == "F"].sort_values("PERCENTAGE", ascending=False)
            holders_f = [[str(x.INVESTOR_NAME), str(x.INVESTOR_CLASSIFICATION) if pd.notna(x.INVESTOR_CLASSIFICATION) else "",
                          str(x.NATIONALITY) if pd.notna(x.NATIONALITY) and str(x.NATIONALITY) != "nan" else (str(x.DOMICILE) if pd.notna(x.DOMICILE) else ""),
                          round(x.PERCENTAGE, 2), 1 if x.HOLDINGS_SCRIP > 0 else 0] for x in gf.itertuples()]
        asing = sum(f_sl.values()) + f_scrip
        scripless_tot = sum(f_sl.values()) + sum(l_sl.values())
        tercatat = scripless_tot + f_scrip + l_scrip
        sk = sektor.get(kode)
        nama = nama or (sk.nama if sk is not None and isinstance(sk.nama, str) else kode)

        def chg(ym):
            if not ym:
                return None
            v = asing_bulan(kode, ym)
            return None if v is None else round((sum(f_sl.values()) - v) / sec * 100, 2)

        h = hsc_aktif.get(kode)
        row = {
            "k": kode, "n": nama,
            "s": (sk.sektor if sk is not None and isinstance(sk.sektor, str) else ""),
            "i": (sk.industri if sk is not None and isinstance(sk.industri, str) else ""),
            "p": r["Price"], "mc": round(sec * r["Price"] / 1e12, 3),
            "a": round(asing / sec * 100, 2),
            "aw": round(f_scrip / sec * 100, 2),
            "ab": {t: round(v / sec * 100, 3) for t, v in f_sl.items() if v > 0},
            "rt": round(l_sl["ID"] / sec * 100, 2),
            "c1": chg(bln_1), "c3": chg(bln_3),
            "ff": ff5, "k1": konsentrasi1,
            "cv": round(min(tercatat / sec, 9.99), 3),
            "hf": holders_f,
        }
        if h:
            row["h"] = {"p": h["persen"], "pos": h.get("posisi_per"), "sj": h["sejak"], "u": h["link"]}
        if kode in klas:
            row["kl"] = klas[kode][:10]
        rows.append(row)

    meta = {
        "dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H.%M WIB"),
        "balancepos": iso(bp_tgl),
        "satu_persen": iso(tgl_file(p1_path)) if p1_path else None,
        "klasifikasi": iso(tgl_file(kl_path)) if kl_path else None,
        "hsc_terakhir": hsc["pengumuman_terakhir"], "hsc_diambil": hsc["diambil"],
        "hsc_jumlah": hsc["jumlah_aktif"],
        "banding_1": bln_1, "banding_3": bln_3,
        "dilewati_harga_nol": len(dilewati),
        "hsc_keluar": hsc["keluar"],
        "sumber": {
            "ksei": "https://www.ksei.co.id/id/publikasi/data-dan-statistik/kepemilikan-efek",
            "bei_kepemilikan": "https://www.idx.co.id/id/perusahaan-tercatat/data-kepemilikan-saham/",
            "bei_hsc": hsc["url_halaman"],
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: {len(rows)} emiten, Balancepos {meta['balancepos']}, 1% {meta['satu_persen']}, "
          f"klasifikasi {meta['klasifikasi']}, HSC {meta['hsc_jumlah']} saham -> {OUT} "
          f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
