"""
build_screener.py
Membangun screener.json untuk screener.html dari data yang sudah ada di repo.

Input:
  Balancepos terbaru (root atau data_src/balancepos/)  kepemilikan scripless per 9 tipe, Sec. Num, Price
  data.json                                           riwayat bulanan untuk perubahan kepemilikan asing
  file BEI pemegang di atas 1% (xlsx)                 termasuk saham warkat (scrip)
  file BEI klasifikasi investor (xlsx, opsional)      39 klasifikasi investor
      Kedua file BEI boleh diletakkan di folder utama, data_src, atau data_src/1pct dan
      data_src/klasifikasi, dengan nama apa saja. Jenis dan tanggalnya dibaca dari isi file.
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

# ---- Asing pengendali atau strategis --------------------------------------------------------
# Tujuan Screener: mengukur porsi investor asing portofolio (jenis yang diikuti indeks seperti MSCI),
# jadi pemegang asing pengendali atau strategis (contoh: Jardine di ASII, MUFG di BDMN) dikecualikan.
# Pemegang asing di file 1% dianggap pengendali atau strategis kalau:
BATAS_PENGENDALI = 10.0   # 1) porsinya minimal 10% saham, apa pun klasifikasinya
BATAS_INDIVIDU = 5.0      # 2) individu atau tanpa klasifikasi dengan porsi minimal 5%
KLAS_STRATEGIS = {        # 3) klasifikasinya bukan investor portofolio, berapa pun porsinya (di atas 1%)
    "corporate", "private equity", "venture capital", "firm", "partnership", "sole proprietorship",
    "commanditaire vennootschap (cv) or limited partnership", "state owned enterprises", "state owned company",
    "government", "central bank", "permanent establishment", "international organization",
    "association/social organizations", "cooperatives", "foundation", "diocese", "conference",
    "congregation", "political parties", "educational institution",
}
# Rekening kustodian atau broker global (sering tercatat Corporate) menyimpan saham milik banyak
# investor portofolio, jadi tidak dianggap pengendali selama porsinya di bawah BATAS_PENGENDALI.
KUSTODIAN = re.compile(
    r"MORGAN STANLEY|GOLDMAN SACHS|J\.?\s?P\.?\s?MORGAN|CITIBANK|CITIGROUP|HSBC|UBS AG|BANK OF NEW YORK|BNY MELLON|"
    r"STATE STREET|DEUTSCHE BANK|BNP PARIBAS|STANDARD CHARTERED|DBS BANK|CREDIT SUISSE|NOMURA|MERRILL LYNCH|"
    r"BARCLAYS|CLEARSTREAM|EUROCLEAR|NORTHERN TRUST|SOCIETE GENERALE|CACEIS|MACQUARIE|CLSA|UOB KAY HIAN|"
    r"MAYBANK SECURITIES|PHILLIP SECURITIES|CGS INTERNATIONAL|KGI SECURITIES", re.I)


def tgl_file(path):
    m = re.search(r"(\d{8})", os.path.basename(path))
    return m.group(1) if m else ""


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def tanggal_sel(v):
    """Isi kolom DATE file BEI: '2026-09-30 00:00:00', '31-Jul-2026', atau nomor seri Excel."""
    s = str(v).strip()
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        return (datetime(1899, 12, 30) + timedelta(days=int(float(s)))).strftime("%Y%m%d")
    try:
        return pd.to_datetime(s, dayfirst=not re.match(r"\d{4}-", s)).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return ""


def kenali_file_bei():
    """Cari file BEI (pemegang 1% dan klasifikasi) di folder utama, data_src, dan subfoldernya.
    Jenis dan tanggal dibaca dari isi file, jadi nama file bebas: file asli dari BEI
    (misalnya peng-2026-09-00024-satu-persen.xlsx) bisa langsung diunggah apa adanya."""
    hasil = {"1pct": [], "klas": []}
    files = set(glob.glob("*.xlsx") + glob.glob("data_src/*.xlsx") + glob.glob("data_src/*/*.xlsx"))
    for f in sorted(files):
        try:
            raw = pd.read_excel(f, header=None, dtype=str, nrows=40)
        except Exception:
            continue
        for i in range(len(raw)):
            vals = [str(v).strip() for v in raw.iloc[i].values]
            if "SHARE_CODE" not in vals:
                continue
            jenis = "1pct" if "INVESTOR_NAME" in vals else ("klas" if any(v.startswith("Foreign_") for v in vals) else None)
            if jenis and i + 1 < len(raw):
                tgl = tanggal_sel(raw.iloc[i + 1].values[vals.index("DATE")]) if "DATE" in vals else tgl_file(f)
                if tgl:
                    hasil[jenis].append((tgl, f))
            break
    return hasil


def pilih(daftar, paling_lambat):
    """File terbaru yang tanggalnya tidak melewati Balancepos; kalau tidak ada, yang terbaru."""
    daftar = sorted(daftar)
    cocok = [x for x in daftar if x[0] <= paling_lambat] or daftar
    return cocok[-1] if cocok else (None, None)


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


def baca_1pct(path):
    d = baca_xlsx_header(path, "SHARE_CODE")
    d["SHARE_CODE"] = d["SHARE_CODE"].astype(str).str.strip().str.upper()
    for c in ["HOLDINGS_SCRIPLESS", "HOLDINGS_SCRIP", "TOTAL_HOLDING_SHARES", "PERCENTAGE"]:
        d[c] = d[c].map(num)
    d["LOCAL_FOREIGN"] = d["LOCAL_FOREIGN"].fillna("").astype(str).str.strip().str.upper()
    d["INVESTOR_CLASSIFICATION"] = d["INVESTOR_CLASSIFICATION"].fillna("").astype(str).str.strip()
    return {kode: g for kode, g in d.groupby("SHARE_CODE")}


def pengendali(klas, persen, nama=""):
    k = klas.strip().lower()
    if persen >= BATAS_PENGENDALI:
        return True
    if KUSTODIAN.search(nama or ""):
        return False
    if k in KLAS_STRATEGIS:
        return True
    if (k in ("individual", "", "nan")) and persen >= BATAS_INDIVIDU:
        return True
    return False


def pecah_asing(g):
    """Dari file 1% satu emiten: saham warkat asing, dan porsi pemegang asing pengendali."""
    if g is None:
        return {"scrip": 0.0, "ctrl_sl": 0.0, "ctrl_sc": 0.0, "ctrl": [], "nama_ctrl": set(), "ctrl_kelas": {}}
    f = g[g["LOCAL_FOREIGN"] == "F"]
    per = f.groupby("INVESTOR_NAME").agg(p=("PERCENTAGE", "sum"), sl=("HOLDINGS_SCRIPLESS", "sum"),
                                          sc=("HOLDINGS_SCRIP", "sum"), k=("INVESTOR_CLASSIFICATION", "first"))
    mask = pd.Series([pengendali(r.k, r.p, n) for n, r in zip(per.index, per.itertuples())], index=per.index, dtype=bool)
    ctrl = per.loc[mask]
    return {"scrip": float(f["HOLDINGS_SCRIP"].sum()), "ctrl_sl": float(ctrl["sl"].sum()),
            "ctrl_sc": float(ctrl["sc"].sum()),
            "ctrl": [[n, r.k, round(r.p, 2)] for n, r in ctrl.sort_values("p", ascending=False).iterrows()],
            "nama_ctrl": set(ctrl.index),
            "ctrl_kelas": ctrl.groupby(ctrl["k"].str.lower())["sl"].sum().to_dict() if len(ctrl) else {}}


def komposisi(vals, kurangi=None):
    """Komposisi saham asing tanpa warkat per klasifikasi, dalam persen; `kurangi` = porsi pengendali per kelas."""
    kurangi = kurangi or {}
    bersih = {n: max(0.0, v - kurangi.get(n.lower(), 0.0)) for n, v in vals.items()}
    tot = sum(bersih.values())
    if tot <= 0:
        return []
    top = sorted(((n, v) for n, v in bersih.items() if v > 0), key=lambda x: -x[1])
    return [[n, round(v / tot * 100, 2)] for n, v in top][:10]


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
    # tiga bulan sebelum bulan terakhir, untuk tren bulanan di detail saham (contoh: Jun, Jul, Agu)
    bln_tren = bulan[max(0, idx_now - 3):idx_now]

    # ---------------- pemegang di atas 1% ----------------
    bei = kenali_file_bei()
    p1_tgl, p1_path = pilih(bei["1pct"], bp_tgl)
    kl_tgl, kl_path = pilih(bei["klas"], bp_tgl)
    pemegang = baca_1pct(p1_path) if p1_path else {}
    # file 1% bulan pembanding, supaya perubahan asing non pengendali tepat
    pemegang_bln = {}
    for ym in bln_tren:
        cocok = [p for t, p in bei["1pct"] if t[:6] == ym.replace("-", "")]
        pemegang_bln[ym] = baca_1pct(cocok[-1]) if cocok else None
    # ---------------- klasifikasi 39 tipe (opsional) ----------------
    klas = {}
    if kl_path:
        k = baca_xlsx_header(kl_path, "SHARE_CODE")
        k["SHARE_CODE"] = k["SHARE_CODE"].astype(str).str.strip().str.upper()
        fcols = [c for c in k.columns if c.startswith("Foreign_")]
        for _, r in k.iterrows():
            vals = {re.sub(r"\s*\([A-Z]{2}\)$", "", c.replace("Foreign_", "").strip()): num(r[c]) for c in fcols}
            if sum(vals.values()) > 0:
                klas[r["SHARE_CODE"]] = vals

    # ---------------- HSC dan sektor ----------------
    with open("hsc.json", encoding="utf-8") as f:
        hsc = json.load(f)
    hsc_aktif = {x["kode"]: x for x in hsc["aktif"]}
    sektor = {}
    sk_path = next((p for p in ["data_src/sektor.csv", "sektor.csv"] if os.path.exists(p)), None)
    if sk_path:
        s = pd.read_csv(sk_path, dtype=str)
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
        n1 = ff = None
        nama = None
        pa = pecah_asing(g)
        if g is not None:
            f_scrip = pa["scrip"]
            l_scrip = g.loc[g["LOCAL_FOREIGN"] != "F", "HOLDINGS_SCRIP"].sum()
            nama = str(g["ISSUER_NAME"].iloc[0])
            # sama dengan dashboard KSEI Ownership: free float = 100 dikurangi jumlah persentase
            # SEMUA baris pemegang di atas 1% (warkat dan tanpa warkat); n1 = jumlah baris pemegang
            n1 = int(len(g))
            ff = round(max(0.0, 100 - sum(int(round(x * 100)) for x in g["PERCENTAGE"]) / 100), 2)
            gf = g[g["LOCAL_FOREIGN"] == "F"].sort_values("PERCENTAGE", ascending=False)
            holders_f = [[str(x.INVESTOR_NAME), str(x.INVESTOR_CLASSIFICATION) if pd.notna(x.INVESTOR_CLASSIFICATION) else "",
                          str(x.NATIONALITY) if pd.notna(x.NATIONALITY) and str(x.NATIONALITY) != "nan" else (str(x.DOMICILE) if pd.notna(x.DOMICILE) else ""),
                          round(x.PERCENTAGE, 2), 1 if x.HOLDINGS_SCRIP > 0 else 0,
                          1 if x.INVESTOR_NAME in pa["nama_ctrl"] else 0] for x in gf.itertuples()]
        f_sl_tot = sum(f_sl.values())
        asing = f_sl_tot + f_scrip
        non_ctrl = max(0.0, f_sl_tot - pa["ctrl_sl"]) + max(0.0, f_scrip - pa["ctrl_sc"])
        scripless_tot = sum(f_sl.values()) + sum(l_sl.values())
        tercatat = scripless_tot + f_scrip + l_scrip
        sk = sektor.get(kode)
        nama = nama or (sk.nama if sk is not None and isinstance(sk.nama, str) else kode)

        a_now, an_now = round(asing / sec * 100, 2), round(non_ctrl / sec * 100, 2)

        def posisi(ym):
            """Porsi asing akhir bulan ym (termasuk dan non pengendali), persen dari total saham
            saat ini, supaya selisihnya murni perubahan jumlah saham asing."""
            v = asing_bulan(kode, ym) if ym else None
            if v is None:
                return None
            bln_pem = pemegang_bln.get(ym)
            pm = pecah_asing(bln_pem.get(kode)) if bln_pem is not None else pa
            a_ = v + pm["scrip"]
            an_ = max(0.0, v - pm["ctrl_sl"]) + max(0.0, pm["scrip"] - pm["ctrl_sc"])
            return round(a_ / sec * 100, 2), round(an_ / sec * 100, 2)

        tren = {ym: posisi(ym) for ym in bln_tren}

        def chg(ym, hanya_non_ctrl):
            """Perubahan porsi asing (poin persen dari total saham) dibanding akhir bulan ym."""
            x = tren.get(ym) if ym in tren else posisi(ym)
            if x is None:
                return None
            return round((an_now - x[1]) if hanya_non_ctrl else (a_now - x[0]), 2)

        h = hsc_aktif.get(kode)
        row = {
            "k": kode, "n": nama,
            "s": (sk.sektor if sk is not None and isinstance(sk.sektor, str) else ""),
            "i": (sk.industri if sk is not None and isinstance(sk.industri, str) else ""),
            "p": r["Price"], "mc": round(sec * r["Price"] / 1e12, 3),
            "a": a_now,
            "aw": round(f_scrip / sec * 100, 2),
            "ab": {t: round(v / sec * 100, 3) for t, v in f_sl.items() if v > 0},
            "rt": round(l_sl["ID"] / sec * 100, 2),
            # total tanpa warkat (Balancepos), untuk grafik versi KSEI
            "fsl": round(f_sl_tot / sec * 100, 3), "lsl": round(sum(l_sl.values()) / sec * 100, 3),
            "an": an_now,
            "c1": chg(bln_1, False), "c3": chg(bln_3, False),
            "cn1": chg(bln_1, True), "cn3": chg(bln_3, True),
            "ff": ff, "n1": n1,
            "tr": [[ym] + list(x) for ym, x in tren.items() if x is not None] + [[bln_now, a_now, an_now]],
            "cv": round(min(tercatat / sec, 9.99), 3),
            "hf": holders_f,
        }
        if pa["ctrl"]:
            row["pg"] = pa["ctrl"]
        if h:
            row["h"] = {"p": h["persen"], "pos": h.get("posisi_per"), "sj": h["sejak"], "u": h["link"]}
        if kode in klas:
            row["kl"] = komposisi(klas[kode])
            if pa["ctrl_kelas"]:
                row["kln"] = komposisi(klas[kode], pa["ctrl_kelas"])
        rows.append(row)

    def agregat(rs):
        mc = sum(r["mc"] for r in rs) or 1
        def w(k):
            vals = [(r[k], r["mc"]) for r in rs if r.get(k) is not None]
            return round(sum(v * m for v, m in vals) / mc, 2)
        return {"n": len(rs), "mc": round(mc, 1), "a": w("a"), "an": w("an"),
                "rp_a": round(sum(r["a"] / 100 * r["mc"] for r in rs), 1),
                "rp_an": round(sum(r["an"] / 100 * r["mc"] for r in rs), 1),
                "c1": w("c1"), "cn1": w("cn1"), "c3": w("c3"), "cn3": w("cn3")}

    meta = {
        "agregat": {"semua": agregat(rows), "non_hsc": agregat([r for r in rows if "h" not in r])},
        "aturan_pengendali": {"batas": BATAS_PENGENDALI, "batas_individu": BATAS_INDIVIDU},
        "pembanding_1pct": {ym: (v is not None) for ym, v in pemegang_bln.items()},
        "dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H.%M WIB"),
        "balancepos": iso(bp_tgl),
        "satu_persen": iso(p1_tgl) if p1_path else None,
        "klasifikasi": iso(kl_tgl) if kl_path else None,
        "hsc_terakhir": hsc["pengumuman_terakhir"], "hsc_diambil": hsc["diambil"],
        "hsc_jumlah": hsc["jumlah_aktif"],
        "banding_1": bln_1, "banding_3": bln_3, "bulan_tren": bln_tren + [bln_now],
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
