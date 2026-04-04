# Publish spacetimedb/devops-module to Maincloud using the official Linux image (no local MSVC/Rust).
# Prerequisite: `spacetime login` on Windows so %LOCALAPPDATA%\SpacetimeDB\config\cli.toml exists.
# Usage: from repo root: .\scripts\publish-maincloud-docker.ps1
# Optional: $env:SPACETIME_DATABASE = "devopsai" (default). Maincloud: avoid underscores in the name.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$module = Join-Path $root "spacetimedb\devops-module"
$cfg = Join-Path $env:LOCALAPPDATA "SpacetimeDB\config"
$db = if ($env:SPACETIME_DATABASE) { $env:SPACETIME_DATABASE } else { "devopsai" }

$volMod = ($module -replace "\\", "/")
Write-Host "Publishing $db to maincloud from $volMod ..."

docker run --rm `
  -v "${volMod}:/module" `
  -v "${cfg}:/home/spacetime/.config/spacetime" `
  -w /module `
  clockworklabs/spacetime:latest `
  publish $db --server maincloud -y --delete-data
