$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' }
foreach ($p in $procs) {
  Write-Output ("Killing server.py PID " + $p.ProcessId + " (" + $p.Name + ")")
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800
$left = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($left) { Write-Output ("WARN: 8090 still held by " + $left.OwningProcess) } else { Write-Output "8090 FREE" }