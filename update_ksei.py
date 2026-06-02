import pandas as pd
import json
import os
import glob

# 1. Cari file TXT KSEI terbaru di dalam folder
txt_files = glob.glob('Balancepos*.txt')
if not txt_files:
    print("❌ File Balancepos KSEI (.txt) tidak ditemukan di folder ini!")
    exit()

# Ambil file terbaru
latest_txt = max(txt_files, key=os.path.getmtime)
print(f"Membaca data dari: {latest_txt}")

# 2. Ambil bulan dan tahun dari nama file (Asumsi nama format: Balancepos20260630.txt)
# Mengambil '2026' dan '06'
tahun = latest_txt[-12:-8]
bulan = latest_txt[-8:-6]
tanggal_format = f"{tahun}-{bulan}"
print(f"Memproses untuk periode: {tanggal_format}")

# 3. Baca data TXT KSEI
try:
    df = pd.read_csv(latest_txt, sep='|', low_memory=False)
    df = df[df['Type'] == 'EQUITY'] # Hanya ambil data Saham
except Exception as e:
    print(f"❌ Gagal membaca file TXT: {e}")
    exit()

cols = ['Local IS', 'Local CP', 'Local PF', 'Local IB', 'Local ID', 'Local MF', 'Local SC', 'Local FD', 'Local OT', 
        'Foreign IS', 'Foreign CP', 'Foreign PF', 'Foreign IB', 'Foreign ID', 'Foreign MF', 'Foreign SC', 'Foreign FD', 'Foreign OT']

for c in cols:
    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(float)

# 4. Buka database lama
db_file = 'data.json'
if os.path.exists(db_file):
    with open(db_file, 'r') as f:
        db = json.load(f)
else:
    db = {}

# 5. Suntikkan data baru
update_count = 0
for index, row in df.iterrows():
    code = str(row['Code']).strip().upper()
    if len(code) < 4: continue
    
    record = {'Date': tanggal_format}
    for c in cols:
        record[c] = row[c]
        
    if code not in db:
        db[code] = []
        
    existing_dates = [r['Date'] for r in db[code]]
    if tanggal_format not in existing_dates:
        db[code].append(record)
        update_count += 1
    else:
        idx = existing_dates.index(tanggal_format)
        db[code][idx] = record
        
    db[code] = sorted(db[code], key=lambda x: x['Date'])

# 6. Simpan kembali
with open(db_file, 'w') as f:
    json.dump(db, f)
    
print(f"✅ Berhasil! {update_count} emiten telah diupdate ke dalam data.json.")
print("Silakan push data.json terbaru ke Github Anda.")
