$root = 'D:\Dev\Blender Addons\SimplySwitch'
$source = Join-Path $root 'simply_switch.py'

# Version comes from bl_info so the zip name can never drift from the addon.
$m = Select-String -Path $source -Pattern '"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)'
if (-not $m) { throw "Could not read version from $source" }
$version = "$($m.Matches[0].Groups[1].Value).$($m.Matches[0].Groups[2].Value).$($m.Matches[0].Groups[3].Value)"

$dist = Join-Path $root 'dist'
$zipPath = Join-Path $dist "SimplySwitch_v$version.zip"
New-Item -ItemType Directory -Path $dist -Force | Out-Null
if (Test-Path $zipPath) { Remove-Item $zipPath }

# Blender expects the addon in a folder matching the module name.
$build = Join-Path $root 'build'
$staging = Join-Path $build 'simply_switch'
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Path $staging -Force | Out-Null

Copy-Item $source (Join-Path $staging '__init__.py')
Copy-Item (Join-Path $root 'LICENSE') $staging
Copy-Item (Join-Path $root 'README.md') $staging

Compress-Archive -Path $staging -DestinationPath $zipPath -Force

$size = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)
Write-Host "Created: $zipPath ($size KB)"
