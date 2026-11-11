"""Bangun dana.json (halaman Dana di Pasar) dan eido.json (halaman Portofolio ETF MSCI Indonesia).

Dana di Pasar
    Nilai kepemilikan = lembar per tipe investor (data.json, dari Balancepos KSEI) x harga
    penutupan akhir bulan (data_src/harga_akhir_bulan.csv, dari ringkasan saham BEI atau kolom
    Price Balancepos). Nilai asing juga dalam USD memakai kurs akhir bulan
    (data_src/kurs_usdidr.csv, kurs referensi ECB via api.frankfurter.app).

    Perubahan nilai antar-bulan dipecah per saham per tipe investor:
        V1 - V0 = (S1 - S0) x (P1 + P0)/2   -> aliran dana (beli/jual bersih, estimasi)
                + (P1 - P0) x (S1 + S0)/2   -> efek harga
    Pemecah saham (split/reverse split) dikoreksi supaya tidak terbaca sebagai aliran dana.

Portofolio ETF MSCI Indonesia
    Portofolio iShares MSCI Indonesia ETF (EIDO) dari laporan N-PORT di SEC EDGAR
    (data_src/eido_nport.csv). Ini portofolio dana, bukan data indeks MSCI.

Jalankan: python build_dana.py
"""
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
TIPE = ["IS", "CP", "PF", "IB", "ID", "MF", "SC", "FD", "OT"]
KOLOM = [f"Local {t}" for t in TIPE] + [f"Foreign {t}" for t in TIPE]
HARGA_CSV = "data_src/harga_akhir_bulan.csv"
KURS_CSV = "data_src/kurs_usdidr.csv"
EIDO_CSV = "data_src/eido_nport.csv"
EIDO_MAP = "data_src/eido_isin_kode.json"


SUMBER_LISTED = {}   # (bulan, kode) -> "bei" (ringkasan saham BEI) atau "ksei" (Sec. Num Balancepos)


def baca_harga():
    harga, listed, tanggal, nama = defaultdict(dict), defaultdict(dict), {}, {}
    with open(HARGA_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m, k = r["bulan"], r["kode"]
            tanggal[m] = r["tanggal"]
            p = float(r["close"] or 0)
            if p > 0:
                harga[m][k] = p
            if r.get("listed_shares"):
                listed[m][k] = float(r["listed_shares"])
                SUMBER_LISTED[(m, k)] = "bei" if r.get("nama") else "ksei"
            if r.get("nama"):
                nama[k] = r["nama"]
    # saham yang disuspensi tidak muncul di ringkasan BEI: pakai harga terakhir yang diketahui
    # (maks. 12 bulan) supaya kepemilikannya di KSEI tetap ternilai dan tidak muncul sebagai arus keluar
    terakhir, umur = {}, {}
    for m in sorted(harga):
        for k, p in harga[m].items():
            terakhir[k], umur[k] = p, 0
        for k in list(terakhir):
            if k not in harga[m]:
                umur[k] += 1
                if umur[k] <= 12:
                    harga[m][k] = terakhir[k]
                    lm = [listed[x][k] for x in sorted(listed) if x < m and k in listed[x]]
                    if lm:
                        listed[m].setdefault(k, lm[-1])
    return harga, listed, tanggal, nama


def baca_kurs():
    kurs = {}
    with open(KURS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kurs[r["tanggal"][:7]] = float(r["usd_idr"])
    return kurs


def aksi_korporasi(k, m0, m1, listed, harga, h0, h1):
    """Kembalikan (rasio_split, ada_penerbitan).

    rasio_split: pemecahan/penggabungan saham, dideteksi dari perubahan jumlah saham tercatat
    (atau, bila data tercatat BEI belum diperbarui, dari total kepemilikan KSEI) yang disertai
    gerak harga berlawanan arah. Lembar dan harga bulan lalu disesuaikan dengan rasio ini.
    ada_penerbitan: jumlah saham tercatat berubah di luar split (rights issue, private placement,
    merger, buyback). Perubahan nilai saham ini dicatat terpisah sebagai aksi korporasi, bukan
    aliran dana perdagangan.
    """
    l0, l1 = listed.get(m0, {}).get(k), listed.get(m1, {}).get(k)
    if SUMBER_LISTED.get((m0, k)) != SUMBER_LISTED.get((m1, k)):
        l0 = l1 = None   # sumber jumlah saham berbeda (BEI vs KSEI): jangan dibandingkan langsung
    p0, p1 = harga.get(m0, {}).get(k), harga.get(m1, {}).get(k)
    if not (p0 and p1):
        return 1.0, False
    lq = math.log(p1 / p0)
    r, dari_listed = 1.0, False
    if l0 and l1 and abs(math.log(l1 / l0)) > math.log(1.2):
        r, dari_listed = l1 / l0, True
    elif h0 > 0 and h1 > 0 and abs(math.log(h1 / h0)) > math.log(1.2):
        r = h1 / h0
    split = 1.0
    if r != 1.0 and lq * math.log(r) < 0 and abs(lq) >= 0.5 * abs(math.log(r)):
        split = r
    terbit = bool(l0 and l1 and abs(math.log(l1 / (l0 * split))) > 0.02) if (dari_listed or split == 1.0) else False
    return split, terbit


def dana():
    with open("data.json", encoding="utf-8") as f:
        db = json.load(f)
    harga, listed, tanggal, nama = baca_harga()
    kurs = baca_kurs()
    lembar = defaultdict(dict)                       # bulan -> kode -> {kolom: lembar}
    for k, rows in db.items():
        for r in rows:
            lembar[r["Date"]][k] = r
    bulan = sorted(m for m in lembar if m in harga)
    hilang_harga = sorted(m for m in lembar if m not in harga)

    total, aliran, cakupan = {}, {}, {}
    for m in bulan:
        t = defaultdict(float)
        nilai_saham = 0.0
        for k, r in lembar[m].items():
            p = harga[m].get(k)
            if not p:
                continue
            for c in KOLOM:
                t[c] += r.get(c, 0) * p
            if listed[m].get(k):
                nilai_saham += listed[m][k] * p
        total[m] = {c: round(t[c]) for c in KOLOM}
        cakupan[m] = round(nilai_saham)

    per_saham_akhir = {}
    for i in range(1, len(bulan)):
        m0, m1 = bulan[i - 1], bulan[i]
        fl = defaultdict(float)
        ef = defaultdict(float)
        ak = defaultdict(float)
        rk = defaultdict(float)
        kv = defaultdict(float)
        daftar_ak, daftar_rk, daftar_kv = [], [], []
        kontrib = []
        for k in set(lembar[m0]) | set(lembar[m1]):
            p0, p1 = harga[m0].get(k), harga[m1].get(k)
            if not p0 and not p1:
                continue
            p0 = p0 or p1
            p1 = p1 or p0
            r0, r1 = lembar[m0].get(k, {}), lembar[m1].get(k, {})
            h0 = sum(r0.get(c, 0) for c in KOLOM)
            h1 = sum(r1.get(c, 0) for c in KOLOM)
            r, terbit = aksi_korporasi(k, m0, m1, listed, harga, h0, h1)
            s_fl = {}
            for c in KOLOM:
                s0, s1 = r0.get(c, 0) * r, r1.get(c, 0)
                q0 = p0 / r
                f = (s1 - s0) * (p1 + q0) / 2
                e = (p1 - q0) * (s1 + s0) / 2
                if terbit:
                    ak[c] += f
                else:
                    fl[c] += f
                ef[c] += e
                s_fl[c] = (0.0 if terbit else f, e)
            # reklasifikasi tipe/status investor (mis. investor individu pindah kewarganegaraan atau
            # kepemilikan dialihkan ke perusahaan miliknya): satu kolom berkurang dan kolom lain bertambah
            # dalam jumlah hampir persis sama (selisih <= 0,2%) dan bernilai >= Rp1 T, antar-tipe di sisi yang sama
            # (lokal/asing) atau tipe yang sama lintas sisi. Ini bukan uang masuk/keluar.
            if not terbit:
                pm = (p1 + p0 / r) / 2
                delta = {c: r1.get(c, 0) - r0.get(c, 0) * r for c in KOLOM}
                turun = sorted((c for c in KOLOM if delta[c] < 0), key=lambda c: delta[c])
                naik = sorted((c for c in KOLOM if delta[c] > 0), key=lambda c: -delta[c])
                dipakai = set()
                for ct in turun:
                    for cn in naik:
                        if cn in dipakai:
                            continue
                        dt, dn = -delta[ct], delta[cn]
                        sisi_sama = ct.split()[0] == cn.split()[0]
                        tipe_sama = ct.split()[1] == cn.split()[1]
                        if not (sisi_sama or tipe_sama):
                            continue   # mis. asing-reksadana -> lokal-individual: lebih mungkin transaksi biasa
                        if abs(dt - dn) <= 0.002 * max(dt, dn) and min(dt, dn) * pm >= 1e12:
                            x = min(dt, dn) * pm
                            fl[cn] -= x
                            fl[ct] += x
                            rk[cn] += x
                            rk[ct] -= x
                            dipakai.add(cn)
                            daftar_rk.append({"k": k, "dari": ct, "ke": cn, "nilai": round(x)})
                            s_fl[cn] = (s_fl[cn][0] - x, s_fl[cn][1])
                            s_fl[ct] = (s_fl[ct][0] + x, s_fl[ct][1])
                            break
            if r != 1.0 or terbit:
                daftar_ak.append({"k": k, "split": round(r, 4), "terbit": terbit,
                                  "nilai": round(sum((r1.get(c, 0) - r0.get(c, 0) * r) * (p1 + p0 / r) / 2 for c in KOLOM))})
            # keluar/masuk pencatatan KSEI: total lembar di KSEI berubah tanpa perubahan saham tercatat
            # (konversi warkat <-> tanpa warkat, atau perpindahan pencatatan). Dalam perdagangan biasa
            # beli bersih asing = jual bersih lokal, sehingga selisih bersih >= Rp1 T dipisahkan dari aliran.
            if not terbit and r == 1.0:
                bersih = sum(s_fl[c][0] for c in KOLOM)
                if abs(bersih) >= 1e12:
                    sisi = max(("Local", "Foreign"), key=lambda z: sum(s_fl[f"{z} {t}"][0] for t in TIPE) * (1 if bersih > 0 else -1))
                    bobot = {t: max(0.0, s_fl[f"{sisi} {t}"][0] * (1 if bersih > 0 else -1)) for t in TIPE}
                    tb = sum(bobot.values())
                    if tb > 0:
                        for t in TIPE:
                            x = bersih * bobot[t] / tb
                            c = f"{sisi} {t}"
                            fl[c] -= x
                            kv[c] += x
                            s_fl[c] = (s_fl[c][0] - x, s_fl[c][1])
                        daftar_kv.append({"k": k, "sisi": "lokal" if sisi == "Local" else "asing", "nilai": round(bersih)})
            fa_k = sum(s_fl[f"Foreign {t}"][0] for t in TIPE)
            fl_k = sum(s_fl[f"Local {t}"][0] for t in TIPE)
            if abs(fa_k) + abs(fl_k) > 0:
                kontrib.append((k, fa_k, fl_k))
            if i == len(bulan) - 1:
                per_saham_akhir[k] = s_fl
        daftar_ak.sort(key=lambda x: -abs(x["nilai"]))
        aliran[m1] = {"dari": m0, "aliran": {c: round(fl[c]) for c in KOLOM},
                      "efek_harga": {c: round(ef[c]) for c in KOLOM},
                      "aksi_korporasi": {c: round(ak[c]) for c in KOLOM},
                      "daftar_aksi_korporasi": daftar_ak[:15],
                      "reklasifikasi": {c: round(rk[c]) for c in KOLOM},
                      "daftar_reklasifikasi": daftar_rk,
                      "pencatatan": {c: round(kv[c]) for c in KOLOM},
                      "daftar_pencatatan": sorted(daftar_kv, key=lambda x: -abs(x["nilai"]))[:10],
                      "top": {
                          "asing_masuk": [[k, round(a)] for k, a, _ in sorted(kontrib, key=lambda x: -x[1])[:5] if a > 0],
                          "asing_keluar": [[k, round(a)] for k, a, _ in sorted(kontrib, key=lambda x: x[1])[:5] if a < 0],
                          "lokal_masuk": [[k, round(b)] for k, _, b in sorted(kontrib, key=lambda x: -x[2])[:5] if b > 0],
                          "lokal_keluar": [[k, round(b)] for k, _, b in sorted(kontrib, key=lambda x: x[2])[:5] if b < 0]}}

    # tabel per saham, bulan terakhir
    m1 = bulan[-1]
    saham = []
    for k, r in lembar[m1].items():
        p = harga[m1].get(k)
        if not p:
            continue
        asing = sum(r.get(f"Foreign {t}", 0) for t in TIPE) * p
        lokal = sum(r.get(f"Local {t}", 0) for t in TIPE) * p
        fa = per_saham_akhir.get(k, {})
        al_a = sum(fa.get(f"Foreign {t}", (0, 0))[0] for t in TIPE)
        al_l = sum(fa.get(f"Local {t}", (0, 0))[0] for t in TIPE)
        ef_a = sum(fa.get(f"Foreign {t}", (0, 0))[1] for t in TIPE)
        saham.append({"k": k, "n": nama.get(k, ""), "p": p, "va": round(asing), "vl": round(lokal),
                      "aa": round(al_a), "al": round(al_l), "ea": round(ef_a),
                      "mc": round(listed[m1].get(k, 0) * p)})
    saham.sort(key=lambda x: -x["va"])

    out = {
        "meta": {
            "dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H.%M WIB"),
            "bulan": bulan, "tanggal": {m: tanggal.get(m) for m in bulan},
            "kurs": {m: kurs.get(m) for m in bulan},
            "tanpa_harga": hilang_harga,
            "bulan_hilang": [],
            "kolom": KOLOM,
            "sumber": {
                "kepemilikan": "KSEI Balancepos (kepemilikan efek tanpa warkat per tipe investor)",
                "harga": "BEI ringkasan saham, harga penutupan hari bursa terakhir tiap bulan",
                "kurs": "Kurs referensi ECB USD/IDR akhir bulan (api.frankfurter.app)",
            },
        },
        "total": total, "nilai_saham_tercatat": cakupan, "aliran": aliran, "saham": saham,
    }
    # bulan yang tidak ada di data.json di antara bulan pertama dan terakhir
    a = datetime.strptime(bulan[0], "%Y-%m")
    while a.strftime("%Y-%m") < bulan[-1]:
        if a.strftime("%Y-%m") not in lembar:
            out["meta"]["bulan_hilang"].append(a.strftime("%Y-%m"))
        a = (a.replace(day=28) + timedelta(days=4)).replace(day=1)
    with open("dana.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return out


def eido(dana_out):
    if not os.path.exists(EIDO_CSV):
        return None
    peta = json.load(open(EIDO_MAP, encoding="utf-8")) if os.path.exists(EIDO_MAP) else {}
    harga, listed, _, _ = baca_harga()
    kurs = {}
    with open(KURS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kurs[r["tanggal"][:7]] = float(r["usd_idr"])
    per = defaultdict(list)
    nav = {}
    with open(EIDO_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            per[r["periode"]].append(r)
            nav[r["periode"]] = float(r["net_assets_usd"])
    periode = sorted(per)
    akhir, sebelum = periode[-1], (periode[-2] if len(periode) > 1 else None)
    lembar_sebelum = {r["isin"]: float(r["lembar"]) for r in per.get(sebelum, []) if r["kategori"] == "EC"}
    m_akhir = akhir[:7]
    kurs_akhir = kurs.get(m_akhir)
    # dana asing KSEI pada bulan yang sama dengan laporan N-PORT, supaya sebanding
    with open("data.json", encoding="utf-8") as f:
        db = json.load(f)
    bulan_ksei = max((m for m in {r["Date"] for v in db.values() for r in v} if m <= m_akhir), default=None)
    asing_lb = {}
    for kk, rws in db.items():
        for x in rws:
            if x["Date"] == bulan_ksei:
                asing_lb[kk] = sum(x.get(f"Foreign {t}", 0) for t in TIPE)
    rows = []
    for r in per[akhir]:
        if r["kategori"] != "EC":
            continue
        k = peta.get(r["isin"])
        ls = float(r["lembar"])
        l0 = lembar_sebelum.get(r["isin"], 0.0)
        p = float(r["nilai_usd"]) / ls if ls else 0
        tercatat = listed.get(m_akhir, {}).get(k) if k else None
        rows.append({
            "k": k, "n": r["nama"], "isin": r["isin"], "lb": ls, "usd": float(r["nilai_usd"]),
            "rp": float(r["nilai_usd"]) * kurs_akhir if kurs_akhir else None,
            "w": float(r["pct_nav"]), "dl": ls - l0, "baru": l0 == 0,
            "dusd": (ls - l0) * p,
            "pt": (ls / tercatat * 100) if tercatat else None,
            "asing_lb": asing_lb.get(k) if k else None,
            "asing_rp": asing_lb[k] * harga.get(bulan_ksei, {}).get(k, 0) if k and k in asing_lb else None,
            "porsi_asing": (ls / asing_lb[k] * 100) if k and asing_lb.get(k) else None,
        })
    rows.sort(key=lambda x: -x["usd"])
    keluar = [{"n": r["nama"], "isin": r["isin"], "k": peta.get(r["isin"]), "lb": float(r["lembar"])}
              for r in per.get(sebelum, []) if r["kategori"] == "EC"
              and r["isin"] not in {x["isin"] for x in per[akhir]}]
    out = {
        "meta": {"periode": periode, "akhir": akhir, "sebelum": sebelum,
                 "nav_usd": nav, "kurs": {p: kurs.get(p[:7]) for p in periode},
                 "bulan_asing_ksei": bulan_ksei,
                 "jumlah_saham": {p: sum(1 for r in per[p] if r["kategori"] == "EC") for p in periode},
                 "sumber": "SEC EDGAR, formulir N-PORT iShares Trust, seri S000028553 (iShares MSCI Indonesia ETF)",
                 "dibuat": datetime.now(WIB).strftime("%Y-%m-%d %H.%M WIB")},
        "holdings": rows, "keluar": keluar,
    }
    with open("eido.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return out


def main():
    d = dana()
    e = eido(d)
    m = d["meta"]["bulan"][-1]
    t = d["total"][m]
    asing = sum(v for c, v in t.items() if c.startswith("Foreign"))
    lokal = sum(v for c, v in t.items() if c.startswith("Local"))
    print(f"OK dana.json: {len(d['meta']['bulan'])} bulan s.d. {m}, lokal Rp{lokal/1e12:,.1f} T, "
          f"asing Rp{asing/1e12:,.1f} T" + (f"; eido.json: {len(e['holdings'])} saham per {e['meta']['akhir']}" if e else ""))


if __name__ == "__main__":
    main()
