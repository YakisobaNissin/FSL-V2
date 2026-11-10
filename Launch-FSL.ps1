$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'launch_fsl.py'
$check = 'import sys; raise SystemExit(sys.implementation.name != ''cpython'' or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32)'

function Test-Python312($executable, $prefix = @()) {
    try {
        & $executable @prefix -c $check 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

if ($env:FSL_PYTHON312) {
    & $env:FSL_PYTHON312 $launcher @args
    exit $LASTEXITCODE
}

if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python312 'py' @('-3.12'))) {
    & py -3.12 $launcher @args
    exit $LASTEXITCODE
}

$candidates = @(
    (Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312\python.exe')
)
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf) -and (Test-Python312 $candidate)) {
        & $candidate $launcher @args
        exit $LASTEXITCODE
    }
}
throw 'Install 64-bit Python 3.12 or set FSL_PYTHON312 to its python.exe path.'
