# Windows setup. Run from any directory; no activation or administrator rights needed.
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

Push-Location -LiteralPath $repoRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host '[1/5] Checking Python and creating .venv...'
        Invoke-Checked $Python @('-c', "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ is required'")
        Invoke-Checked $Python @('-m', 'venv', (Join-Path $repoRoot '.venv'))
    } else {
        Write-Host '[1/5] Reusing .venv...'
    }
    Invoke-Checked $venvPython @('-c', "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ is required'")

    Write-Host '[2/5] Preparing the official FMPy binary dependencies...'
    # Force the released wheel, including on repeat runs after an editable install.
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--force-reinstall', '--no-deps', '--only-binary=:all:', 'fmpy==0.3.32')
    Invoke-Checked $venvPython @((Join-Path $repoRoot 'scripts\prepare_native.py'))

    Write-Host '[3/5] Installing this checkout, GUI, and test dependencies...'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-e', '.[gui]', 'pytest', 'requests')

    Write-Host '[4/5] Checking package dependencies...'
    Invoke-Checked $venvPython @('-m', 'pip', 'check')

    Write-Host '[5/5] Checking the GUI and scenario runner...'
    Invoke-Checked $venvPython @((Join-Path $repoRoot 'scripts\check_gui.py'))
    Write-Host ''
    Write-Host 'Setup complete. Start the GUI with: .\start-fmpy.ps1'
    if ($Launch) {
        & (Join-Path $repoRoot 'start-fmpy.ps1')
    }
} finally {
    Pop-Location
}
