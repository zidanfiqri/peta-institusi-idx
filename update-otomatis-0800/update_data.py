"""
update_data.py
Satu perintah untuk memperbarui semua data peta-institusi-idx dari sumber resmi.

    python update_data.py              cek semua sumber, update yang baru, lalu commit dan push
    python update_data.py --no-push    sama, tapi tanpa git
    python update_data.py --only hsc   hanya satu sumber: hsc, ksei, atau idx (boleh dipisah koma)
    python update_data.py --show       tampilkan jendela browser (pakai ini kalau mode tersembunyi diblokir)

Sumber yang dicek:
  1. KSEI  Balancepos bulanan (zip)           -> data.json, emiten_info.json
  2. BEI   Pemegang saham di atas 1% (xlsx)    -> data_src/1pct/
           Klasifikasi investor 39 tipe (xlsx) -> data_src/klasifikasi/
  3. BEI   Pengumuman HSC beserta PDF          -> data_src/bei_hsc_pengumuman.csv, hsc.json

Skrip ini aman dijalankan berulang kali: file yang sudah ada dilewati, jadi kalau tidak ada
data baru skrip selesai dalam hitungan detik. Situs BEI dilindungi Cloudflare, sehingga skrip
membuka situs BEI lewat Google Chrome yang terpasang di komputer (Playwright, channel "chrome").

Instal sekali:
    pip install -r requirements.txt
"""
import argparse
import base64
import csv
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

WIB = timezone(timedelta(hours=7))
BULAN = {"januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7,
         "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12}

KSEI_LIST = "https://www.ksei.co.id/id/publikasi/data-dan-statistik/kepemilikan-efek"
KSEI_ZIP = "https://www.ksei.co.id/storage/Download/BalanceposEfek{}.zip"
IDX_HOME = "https://www.idx.co.id/id/"
IDX_LIST = "/primary/list/id/perusahaan-tercatat/{}/?lang=id&start=0&length=9999&year={}"
IDX_MEDIA = "https://www.idx.co.id/Media/"

HSC_CSV = "data_src/bei_hsc_pengumuman.csv"
HSC_FIELDS = ["jenis", "kode", "tanggal", "persen", "posisi_per", "catatan", "file"]
BP_DIR = "data_src/balancepos"
DIR_1PCT = "data_src/1pct"
DIR_KLAS = "data_src/klasifikasi"
CACHE = ".cache"

BP_COLS = ["Local IS", "Local CP", "Local PF", "Local IB", "Local ID", "Local MF", "Local SC", "Local FD", "Local OT",
           "Foreign IS", "Foreign CP", "Foreign PF", "Foreign IB", "Foreign ID", "Foreign MF", "Foreign SC", "Foreign FD", "Foreign OT"]

LOG = []


def log(msg):
    line = f"[{datetime.now(WIB).strftime('%H.%M.%S')}] {msg}"
    print(line, flush=True)
    LOG.append(line)


# --------------------------------------------------------------------------------------------
# Bantuan teks
# --------------------------------------------------------------------------------------------
def tanggal_indo(teks):
    """'30 September 2026' atau '2 8 Sep tember 2026' (spasi dari PDF) -> '2026-09-30'."""
    s = re.sub(r"\s+", "", teks or "")
    m = re.search(r"(\d{1,2})([A-Za-z]+?)(\d{4})", s)
    if not m or m.group(2).lower() not in BULAN:
        return None
    return f"{m.group(3)}-{BULAN[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def baca_hsc_pdf(teks):
    """Ambil persen konsentrasi dan tanggal posisi dari teks PDF pengumuman HSC BEI."""
    t = re.sub(r"\s+", " ", teks or "")
    persen = None
    m = re.search(r"menguasai\s*([\d\s.,]+?)\s*%", t, re.I)
    if m:
        try:
            persen = float(re.sub(r"\s", "", m.group(1)).replace(",", "."))
        except ValueError:
            persen = None
    posisi = None
    k = re.search(r"per tanggal", t, re.I)
    if k:
        posisi = tanggal_indo(t[k.end():k.end() + 40])
    return persen, posisi


def kode_dari_judul(judul):
    m = re.search(r"\(([A-Z0-9]{4})\)\s*$", judul.strip()) or re.search(r"\(([A-Z0-9]{4})\)", judul)
    return m.group(1) if m else None


# --------------------------------------------------------------------------------------------
# Browser BEI (Playwright + Chrome terpasang)
# --------------------------------------------------------------------------------------------
class BrowserBEI:
    def __init__(self, show=False):
        self.show = show
        self.pw = None
        self.ctx = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        profil = os.path.join(CACHE, "chrome-profile")
        os.makedirs(profil, exist_ok=True)
        kwargs = dict(user_data_dir=profil, headless=not self.show, accept_downloads=True)
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception:
            # Chrome tidak ditemukan: pakai Chromium bawaan Playwright (perlu: python -m playwright install chromium)
            self.ctx = self.pw.chromium.launch_persistent_context(**kwargs)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto(IDX_HOME, wait_until="domcontentloaded", timeout=90000)
        self.page.wait_for_timeout(4000)
        return self

    def __exit__(self, *a):
        try:
            self.ctx.close()
        finally:
            self.pw.stop()

    def json(self, path):
        hasil = self.page.evaluate(
            """async (p) => { const r = await fetch(p, {credentials: 'include'});
                 const t = await r.text(); return {status: r.status, text: t}; }""", path)
        if hasil["status"] != 200 or not hasil["text"].lstrip().startswith("{"):
            raise RuntimeError(f"BEI menolak permintaan ({hasil['status']}). Coba jalankan ulang dengan --show.")
        return json.loads(hasil["text"])

    def unduh(self, url):
        b64 = self.page.evaluate(
            """async (u) => { const r = await fetch(u, {credentials: 'include'});
                 if (!r.ok) throw new Error('HTTP ' + r.status);
                 const b = new Uint8Array(await r.arrayBuffer()); let s = '';
                 for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
                 return btoa(s); }""", url)
        return base64.b64decode(b64)


def daftar_idx(bei, halaman):
    """Semua item daftar BEI (tahun 2026 s.d. tahun berjalan)."""
    items = []
    for th in range(2026, date.today().year + 1):
        items += bei.json(IDX_LIST.format(halaman, th)).get("items", [])
    return items


# --------------------------------------------------------------------------------------------
# 1. HSC dari BEI
# --------------------------------------------------------------------------------------------
def update_hsc(bei):
    with open(HSC_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    dikenal = {r["file"] for r in rows}
    items = daftar_idx(bei, "kepemilikan-saham-terkonsentrasi-tinggi")
    baru = []
    for it in items:
        url = it.get("Prospectus") or ""
        file = url.replace(IDX_MEDIA, "").replace("https://www.idx.co.id/media/", "")
        if not file or file in dikenal:
            continue
        judul = it.get("Description") or ""
        jenis = judul.split(" ")[0]
        if jenis not in ("Pengenaan", "Pencabutan"):
            log(f"HSC: jenis pengumuman tidak dikenal, dilewati: {judul}")
            continue
        kode = kode_dari_judul(judul)
        if not kode:
            log(f"HSC: kode saham tidak terbaca, dilewati: {judul}")
            continue
        pdf = bei.unduh(url)
        os.makedirs(os.path.join(CACHE, "hsc_pdf"), exist_ok=True)
        with open(os.path.join(CACHE, "hsc_pdf", os.path.basename(file)), "wb") as f:
            f.write(pdf)
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            teks = " ".join((p.extract_text() or "") for p in doc.pages)
        persen, posisi = baca_hsc_pdf(teks)
        catatan = ""
        if jenis == "Pengenaan" and persen is None:
            catatan = "Persen tidak terbaca otomatis, cek PDF"
        elif jenis == "Pengenaan" and posisi is None:
            catatan = "Tanggal posisi tidak tertulis di PDF, cek manual"
        baru.append({"jenis": jenis, "kode": kode, "tanggal": (it.get("ListingDate") or "")[:10],
                     "persen": "" if persen is None else f"{persen:.2f}", "posisi_per": posisi or "",
                     "catatan": catatan, "file": file})
        log(f"HSC baru: {jenis} {kode} {baru[-1]['tanggal']} {baru[-1]['persen']}% {catatan}")
    if not baru:
        log(f"HSC: tidak ada pengumuman baru ({len(items)} pengumuman di BEI).")
        return False
    rows = sorted(rows + baru, key=lambda r: r["tanggal"], reverse=True)
    with open(HSC_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HSC_FIELDS)
        w.writeheader()
        w.writerows(rows)
    import update_hsc
    if any(r["jenis"] == "Pengenaan" and not r["persen"] for r in baru):
        log("HSC: ada persen yang belum terbaca. Isi kolom persen di CSV, lalu jalankan: python update_hsc.py")
        return True
    update_hsc.main()
    return True


# --------------------------------------------------------------------------------------------
# 2. File bulanan BEI: pemegang 1% dan klasifikasi investor
# --------------------------------------------------------------------------------------------
def update_idx_bulanan(bei):
    items = daftar_idx(bei, "data-kepemilikan-saham")
    target = [(r"Pemegang Saham di Atas 1% per (.+)$", DIR_1PCT, "satu_persen_{}.xlsx"),
              (r"Berdasarkan Klasifikasi Investor per (.+)$", DIR_KLAS, "klasifikasi_{}.xlsx")]
    ada_baru = False
    for it in items:
        judul = (it.get("Description") or "").strip()
        for pola, folder, nama in target:
            m = re.search(pola, judul, re.I)
            if not m:
                continue
            tgl = tanggal_indo(m.group(1))
            if not tgl:
                continue
            path = os.path.join(folder, nama.format(tgl.replace("-", "")))
            if os.path.exists(path):
                continue
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(bei.unduh(it["Prospectus"]))
            log(f"BEI baru: {path}")
            ada_baru = True
    if not ada_baru:
        log("BEI bulanan: file 1% dan klasifikasi sudah terbaru.")
    return ada_baru


# --------------------------------------------------------------------------------------------
# 3. Balancepos KSEI
# --------------------------------------------------------------------------------------------
def _http_get(url, timeout=60):
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    return r


def balancepos_tersedia():
    """Tanggal file (YYYYMMDD) yang tercantum di halaman KSEI."""
    try:
        html = _http_get(KSEI_LIST).text
        return sorted(set(re.findall(r"BalanceposEfek(\d{8})\.zip", html)))
    except Exception as e:
        log(f"KSEI: halaman daftar tidak bisa dibuka ({e}); mencoba tebak tanggal akhir bulan.")
        return []


def cari_balancepos_bulan(ym, daftar):
    """Cari file Balancepos untuk bulan 'YYYY-MM'. Kembalikan (tanggal, bytes txt) atau None."""
    tahun, bulan = map(int, ym.split("-"))
    kandidat = [d for d in daftar if d.startswith(f"{tahun}{bulan:02d}")]
    if not kandidat:
        akhir = (date(tahun + (bulan == 12), bulan % 12 + 1, 1) - timedelta(days=1))
        kandidat = [(akhir - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]
    for d in sorted(kandidat, reverse=True):
        lokal = os.path.join(BP_DIR, f"Balancepos{d}.txt")
        if os.path.exists(lokal):
            return d, open(lokal, "rb").read()
        try:
            r = _http_get(KSEI_ZIP.format(d))
        except Exception:
            continue
        if r.status_code != 200 or r.content[:2] != b"PK":
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            nama = next((n for n in z.namelist() if n.lower().endswith(".txt")), None)
            if not nama:
                continue
            isi = z.read(nama)
        os.makedirs(BP_DIR, exist_ok=True)
        with open(lokal, "wb") as f:
            f.write(isi)
        log(f"KSEI baru: {lokal}")
        return d, isi
    return None


def baca_balancepos(isi):
    import pandas as pd
    df = pd.read_csv(io.BytesIO(isi), sep="|", low_memory=False)
    df = df[df["Type"] == "EQUITY"].copy()
    for c in BP_COLS + ["Sec. Num", "Price"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(float)
    df["Code"] = df["Code"].astype(str).str.strip().str.upper()
    return df[df["Code"].str.len() >= 4]


def masukkan_balancepos(db, df, ym):
    """Sama persis dengan logika update_ksei.py, supaya index.html dan Smart Money tetap jalan."""
    n = 0
    for _, row in df.iterrows():
        code = row["Code"]
        rec = {"Date": ym}
        for c in BP_COLS:
            rec[c] = row[c]
        lst = db.setdefault(code, [])
        tgl = [r["Date"] for r in lst]
        if ym in tgl:
            lst[tgl.index(ym)] = rec
        else:
            lst.append(rec)
            n += 1
        lst.sort(key=lambda x: x["Date"])
    return n


def bulan_antara(a, b):
    y, m = map(int, a.split("-"))
    out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}")
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def update_ksei():
    with open("data.json") as f:
        db = json.load(f)
    ada = sorted({r["Date"] for v in db.values() for r in v})
    daftar = balancepos_tersedia()
    awal_bulan_ini = date.today().replace(day=1)
    bulan_lalu = (awal_bulan_ini - timedelta(days=1)).strftime("%Y-%m")
    # bulan bolong di tengah + bulan baru setelah data terakhir, s.d. bulan lalu
    perlu = [m for m in bulan_antara(ada[0], bulan_lalu) if m not in ada]
    if not perlu:
        log(f"KSEI: data.json sudah lengkap s.d. {ada[-1]}.")
    berubah = False
    for ym in perlu:
        hasil = cari_balancepos_bulan(ym, daftar)
        if not hasil:
            log(f"KSEI: file Balancepos {ym} belum tersedia di KSEI.")
            continue
        d, isi = hasil
        n = masukkan_balancepos(db, baca_balancepos(isi), ym)
        log(f"KSEI: {ym} dimasukkan ke data.json ({n} emiten baru di bulan itu).")
        berubah = True
    if berubah:
        with open("data.json", "w") as f:
            json.dump(db, f)
    terakhir = max(r["Date"] for v in db.values() for r in v)
    berubah |= tulis_emiten_info(terakhir, daftar)
    return berubah


def tulis_emiten_info(ym, daftar):
    """Sec. Num dan Price bulan terbaru untuk % asing dan market cap."""
    lama = None
    if os.path.exists("emiten_info.json"):
        with open("emiten_info.json") as f:
            lama = json.load(f)
        if lama.get("periode", "")[:7] >= ym:
            return False
    # pakai file lokal dulu (termasuk Balancepos*.txt lama di root), baru unduh
    lokal = sorted(glob.glob(f"Balancepos{ym.replace('-', '')}*.txt") + glob.glob(os.path.join(BP_DIR, f"Balancepos{ym.replace('-', '')}*.txt")))
    if lokal:
        d = re.search(r"(\d{8})", os.path.basename(lokal[-1])).group(1)
        isi = open(lokal[-1], "rb").read()
    else:
        hasil = cari_balancepos_bulan(ym, daftar)
        if not hasil:
            log(f"KSEI: emiten_info.json tidak diperbarui, file Balancepos {ym} tidak ditemukan.")
            return False
        d, isi = hasil
    df = baca_balancepos(isi)
    info = {"periode": f"{d[:4]}-{d[4:6]}-{d[6:]}", "sumber": "KSEI Balancepos " + d,
            "data": {r["Code"]: {"sec_num": int(r["Sec. Num"]), "price": float(r["Price"])} for _, r in df.iterrows()}}
    with open("emiten_info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    log(f"KSEI: emiten_info.json diperbarui untuk {info['periode']} ({len(info['data'])} emiten).")
    return True


# --------------------------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------------------------
GITIGNORE = [".cache/", "update_log.txt", "__pycache__/", "data_src/balancepos/"]


def pastikan_gitignore():
    """Buat atau lengkapi .gitignore supaya cache Chrome dan file mentah tidak ikut ke GitHub."""
    ada = open(".gitignore", encoding="utf-8").read().splitlines() if os.path.exists(".gitignore") else []
    kurang = [g for g in GITIGNORE if g not in ada]
    if kurang:
        with open(".gitignore", "a", encoding="utf-8") as f:
            f.write(("\n" if ada and ada[-1] else "") + "\n".join(kurang) + "\n")
        log(".gitignore dilengkapi: " + ", ".join(kurang))
    return bool(kurang)


def git_push():
    if not shutil.which("git") or not os.path.isdir(".git"):
        log("Git tidak ditemukan atau folder ini bukan hasil git clone. Upload file yang berubah secara manual.")
        return
    jalur = [".gitignore", "data.json", "emiten_info.json", "hsc.json", HSC_CSV, DIR_1PCT, DIR_KLAS]
    jalur = [j for j in jalur if os.path.exists(j)]
    subprocess.run(["git", "add", *jalur], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        log("Git: tidak ada perubahan baru.")
        return
    pesan = f"Update data otomatis {datetime.now(WIB).strftime('%Y-%m-%d')}"
    subprocess.run(["git", "commit", "-m", pesan], check=True)
    r = subprocess.run(["git", "push"], capture_output=True, text=True)
    if r.returncode == 0:
        log("Git: commit dan push berhasil.")
    else:
        log("Git: push gagal. Pesan: " + (r.stderr or r.stdout).strip()[-300:])


# --------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="ksei,idx,hsc")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    pilih = {x.strip() for x in a.only.split(",")}
    log(f"Mulai update: {', '.join(sorted(pilih))}")
    berubah = pastikan_gitignore()
    gagal = []
    if "ksei" in pilih:
        try:
            berubah |= update_ksei()
        except Exception as e:
            gagal.append("ksei")
            log(f"KSEI gagal: {e}")
    if pilih & {"idx", "hsc"}:
        # mode tersembunyi dulu; kalau Cloudflare menolak, ulangi sekali dengan jendela Chrome terlihat
        for tampil in ([a.show] if a.show else [False, True]):
            try:
                with BrowserBEI(show=tampil) as bei:
                    if "idx" in pilih:
                        berubah |= update_idx_bulanan(bei)
                    if "hsc" in pilih:
                        berubah |= update_hsc(bei)
                break
            except Exception as e:
                log(f"BEI gagal{' (jendela terlihat)' if tampil else ' (mode tersembunyi)'}: {e}")
                if tampil:
                    gagal.append("bei")
    if berubah and not a.no_push:
        git_push()
    log("Selesai." + (f" Ada yang gagal: {', '.join(gagal)}." if gagal else ""))
    with open("update_log.txt", "a", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n\n")
    sys.exit(1 if gagal else 0)


if __name__ == "__main__":
    main()
