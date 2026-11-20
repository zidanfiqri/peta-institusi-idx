"""build_flow.py
Data untuk persentase & rincian pemegang di Peta Institusi (index.html) dan halaman Foreign Flow
(foreign_flow.html).

Keluaran
  detail/{KODE}.json   per saham: jumlah saham KSEI (Sec. Num) per bulan, pemegang >=1% per bulan
                       (dipetakan ke tipe investor KSEI), kepemilikan asing per tipe, arus asing per
                       tipe, dan komposisi kelas investor BEI.
  foreign_flow.json    ringkasan: peristiwa indeks MSCI, arus asing per tipe dan kelas untuk seluruh
                       pasar dan per saham, serta peringkat arus asing per tipe per bulan.
  harian_asing.json    arus asing harian (ringkasan saham BEI) untuk saham pantauan MSCI.
  data_src/saham_ksei.csv  Sec. Num dari setiap file Balancepos yang pernah diproses (terakumulasi).

Sumber (semuanya resmi; tidak ada angka yang diisi manual kecuali daftar peristiwa MSCI)
  data.json                          Balancepos KSEI: lembar tanpa warkat per tipe investor per bulan
  Balancepos*.txt                    kolom Sec. Num = jumlah saham versi KSEI (penyebut persentase file 1%)
  satu_persen_*.xlsx, data_src/1pct  pemegang saham >=1% (KSEI, dipublikasikan BEI)
  data_src/1pct_riwayat.csv          pemegang >=1% bulan yang file xlsx-nya tidak ada di repo ini
                                     (Mei 2026, dari repo ksei-ownership-dashboard, sumber PDF KSEI)
  data_src/pemetaan_tipe.json        pemetaan klasifikasi BEI -> tipe KSEI (sama dengan ksei-ownership-dashboard)
  data_src/klasifikasi/*.xlsx        jumlah saham tanpa warkat per 39 kelas investor (BEI). Mar-Agu 2026
                                     tanpa pemisahan lokal/asing; mulai Sep 2026 dipisah lokal/asing.
  data_src/harga_akhir_bulan.csv     harga penutupan akhir bulan
  data_src/idx_harian/*.csv.gz       ringkasan saham harian BEI: volume beli & jual asing
  data_src/peristiwa_msci.json       tanggal dan daftar saham hasil review indeks MSCI (dari pengumuman MSCI
                                     yang dikutip media; tautan sumber ada di file)

Catatan metode
  - Persen "tanpa warkat" = lembar / total saham tanpa warkat (jumlah 18 kolom Balancepos). Selalu tersedia.
  - Persen "total saham" = lembar / Sec. Num KSEI. Sec. Num pasti bila file Balancepos bulan itu ada; bila
    tidak, diturunkan dari file 1% bulan yang sama (lembar / persen tiap pemegang, galat <= 0,1%) dan
    ditandai "est". Bulan tanpa keduanya tidak diberi persen total saham.
  - Tipe KSEI pemegang >=1% dipetakan dari klasifikasi BEI. Bila seorang pemegang tidak mungkin berada di
    tipe hasil pemetaan (lembarnya melebihi total tipe itu di Balancepos), pemegang dipindah ke satu-satunya
    tipe yang muat; bila ada beberapa kemungkinan, ditandai tidak pasti. Tidak ada penebakan lain.
  - Arus asing bulanan = perubahan lembar x rata-rata harga dua akhir bulan, disesuaikan split
    (fungsi aksi_korporasi di build_dana.py). Ini estimasi beli/jual bersih, bukan data transaksi.
  - Arus asing harian = (volume beli asing - volume jual asing) x (nilai / volume) hari itu.

Jalankan: python build_flow.py
"""
import csv
import glob
import gzip
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd

import build_dana as BD

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
WIB = timezone(timedelta(hours=7))
TIPE = ["IS", "CP", "PF", "IB", "ID", "MF", "SC", "FD", "OT"]
SAHAM_KSEI = "data_src/saham_ksei.csv"
RIWAYAT_1PCT = "data_src/1pct_riwayat.csv"
PEMETAAN = "data_src/pemetaan_tipe.json"
PERISTIWA = "data_src/peristiwa_msci.json"
DIR_KLAS = "data_src/klasifikasi"
DIR_HARIAN = "data_src/idx_harian"
OUT_DETAIL = "detail"

# Kelompok kelas BEI yang relevan untuk investor asing; sisanya masuk "Lainnya"
KELAS = ["Exchange Traded Funds", "Mutual Funds (MF)", "Hedge Fund", "Investment Manager",
         "Investment Advisors", "Pension Funds (PF)", "Insurance (IS)", "Sovereign Wealth Fund",
         "Central Bank", "Government", "Bank", "Private Bank", "Trustee Bank", "Financial Institutional (IB)",
         "Brokerage Firms", "Securities Company (SC)", "Corporate", "Private Equity", "Venture Capital",
         "Individual (ID)"]


def num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return 0.0 if math.isnan(v) else v
    except (TypeError, ValueError):
        return 0.0


def ym_dari_tgl(t):
    return f"{t[:4]}-{t[5:7]}" if "-" in t else f"{t[:4]}-{t[4:6]}"


# ------------------------------------------------------------------ Sec. Num (jumlah saham KSEI)
def tgl_balancepos(path):
    m = re.search(r"(\d{8})", os.path.basename(path))
    return m.group(1) if m else None


def perbarui_saham_ksei():
    """Kumpulkan Sec. Num dari semua Balancepos*.txt yang ada ke data_src/saham_ksei.csv (upsert)."""
    data = {}
    if os.path.exists(SAHAM_KSEI):
        with open(SAHAM_KSEI, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                data[(r["bulan"], r["kode"])] = (r["tanggal"], float(r["sec_num"]))
    files = sorted(set(glob.glob("Balancepos*.txt") + glob.glob("data_src/balancepos/Balancepos*.txt")))
    baru = 0
    for path in files:
        tgl = tgl_balancepos(path)
        if not tgl:
            continue
        ym = ym_dari_tgl(tgl)
        df = pd.read_csv(path, sep="|", low_memory=False)
        df.columns = [c.strip() for c in df.columns]
        df = df[df["Type"].astype(str).str.strip() == "EQUITY"]
        for k, s in zip(df["Code"].astype(str).str.strip().str.upper(), pd.to_numeric(df["Sec. Num"], errors="coerce")):
            if len(k) >= 4 and s and s > 0:
                if (ym, k) not in data:
                    baru += 1
                data[(ym, k)] = (f"{tgl[:4]}-{tgl[4:6]}-{tgl[6:]}", float(s))
    os.makedirs(os.path.dirname(SAHAM_KSEI), exist_ok=True)
    with open(SAHAM_KSEI, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bulan", "tanggal", "kode", "sec_num"])
        for (ym, k), (t, s) in sorted(data.items()):
            w.writerow([ym, t, k, int(s)])
    print(f"saham_ksei.csv: {len(data)} baris ({baru} baru) dari {len(files)} file Balancepos")
    out = defaultdict(dict)
    for (ym, k), (t, s) in data.items():
        out[k][ym] = s
    return out


# ------------------------------------------------------------------ pemegang >= 1%
def baca_pemetaan():
    with open(PEMETAAN, encoding="utf-8") as f:
        m = json.load(f)
    return {k.strip().lower(): (v.get("kode") or None) for k, v in m.items()}


def cari_file_1pct():
    """{tanggal 'YYYY-MM-DD': path} dari xlsx pemegang 1% (nama file bebas; tanggal dibaca dari isi)."""
    hasil = {}
    files = set(glob.glob("*.xlsx") + glob.glob("data_src/*.xlsx") + glob.glob("data_src/*/*.xlsx"))
    for path in sorted(files):
        try:
            raw = pd.read_excel(path, header=None, dtype=str, nrows=40)
        except Exception:
            continue
        for i in range(len(raw)):
            vals = [str(v).strip() for v in raw.iloc[i].values]
            if "SHARE_CODE" in vals and "INVESTOR_NAME" in vals and i + 1 < len(raw):
                t = str(raw.iloc[i + 1].values[vals.index("DATE")])[:10]
                if re.match(r"\d{4}-\d{2}-\d{2}", t):
                    hasil[t] = path
                break
    return hasil


def baca_1pct_xlsx(path):
    raw = pd.read_excel(path, header=None, dtype=str)
    for i in range(min(40, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].values]
        if "SHARE_CODE" in vals and "INVESTOR_NAME" in vals:
            d = raw.iloc[i + 1:].copy()
            d.columns = vals
            break
    d = d[d["SHARE_CODE"].notna()]
    out = pd.DataFrame({
        "kode": d["SHARE_CODE"].astype(str).str.strip().str.upper(),
        "nama": d["INVESTOR_NAME"].fillna("").astype(str).str.strip(),
        "klas": d.get("INVESTOR_CLASSIFICATION", pd.Series("", index=d.index)).fillna("").astype(str).str.strip(),
        "la": d["LOCAL_FOREIGN"].fillna("").astype(str).str.strip().str.upper().replace({"D": "L", "A": "F"}),
        "sl": d["HOLDINGS_SCRIPLESS"].map(num),
        "sc": d["HOLDINGS_SCRIP"].map(num),
        "pct": d["PERCENTAGE"].map(num),
        "ctrl": d.get("CONTROLLER_OR_AFFILIATE", pd.Series("", index=d.index)).fillna("").astype(str).str.strip(),
    })
    out.loc[out["klas"].str.lower() == "nan", "klas"] = ""
    out.loc[out["ctrl"].str.lower() == "nan", "ctrl"] = ""
    return out[out["kode"].str.len() >= 4]


def baca_1pct_riwayat():
    if not os.path.exists(RIWAYAT_1PCT):
        return {}
    d = pd.read_csv(RIWAYAT_1PCT, dtype=str).fillna("")
    hasil = {}
    for t, g in d.groupby("periode"):
        hasil[t] = pd.DataFrame({
            "kode": g["kode"].str.upper(), "nama": g["investor"], "klas": g["klasifikasi"],
            "la": g["lokal_asing"].str.upper().replace({"D": "L"}), "sl": g["scripless"].map(num),
            "sc": g["scrip"].map(num), "pct": g["persentase"].map(num), "ctrl": "",
        })
    return hasil


def tentukan_tipe(rows, kap, pemetaan):
    """rows: list dict pemegang satu saham satu sisi; kap: {tipe: lembar Balancepos}.
    Isi r['t'] (tipe hasil pemetaan), r['adj'] (tipe hasil penyesuaian atau None), r['ragu'] (bool)."""
    pakai = defaultdict(float)
    for r in rows:
        r["t"] = pemetaan.get(r["klas"].lower()) if r["klas"] else None
        r["adj"], r["ragu"] = None, False
        if r["t"] and r["sl"] > 0:
            pakai[r["t"]] += r["sl"]
    for _ in range(10):
        berubah = False
        for r in sorted(rows, key=lambda x: -x["sl"]):
            t = r["adj"] or r["t"]
            if r["sl"] <= 0 or (t and r["sl"] <= kap.get(t, 0) + 1):
                continue
            # pemegang ini tidak mungkin berada di tipe t: lembarnya melebihi total tipe itu
            muat = [u for u in TIPE if u != t and pakai[u] + r["sl"] <= kap.get(u, 0) + 1]
            if len(muat) == 1:
                if t:
                    pakai[t] -= r["sl"]
                pakai[muat[0]] += r["sl"]
                r["adj"], r["ragu"], berubah = muat[0], False, True
            else:
                r["ragu"] = True
        if not berubah:
            break


def interval_sec(rows):
    """Batas Sec. Num dari persen 2 desimal tiap pemegang: total/(p+0,005) <= Sec <= total/(p-0,005)."""
    lo, hi = 0.0, float("inf")
    for r in rows:
        tot, p = r["sl"] + r["sc"], r["pct"]
        if tot > 0 and p >= 0.05:
            lo = max(lo, tot * 100 / (p + 0.005))
            hi = min(hi, tot * 100 / (p - 0.005))
    return (lo, hi) if lo > 0 and hi < float("inf") and lo <= hi * 1.000001 else None


# ------------------------------------------------------------------ klasifikasi BEI
def norm_kelas(n):
    n = n.strip().title().replace(" Of ", " of ").replace(" And ", " and ").replace(" Or ", " or ").replace(" To ", " to ")
    return re.sub(r"\(([A-Za-z]+)\)", lambda m: "(" + m.group(1).upper() + ")", n)


def baca_klasifikasi():
    """{ym: {kode: {"L": {kelas: lembar}, "F": {...}} atau {"LF": {...}}}}"""
    hasil = {}
    for path in sorted(glob.glob(os.path.join(DIR_KLAS, "*.xlsx"))):
        raw = pd.read_excel(path, header=None, dtype=str)
        hr = None
        for i in range(min(15, len(raw))):
            if any(str(v).strip().upper().replace(" ", "_") == "SHARE_CODE" for v in raw.iloc[i].values):
                hr = i
                break
        if hr is None:
            continue
        cols = [str(c).strip() for c in raw.iloc[hr].values]
        d = raw.iloc[hr + 1:]
        ck = next(j for j, c in enumerate(cols) if c.upper().replace(" ", "_") == "SHARE_CODE")
        cd = next(j for j, c in enumerate(cols) if c.upper() == "DATE")
        t = str(d.iloc[0, cd])[:10]
        ym = t[:7]
        per = {}
        for _, row in d.iterrows():
            k = str(row.iloc[ck]).strip().upper()
            if len(k) < 4 or k == "NAN":
                continue
            rec = defaultdict(lambda: defaultdict(float))
            for j, c in enumerate(cols):
                if j <= 2 or c in ("", "nan") or c.lower().startswith("total"):
                    continue
                v = num(row.iloc[j])
                if not v:
                    continue
                if c.startswith("Local_"):
                    s, n = "L", c[6:]
                elif c.startswith("Foreign_"):
                    s, n = "F", c[8:]
                else:
                    s, n = "LF", c
                n = norm_kelas(n)
                rec[s][n if n in KELAS else "Lainnya"] += v
            per[k] = {s: dict(v) for s, v in rec.items()}
        hasil[ym] = {"tgl": t, "data": per}
    return hasil


# ------------------------------------------------------------------ ringkasan harian BEI
def baca_harian():
    """{tanggal: {kode: (close, volume, value, fbuy, fsell, nonreg_volume, nonreg_value)}}
    Volume, nilai, dan beli/jual asing di ringkasan BEI adalah pasar reguler; transaksi negosiasi
    (nonreg) terpisah dan tidak dirinci asing/lokal."""
    hasil = {}
    for path in sorted(glob.glob(os.path.join(DIR_HARIAN, "*.csv.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                hasil.setdefault(r["tanggal"], {})[r["kode"]] = (num(r["close"]), num(r["volume"]), num(r["value"]),
                                                                  num(r["foreign_buy"]), num(r["foreign_sell"]),
                                                                  num(r.get("nonreg_volume")), num(r.get("nonreg_value")))
    return hasil


# ------------------------------------------------------------------ utama
def main():
    with open("data.json", encoding="utf-8") as f:
        db = json.load(f)
    harga, listed, tanggal, nama = BD.baca_harga()
    kurs = BD.baca_kurs()
    bulan = sorted({r["Date"] for v in db.values() for r in v})
    rows_db = {k: {r["Date"]: r for r in v} for k, v in db.items()}

    sec = perbarui_saham_ksei()
    pemetaan = baca_pemetaan()

    # pemegang 1%: xlsx (diutamakan) + riwayat; hanya tanggal yang sama dengan tanggal Balancepos bulan itu
    sumber_1pct = {t: baca_1pct_xlsx(p) for t, p in cari_file_1pct().items()}
    for t, d in baca_1pct_riwayat().items():
        sumber_1pct.setdefault(t, d)
    p1 = {}
    for t, d in sorted(sumber_1pct.items()):
        ym = t[:7]
        if ym in bulan and str(tanggal.get(ym, "")) .replace("-", "") == t.replace("-", ""):
            p1[ym] = (t, d)
        else:
            print(f"1%: {t} dilewati (tidak sama dengan tanggal Balancepos bulan {ym}: {tanggal.get(ym)})")
    print("1%: bulan dipakai", sorted(p1))

    klas = baca_klasifikasi()
    print("klasifikasi:", {ym: ("lokal/asing" if any("F" in v for v in x["data"].values()) else "gabungan") for ym, x in sorted(klas.items())})

    with open(PERISTIWA, encoding="utf-8") as f:
        peristiwa = json.load(f)

    # tanda baris peringkat yang bukan transaksi (dari build_dana: aksi korporasi, reklasifikasi, pencatatan)
    bendera = {}
    try:
        with open("dana.json", encoding="utf-8") as f:
            dn = json.load(f)
        for m, a in dn.get("aliran", {}).items():
            for x in a.get("daftar_aksi_korporasi", []):
                bendera[(m, x["k"])] = "split" if x.get("split", 1) != 1 else "terbit"
            for x in a.get("daftar_reklasifikasi", []):
                bendera[(m, x["k"])] = "reklasifikasi"
            for x in a.get("daftar_pencatatan", []):
                bendera[(m, x["k"])] = "pencatatan"
    except (OSError, ValueError):
        pass

    os.makedirs(OUT_DETAIL, exist_ok=True)
    ringkas_saham = {}
    peringkat = {}           # ym -> list [kode, arus per tipe asing (Rp miliar)]
    kelas_pasar = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))   # ym -> sisi -> kelas -> Rp
    stat = defaultdict(int)

    for k in sorted(rows_db):
        rb = rows_db[k]
        ms = [m for m in bulan if m in rb]
        if not ms:
            continue
        # --- Sec. Num per bulan
        sec_k = {m: [int(sec[k][m]), "ksei"] for m in ms if m in sec.get(k, {})}
        # --- pemegang >= 1%
        det_p1 = {}
        for ym, (t, d) in p1.items():
            g = d[d["kode"] == k]
            if not len(g) or ym not in rb:
                continue
            r0 = rb[ym]
            hs = []
            for side in ("L", "F"):
                pre = "Local " if side == "L" else "Foreign "
                kap = {u: float(r0.get(pre + u, 0) or 0) for u in TIPE}
                rows = [dict(nama=x.nama, klas=x.klas, la=side, sl=x.sl, sc=x.sc, pct=x.pct, ctrl=x.ctrl)
                        for x in g[g["la"] == side].itertuples()]
                tentukan_tipe(rows, kap, pemetaan)
                for r in rows:
                    stat["adj"] += bool(r["adj"])
                    stat["ragu"] += bool(r["ragu"])
                hs += rows
            iv = interval_sec(hs)
            if ym not in sec_k and iv:
                # Sec. Num pasti dari Balancepos lain yang masih di dalam batas -> pakai; selain itu titik tengah
                pasti = [v for mm, v in sorted(sec.get(k, {}).items()) if iv[0] - 1 <= v <= iv[1] + 1]
                sec_k[ym] = [int(pasti[-1] if pasti else (iv[0] + iv[1]) / 2), "est"]
                stat["sec_est"] += 1
            det_p1[ym] = {"tgl": t, "h": [[r["nama"], r["la"], r["t"] or "", r["klas"], int(r["sl"]), int(r["sc"]),
                                           round(r["pct"], 2), r["adj"] or "", 1 if r["ragu"] else 0, r["ctrl"]]
                                          for r in sorted(hs, key=lambda x: (-x["pct"], x["nama"]))]}
        # --- asing per tipe per bulan, arus per tipe (Rp), split
        fser = {u: [float(rb[m].get(f"Foreign {u}", 0) or 0) for m in ms] for u in TIPE}
        lser = [sum(float(rb[m].get(f"Local {u}", 0) or 0) for u in TIPE) for m in ms]
        scl = [lser[i] + sum(fser[u][i] for u in TIPE) for i in range(len(ms))]
        px = [harga.get(m, {}).get(k) for m in ms]
        arus, split = [], []
        for i in range(1, len(ms)):
            m0, m1 = ms[i - 1], ms[i]
            h0, h1 = scl[i - 1], scl[i]
            sp, terbit = BD.aksi_korporasi(k, m0, m1, listed, harga, h0, h1)
            p0, p1_ = px[i - 1], px[i]
            pr = (p0 / sp + p1_) / 2 if (p0 and p1_) else None
            split.append(round(sp, 6) if sp != 1 else 1)
            if pr is None:
                arus.append(None)
                continue
            arus.append([round((fser[u][i] - fser[u][i - 1] * sp) * pr / 1e6) for u in TIPE] +
                        [round((lser[i] - lser[i - 1] * sp) * pr / 1e6), 1 if terbit else 0])
        for i in range(1, len(ms)):
            a = arus[i - 1]
            if a is not None:
                peringkat.setdefault(ms[i], []).append([k] + [round(v / 1e3, 2) for v in a[:9]] +
                                                       [bendera.get((ms[i], k), "") or ("terbit" if a[10] else "")])
        # --- kelas BEI per bulan (sisi asing bila tersedia, gabungan bila belum)
        det_kl = {}
        for ym, x in klas.items():
            v = x["data"].get(k)
            if not v:
                continue
            det_kl[ym] = {s: {n: int(c) for n, c in sorted(vv.items(), key=lambda z: -z[1])} for s, vv in v.items()}
            p = harga.get(ym, {}).get(k)
            if p:
                for s, vv in v.items():
                    for n, c in vv.items():
                        kelas_pasar[ym][s][n] += c * p
        det = {"kode": k, "nama": nama.get(k, ""), "satuan_arus": "juta rupiah", "bulan": ms, "harga": px, "sec": sec_k, "p1": det_p1,
               "asing": {u: [int(v) for v in fser[u]] for u in TIPE}, "lokal": [int(v) for v in lser],
               "tanpa_warkat": [int(v) for v in scl], "split": split, "arus": arus, "kelas": det_kl}
        with open(os.path.join(OUT_DETAIL, f"{k}.json"), "w", encoding="utf-8") as f:
            json.dump(det, f, ensure_ascii=False, separators=(",", ":"))
        # ringkasan untuk tabel saham di Foreign Flow
        i = len(ms) - 1
        va = sum(fser[u][i] for u in TIPE) * (px[i] or 0)
        ringkas_saham[k] = {"n": nama.get(k, ""), "va": round(va), "pa": round(sum(fser[u][i] for u in TIPE) / scl[i] * 100, 2) if scl[i] else None,
                            "b": ms[i]}
    print(f"detail/: {len(ringkas_saham)} saham; Sec. Num estimasi {stat['sec_est']}; "
          f"pemegang 1% dipindah tipe {stat['adj']}, tidak pasti {stat['ragu']}")

    # --- arus asing harian untuk saham pantauan (peristiwa MSCI + portofolio EIDO)
    pantau = set()
    for e in peristiwa["peristiwa"]:
        for key in ("hapus_standard", "turun_small", "hapus_small", "tambah_standard", "tambah_small"):
            pantau |= set(e.get(key, []))
    pantau |= set(peristiwa.get("standard_sekarang", []))
    try:
        with open("eido.json", encoding="utf-8") as f:
            pantau |= {h["k"] for h in json.load(f)["holdings"] if h.get("k")}
    except (OSError, KeyError, ValueError):
        pass
    hr = baca_harian()
    hari = sorted(hr)
    pasar_harian = []
    saham_harian = {k: [] for k in sorted(pantau)}
    for t in hari:
        tot_b = tot_s = 0.0
        for k, (c, vol, val, fb, fs, nvol, nval) in hr[t].items():
            vw = val / vol if vol else c
            tot_b += fb * vw
            tot_s += fs * vw
            if k in saham_harian:
                saham_harian[k].append([t, c, round(vol), round(val), round(fb), round(fs), round(nvol), round(nval)])
        pasar_harian.append([t, round(tot_b), round(tot_s)])
    with open("harian_asing.json", "w", encoding="utf-8") as f:
        json.dump({"meta": {"dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB"), "hari": len(hari),
                            "dari": hari[0] if hari else None, "sampai": hari[-1] if hari else None,
                            "kolom": ["tanggal", "close", "volume", "nilai", "beli_asing_lembar", "jual_asing_lembar",
                                      "volume_negosiasi", "nilai_negosiasi"],
                            "sumber": "Ringkasan Saham harian BEI (idx.co.id): Volume/Value/ForeignBuy/ForeignSell "
                                      "= pasar reguler; NonRegularVolume/Value = pasar negosiasi (tanpa rincian asing)"},
                   "pasar": pasar_harian, "saham": {k: v for k, v in saham_harian.items() if v}},
                  f, ensure_ascii=False, separators=(",", ":"))

    # --- ringkasan pasar & peringkat
    ff = {
        "meta": {"dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB"), "bulan": bulan,
                 "tanggal": {m: tanggal.get(m) for m in bulan}, "kurs": {m: kurs.get(m) for m in bulan},
                 "tipe": TIPE, "bulan_1pct": sorted(p1), "bulan_kelas": sorted(klas),
                 "kelas_lokal_asing": sorted(ym for ym, x in klas.items() if any("F" in v for v in x["data"].values())),
                 "hari_terakhir": hari[-1] if hari else None},
        "peristiwa": peristiwa,
        "kelas_pasar": {ym: {s: {n: round(v) for n, v in sorted(vv.items(), key=lambda z: -z[1])} for s, vv in x.items()}
                        for ym, x in sorted(kelas_pasar.items())},
        "peringkat": {ym: sorted([r for r in v if any(abs(x) >= 0.05 for x in r[1:10])], key=lambda r: sum(r[1:10]))
                      for ym, v in sorted(peringkat.items())[-12:]},
        "saham": ringkas_saham,
    }
    with open("foreign_flow.json", "w", encoding="utf-8") as f:
        json.dump(ff, f, ensure_ascii=False, separators=(",", ":"))
    print("foreign_flow.json, harian_asing.json: hari", len(hari), "pantauan", len([k for k in saham_harian if saham_harian[k]]))


if __name__ == "__main__":
    main()
