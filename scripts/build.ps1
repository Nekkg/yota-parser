param([string]$Python)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
foreach ($relative in @('.build', '.build\bundle', '.build\work', 'bin', 'dist', 'dist\YotaParser', 'dist\YotaParser\bin')) {
    $target = [IO.Path]::GetFullPath((Join-Path $taskRoot $relative))
    if (-not $target.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build destination is outside the project.' }
    if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Build destination is a directory link.' }
}
if (-not $Python) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup.ps1') -Build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $Python = Join-Path $taskRoot '.runtime\python\Scripts\python.exe'
}
$Python = (Get-Item -LiteralPath $Python).FullName

$buildFolder = Join-Path $taskRoot '.build'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildFolder 'cache'
$bundleFolder = Join-Path $buildFolder 'bundle'
& $Python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name YotaParser --paths (Join-Path $taskRoot 'src') --collect-all curl_cffi --collect-all certifi --exclude-module playwright --exclude-module yota_parser.browser --exclude-module tkinter --distpath $bundleFolder --workpath (Join-Path $buildFolder 'work') --specpath $buildFolder (Join-Path $taskRoot 'run.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

$bin = Join-Path $taskRoot 'bin'
$stage = Join-Path $taskRoot 'dist\YotaParser'
foreach ($folder in @($bin, (Join-Path $stage 'bin'))) {
    $target = [IO.Path]::GetFullPath($folder)
    if (-not $target.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build destination is outside the project.' }
    if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Build destination is a directory link.' }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}
Get-ChildItem -LiteralPath (Join-Path $bundleFolder 'YotaParser') | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $bin -Recurse }
Get-ChildItem -LiteralPath $bin | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stage 'bin') -Recurse }
foreach ($file in @('start.bat', 'settings.ini')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot $file) -Destination $stage
}
New-Item -ItemType Directory -Path (Join-Path $stage 'results') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable-README.txt') -Destination (Join-Path $stage 'README.txt')
Write-Host "Build ready: $stage"
Write-Host 'Run start.bat in that folder. Copy or archive the complete folder yourself.'
