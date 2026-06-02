# peta-institusi-idx
### 🚀 Cara Pintar Update Data Bulan Selanjutnya (Juni, dst.)

Mengingat ke depannya Anda akan terus melakukan update rutin setiap bulan, Anda tidak perlu repot meminta bantuan atau membuka Excel lagi. Karena Anda memiliki Python (dilihat dari cara Anda menjalankan `py -m http.server`), saya buatkan satu **Skrip Robot Otomatis** (Python).

**Cara Kerja Robot:**

1. Bulan depan, saat data Juni keluar, cukup unduh file `Balancepos20260630.txt` dari web KSEI.
2. Masukkan file tersebut ke folder proyek Anda.
3. Buka terminal/CMD di folder tersebut dan ketik: `python update_ksei.py`
4. Dalam 2 detik, file `data.json` Anda otomatis terupdate dengan tambahan data bulan Juni, tanpa Excel sama sekali!
