$ErrorActionPreference = 'Stop'
$appDir = 'C:\Dev\galaxy-ai'
$legacyDir = 'C:\Dev\Xingxu-AI-0.1.0\Xingxu-AI'
Get-Process -Name GalaxyAI,Xingxu -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq (Join-Path $appDir 'GalaxyAI.exe') -or $_.Path -eq (Join-Path $legacyDir 'Xingxu.exe') } |
  Stop-Process -Force
Start-Sleep -Milliseconds 500
if (-not (Test-Path -LiteralPath $appDir)) {
  if (-not (Test-Path -LiteralPath $legacyDir)) { throw 'Existing Electron runtime is missing' }
  Copy-Item -LiteralPath $legacyDir -Destination $appDir -Recurse
}
Copy-Item -LiteralPath 'C:\Dev\galaxy-ai-update.asar' -Destination (Join-Path $appDir 'resources\app.asar') -Force
Copy-Item -LiteralPath 'C:\Dev\GalaxyAI-update.exe' -Destination (Join-Path $appDir 'GalaxyAI.exe') -Force
Remove-Item -LiteralPath (Join-Path $appDir 'Xingxu.exe') -Force -ErrorAction SilentlyContinue
Write-Output 'galaxy ai desktop updated; existing login store preserved'
