$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    py -3.10 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python 3.10 environment." }
}

& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

$qtDllDirectory = Join-Path $PSScriptRoot ".venv\Lib\site-packages\PySide6"
$qtRuntime = Join-Path $qtDllDirectory "vcruntime140.dll"
$qtRuntimeExtra = Join-Path $qtDllDirectory "vcruntime140_1.dll"
$logoSvg = Join-Path $PSScriptRoot "curldesk\assets\curldesk.svg"
$logoIco = Join-Path $PSScriptRoot "curldesk\assets\curldesk.ico"

& $python (Join-Path $PSScriptRoot "curldesk\assets\build_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Could not build the CurlDesk icon." }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name CurlDesk `
    --icon "$logoIco" `
    --add-data "$logoSvg;curldesk/assets" `
    --add-data "$logoIco;curldesk/assets" `
    --add-binary "$qtRuntime;." `
    --add-binary "$qtRuntimeExtra;." `
    --collect-all curl_cffi `
    --collect-all PySide6.QtSvg `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host "Built: $PSScriptRoot\dist\CurlDesk.exe"
