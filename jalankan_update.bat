@echo off
REM Jalankan update semua data. Klik dua kali untuk menjalankan manual.
cd /d "%~dp0"
set "AUTO="
if /i "%~1"=="auto" (set "AUTO=1" & shift)
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py"
%PY% update_data.py %1 %2 %3
if not defined AUTO pause
