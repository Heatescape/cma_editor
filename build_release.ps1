# Build a clean distribution zip of CMA Editor.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File build_release.ps1

$ProjectDir = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\')
$ZipPath    = Join-Path $ProjectDir "CMA-Editor.zip"

$ExcludeDirs  = @('.git', '.venv', 'uploads', 'output', '.tmp', '.claude')
$ExcludeExts  = @('.pdf', '.lnk', '.sh')
$ExcludeFiles = @('debug_scrape.py', 'build_release.ps1', 'CMA-Editor.zip')

Write-Host ""
Write-Host "Building CMA-Editor.zip from: $ProjectDir"
Write-Host ""

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

$files = Get-ChildItem -Path $ProjectDir -Recurse -File | Where-Object {
    $rel   = $_.FullName.Substring($ProjectDir.Length + 1)
    $parts = $rel -split '[/\\]'

    # Skip excluded top-level directories anywhere in path
    foreach ($d in $ExcludeDirs) {
        if ($parts -contains $d) { return $false }
    }
    # Skip __pycache__ directories
    if ($rel -match '__pycache__') { return $false }
    # Skip by extension
    if ($ExcludeExts -contains $_.Extension.ToLower()) { return $false }
    # Skip by filename
    if ($ExcludeFiles -contains $_.Name) { return $false }

    return $true
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($ZipPath, 'Create')

foreach ($f in $files) {
    $rel       = $f.FullName.Substring($ProjectDir.Length + 1)
    $entryName = "CMA-Editor\$rel"
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entryName) | Out-Null
    Write-Host "  + $entryName"
}

$zip.Dispose()

$sizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Done: CMA-Editor.zip  ($sizeMB MB)"
Write-Host ""
