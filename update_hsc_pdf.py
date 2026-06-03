import pdfplumber
import json
import glob
import re

def extract_ksei_pdf():
    # Mencari semua file PDF di direktori yang sama
    pdf_files = glob.glob('*.pdf')
    if not pdf_files:
        print("❌ Tidak ada file PDF yang ditemukan di folder ini.")
        return

    investor_db = {}
    total_extracted = 0

    for file in pdf_files:
        # Deteksi kategori laporan dari nama file (1% atau 5%)
        kategori = ">1%" if "1%" in file else ">5%"
        print(f"Memproses file: {file} (Kategori: {kategori})")

        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                # Mengekstrak tabel dari setiap halaman PDF KSEI
                table = page.extract_table()
                if not table:
                    continue
                
                for row in table:
                    # Lewati baris header atau baris yang kosong
                    if not row or not row[0] or "Emiten" in str(row[0]) or "No." in str(row[0]):
                        continue
                    
                    try:
                        # Membersihkan baris dari enter (\n) yang sering terjadi di PDF KSEI
                        clean_row = [str(cell).replace('\n', ' ').strip() if cell else "" for cell in row]
                        
                        # Standar kolom tabel KSEI:
                        # [0] Nomor, [1] Kode Emiten, [2] Nama Pemegang Saham, 
                        # [3] Alamat (Opsional), [4] Jumlah Saham, [5] Persentase
                        
                        # Cari indeks yang mengandung persentase (biasanya ada tanda % atau angka desimal)
                        # Kita gunakan regex untuk mendeteksi struktur kolom yang dinamis
                        kode_emiten = clean_row[1]
                        
                        if len(kode_emiten) < 4: 
                            continue # Bukan kode emiten valid
                            
                        nama_investor = clean_row[2]
                        
                        # Ekstraksi jumlah saham (hilangkan titik pemisah ribuan)
                        jumlah_saham_str = re.sub(r'[^\d]', '', clean_row[-2]) if len(clean_row) >= 5 else "0"
                        jumlah_saham = int(jumlah_saham_str) if jumlah_saham_str else 0
                        
                        # Ekstraksi persentase
                        persen_str = clean_row[-1].replace('%', '').replace(',', '.').strip()
                        persen = float(persen_str) if persen_str.replace('.','',1).isdigit() else 0.0

                        # Analisis Tipe Identitas (Lokal/Asing)
                        tipe = "Lokal"
                        if "FOREIGN" in nama_investor.upper() or "LTD" in nama_investor.upper() or "LLC" in nama_investor.upper():
                            tipe = "Asing"
                        elif "PT " in nama_investor.upper() or "NEGARA" in nama_investor.upper():
                            tipe = "Lokal (Korporat/Institusi)"

                        if kode_emiten not in investor_db:
                            investor_db[kode_emiten] = []
                            
                        # Hindari duplikasi jika ada data yang sama (misal 5% pasti masuk di 1% juga)
                        existing_names = [inv['nama'] for inv in investor_db[kode_emiten]]
                        if nama_investor not in existing_names:
                            investor_db[kode_emiten].append({
                                "nama": nama_investor,
                                "tipe": tipe,
                                "jumlah": jumlah_saham,
                                "persen": persen,
                                "kategori": kategori
                            })
                            total_extracted += 1

                    except Exception as e:
                        # Abaikan baris yang formatnya rusak agar script tidak berhenti
                        continue
                        
    # Simpan ke file JSON
    with open('hsc_investors.json', 'w') as f:
        json.dump(investor_db, f, indent=4)
        
    print(f"✅ Selesai! Berhasil mengekstrak {total_extracted} data identitas pemegang saham.")
    print("File hsc_investors.json berhasil dibuat dan diperbarui.")

if __name__ == "__main__":
    extract_ksei_pdf()
