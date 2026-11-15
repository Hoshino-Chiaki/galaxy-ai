$ErrorActionPreference = 'Stop'
$appDirectory = 'C:\Dev\galaxy-ai'
$executable = Join-Path $appDirectory 'GalaxyAI.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw 'Application executable is missing' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'galaxy ai.lnk'))
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = $appDirectory
$shortcut.Description = 'galaxy ai'
$shortcut.Save()
Remove-Item -LiteralPath (Join-Path $desktop 'Xingxu AI.lnk') -Force -ErrorAction SilentlyContinue
Write-Output 'Desktop shortcut created: galaxy ai'
