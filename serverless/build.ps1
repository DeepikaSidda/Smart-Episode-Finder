# Builds the two Lambda deployment packages.
#
# Dependencies are downloaded as Linux wheels (--platform manylinux2014_x86_64)
# so the packages are valid for the Lambda runtime even though we build on Windows.
$ErrorActionPreference = "Stop"
$root    = $PSScriptRoot
$build   = Join-Path $root "build"
$runtime = "3.12"

Write-Host "== Cleaning build directory =="
if (Test-Path $build) { Remove-Item $build -Recurse -Force }
New-Item -ItemType Directory -Path $build | Out-Null

# ---------- API package (no third-party deps; boto3 is in the runtime) ----------
Write-Host "== Building api.zip =="
$apiDir = Join-Path $build "api"
New-Item -ItemType Directory -Path $apiDir | Out-Null
Copy-Item (Join-Path $root "api_handler.py") $apiDir
Copy-Item (Join-Path $root "core") $apiDir -Recurse
Get-ChildItem $apiDir -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path (Join-Path $apiDir "*") -DestinationPath (Join-Path $build "api.zip") -Force

# ---------- Activities package (requests + youtube-transcript-api) ----------
Write-Host "== Building activities.zip =="
$actDir = Join-Path $build "activities"
New-Item -ItemType Directory -Path $actDir | Out-Null

python -m pip install `
    --requirement (Join-Path $root "requirements.txt") `
    --target $actDir `
    --platform manylinux2014_x86_64 `
    --python-version $runtime `
    --implementation cp `
    --only-binary=:all: `
    --upgrade `
    --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Copy-Item (Join-Path $root "activities_handler.py") $actDir
Copy-Item (Join-Path $root "core") $actDir -Recurse
Get-ChildItem $actDir -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Get-ChildItem $actDir -Recurse -Directory -Include "*.dist-info" | Remove-Item -Recurse -Force
Compress-Archive -Path (Join-Path $actDir "*") -DestinationPath (Join-Path $build "activities.zip") -Force

Write-Host ""
Write-Host "== Built =="
Get-ChildItem $build -Filter *.zip | ForEach-Object {
    "  {0}  {1:N0} KB" -f $_.Name, ($_.Length / 1KB)
}
