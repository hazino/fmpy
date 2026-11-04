[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Run setup.ps1 first.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $venvPython -m fmpy.gui
    if ($LASTEXITCODE -ne 0) {
        throw 'FMPy GUI failed to start. Run setup.ps1 to check or repair the installation.'
    }
} finally {
    Pop-Location
}
