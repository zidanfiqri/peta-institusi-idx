@echo off
REM Daftarkan update otomatis harian di Windows Task Scheduler (cukup sekali).
REM Jam bisa diganti di bagian /st, format 24 jam.
schtasks /create /tn "Update Peta Institusi IDX" /tr "\"%~dp0jalankan_update.bat\" auto" /sc daily /st 08:30 /f
if errorlevel 1 (
  echo Gagal mendaftarkan jadwal.
) else (
  echo Jadwal terpasang: setiap hari jam 08.30.
  echo Untuk menghapus: schtasks /delete /tn "Update Peta Institusi IDX" /f
)
pause
