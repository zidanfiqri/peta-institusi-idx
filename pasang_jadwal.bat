@echo off
REM Daftarkan update otomatis harian jam 08.00 di Windows Task Scheduler (cukup sekali).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pasang_jadwal.ps1"
if errorlevel 1 (
  echo Cara pertama gagal, mencoba cara cadangan...
  schtasks /create /tn "Update Peta Institusi IDX" /tr "\"%~dp0jalankan_update.bat\" auto" /sc daily /st 08:00 /f
)
echo.
echo Cek jadwal: buka Task Scheduler, cari "Update Peta Institusi IDX".
echo Hapus jadwal: schtasks /delete /tn "Update Peta Institusi IDX" /f
pause
