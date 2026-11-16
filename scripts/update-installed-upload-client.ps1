$ErrorActionPreference = 'Stop'
$taskWorkspace = 'C:\Users\Administrator\Desktop\galaxy-ai-main'
$taskPackage = Join-Path $taskWorkspace 'desktop-client\release\win-unpacked'
$taskInstalled = 'C:\Program Files\GalaxyAI'
$taskBackup = Join-Path $taskWorkspace ('artifacts\installed-client-before-0.7.6-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$taskExe = Join-Path $taskInstalled 'GalaxyAI.exe'
if ((Resolve-Path -LiteralPath $taskPackage).Path -ne $taskPackage) { throw 'Unexpected package path' }
if ((Resolve-Path -LiteralPath $taskInstalled).Path -ne $taskInstalled) { throw 'Unexpected installed app path' }
if ((Get-Item -LiteralPath $taskInstalled).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installed path must not be a link' }
foreach ($taskRequired in @('GalaxyAI.exe', 'resources\app.asar')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskPackage $taskRequired))) { throw ('Missing package file: ' + $taskRequired) }
}
New-Item -ItemType Directory -Path $taskBackup | Out-Null
Get-ChildItem -LiteralPath $taskInstalled -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $taskBackup -Recurse -Force
}
Write-Output ('Installed application backup: ' + $taskBackup)
Get-Process -Name GalaxyAI -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskExe } | Stop-Process -Force
Start-Sleep -Milliseconds 700
try {
    Get-ChildItem -LiteralPath $taskPackage -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $taskInstalled -Recurse -Force
    }
    $taskSourceHash = (Get-FileHash -LiteralPath (Join-Path $taskPackage 'resources\app.asar') -Algorithm SHA256).Hash
    $taskInstalledHash = (Get-FileHash -LiteralPath (Join-Path $taskInstalled 'resources\app.asar') -Algorithm SHA256).Hash
    if ($taskSourceHash -ne $taskInstalledHash) { throw 'Installed package checksum mismatch' }
} catch {
    Get-ChildItem -LiteralPath $taskBackup -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $taskInstalled -Recurse -Force
    }
    Start-Process -FilePath $taskExe -WorkingDirectory $taskInstalled -WindowStyle Normal | Out-Null
    throw
}
# This is the interactive client the user will test, rather than a background helper.
Start-Process -FilePath $taskExe -WorkingDirectory $taskInstalled -WindowStyle Normal | Out-Null
Write-Output 'Installed client updated and restarted. Existing user data and login storage were preserved.'
