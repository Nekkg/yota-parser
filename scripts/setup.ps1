param([switch]$Browser, [switch]$Build)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimeRoot = Join-Path $taskRoot '.runtime'
$pythonFolder = Join-Path $runtimeRoot 'python'
$taskPython = Join-Path $pythonFolder 'Scripts\python.exe'

function Remove-LocalEnvironment {
    $target = [IO.Path]::GetFullPath($pythonFolder)
    if (-not $target.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Environment path is outside the project.'
    }
    foreach ($item in @($runtimeRoot, $target)) {
        if ((Test-Path -LiteralPath $item) -and ((Get-Item -LiteralPath $item).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Refusing to remove an environment through a directory link.'
        }
    }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
}

function Invoke-Python {
    param([string[]]$PythonArguments)
    & $taskPython @PythonArguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit $LASTEXITCODE)." }
}

function Test-Python {
    param([string]$Code)
    $ErrorActionPreference = 'Continue'
    & $taskPython -c $Code 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Find-Python {
    $ErrorActionPreference = 'Continue'
    foreach ($name in @('py.exe', 'python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command -or $command.Source -like '*\Microsoft\WindowsApps\*') { continue }
        $flags = @()
        if ($name -eq 'py.exe') { $flags += '-3' }
        $candidate = & $command.Source @flags -c 'import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath ([string]$candidate))) { return [string]$candidate }
    }
    return $null
}

try {
    $valid = $false
    if (Test-Path -LiteralPath $taskPython) {
        $valid = Test-Python -Code 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
    }
    if (-not $valid) {
        $basePython = Find-Python
        if (-not $basePython) {
            throw 'Python 3.10+ was not found. Use the built portable folder (no Python required), or install Python from https://www.python.org/downloads/windows/ and enable Add Python to PATH.'
        }
        Remove-LocalEnvironment
        New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
        Write-Host 'Creating a local Python environment...'
        & $basePython -m venv $pythonFolder
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
    }

    if (-not (Test-Python -Code "from importlib.metadata import version; import curl_cffi; assert version('curl_cffi') == '0.16.3'")) {
        Write-Host 'Installing parser dependencies...'
        Invoke-Python -PythonArguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $taskRoot 'requirements.txt'))
    }
    if ($Build) {
        Invoke-Python -PythonArguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $taskRoot 'requirements-build.txt'))
    }
    if ($Browser) {
        Invoke-Python -PythonArguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $taskRoot 'requirements-browser.txt'))
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtimeRoot 'browsers'
        Invoke-Python -PythonArguments @('-m', 'playwright', 'install', 'chromium')
    }
    Write-Host 'Source environment is ready.'
    exit 0
} catch {
    Write-Host "Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
