$targets = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($targets) {
  foreach ($t in $targets) {
    $procId = $t.OwningProcess
    Write-Output ("Releasing 8090 held by PID " + $procId)
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 800
} else {
  Write-Output "8090 not listening"
}