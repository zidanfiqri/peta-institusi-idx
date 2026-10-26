# Daftarkan update otomatis harian jam 08.00 di Windows Task Scheduler.
# Kalau komputer mati atau tidur pada jam itu, update dijalankan begitu komputer menyala lagi.
$nama = 'Update Peta Institusi IDX'
$bat = Join-Path $PSScriptRoot 'jalankan_update.bat'
$aksi = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c ""{0}" auto"' -f $bat) -WorkingDirectory $PSScriptRoot
$pemicu = New-ScheduledTaskTrigger -Daily -At '08:00'
$atur = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $nama -Action $aksi -Trigger $pemicu -Settings $atur -Description 'Cek data baru KSEI dan BEI, update repo peta-institusi-idx' -Force | Out-Null
Write-Host "Jadwal terpasang: '$nama' setiap hari jam 08.00."
