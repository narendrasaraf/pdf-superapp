$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$logo = Join-Path $PSScriptRoot 'logo.png'
$icon = Join-Path $PSScriptRoot 'logo.ico'
python -m PyInstaller --noconfirm --onedir --windowed --name 'PDF Superapp' --icon $icon --add-data ($logo + ';.') --collect-all pillow_heif --collect-all pymupdf --collect-data docx superapp.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Output 'App built in dist/PDF Superapp. Keep the entire folder together.'
