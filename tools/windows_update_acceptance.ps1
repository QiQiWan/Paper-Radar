# v1.16 Windows acceptance. Run from an elevated PowerShell only if needed to set up Python.
# This script runs offline failure-injection tests, signature verification,
# SQLite compatibility, and start/stop smoke checks. It does NOT publish to GitHub.
param([string]$Python = 'python', [switch]$Online)
$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$stamp=Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Force -Path 'reports' | Out-Null
$log="reports\windows_update_acceptance_$stamp.txt"
function Run-Check([string]$Label,[string[]]$Params){
  "=== $Label ===" | Out-File -Append -Encoding utf8 $log
  & $Python @Params 2>&1 | Out-File -Append -Encoding utf8 $log
  if($LASTEXITCODE -ne 0){throw "$Label FAILED (exit $LASTEXITCODE), inspect $log"}
}
if($env:OS -ne 'Windows_NT'){throw 'This acceptance must run on actual Windows.'}
Run-Check 'Python dependencies' @('-c','import cryptography,pypdf,docx,keyring; print("dependencies OK")')
Run-Check 'Signed release and recovery tests' @('-m','pytest','-q','tests/test_v116_release_security.py','tests/test_v115_startup_update.py')
Run-Check 'Database migrations and portal tests' @('-m','pytest','-q','tests/test_v116_portal_and_synthesis.py')
Run-Check 'Static checks' @('-m','compileall','-q','paper_radar','startup.py')
Run-Check 'Agent registration' @('-m','paper_radar.agents','doctor')
Run-Check 'Offline launch verification' @('-m','paper_radar.startup_update','--no-update','--check-only')
if($Online){
  Run-Check 'Real GitHub release inspection' @('-m','paper_radar.startup_update','--check-only')
}
"SUCCESS - Windows acceptance at $(Get-Date -Format s)" | Out-File -Append -Encoding utf8 $log
Write-Host "Windows acceptance passed: $log"
