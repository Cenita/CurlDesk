$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    py -3.10 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python 3.10 environment." }
}

& $python -c "import PySide6, curl_cffi" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

& $python main.py
