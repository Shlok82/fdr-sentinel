<#
.SYNOPSIS
    FDR-Sentinel Demo Snapshot & Restore Utility (PowerShell)
    SPEC §14.2 & Stage 9 Acceptance Testing

.DESCRIPTION
    Creates, restores, and inspects zero-drift snapshots of database state and vault storage.
    WARNING: For local development, demo rehearsal, and automated evaluation ONLY.

.EXAMPLE
    .\scripts\demo_snapshot.ps1 save pristine
    .\scripts\demo_snapshot.ps1 restore pristine
    .\scripts\demo_snapshot.ps1 list
#>

[CmdletBinding()]
param (
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("save", "restore", "list", "help")]
    [string]$Action,

    [Parameter(Position = 1, Mandatory = $false)]
    [string]$Name = "",

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Base directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$SnapshotsDir = Join-Path $ProjectRoot "snapshots"
if (-not (Test-Path $SnapshotsDir)) {
    New-Item -ItemType Directory -Path $SnapshotsDir -Force | Out-Null
}

function Show-Usage {
    Write-Host @"
FDR-Sentinel Snapshot Utility
Usage:
    .\scripts\demo_snapshot.ps1 save <snapshot_name> [--force]
    .\scripts\demo_snapshot.ps1 restore <snapshot_name>
    .\scripts\demo_snapshot.ps1 list

Note: All snapshot operations require docker compose services 'web' and 'db' to be running.
"@
}

function Get-StateSummary {
    Write-Host "Querying database state summary..." -ForegroundColor Cyan
    $summaryCmd = @"
from app.core.database import SessionLocal
from sqlalchemy import text
from app.services.clock_service import clock_service
s = SessionLocal()
clock_service.load_offset_from_db(s)
inst_count = s.execute(text('SELECT count(*) FROM institutions')).scalar()
fdr_count = s.execute(text('SELECT count(*) FROM instruments')).scalar()
audit_count = s.execute(text('SELECT count(*) FROM audit_log')).scalar()
alert_count = s.execute(text('SELECT count(*) FROM alerts')).scalar()
notif_count = s.execute(text('SELECT count(*) FROM notifications')).scalar()
status_rows = s.execute(text('SELECT status, count(*) FROM instruments GROUP BY status')).fetchall()
st_map = dict(status_rows)
print(f'CLOCK_OFFSET:{clock_service.offset_seconds}')
print(f'INSTITUTIONS:{inst_count}')
print(f'INSTRUMENTS:{fdr_count}')
print(f'AUDIT_LOG:{audit_count}')
print(f'ALERTS:{alert_count}')
print(f'NOTIFICATIONS:{notif_count}')
for st, cnt in sorted(st_map.items()):
    print(f'STATUS_{st}:{cnt}')
"@
    $res = docker compose exec -T web python -c "$summaryCmd"
    return $res
}

function Verify-AuditChain {
    Write-Host "Verifying cryptographic audit chain..." -ForegroundColor Cyan
    $auditCmd = @"
from app.core.database import SessionLocal
from app.services.audit_service import verify_chain
s = SessionLocal()
res = verify_chain(s)
print(f'CHAIN_OK:{res.ok}')
print(f'CHAIN_ENTRIES:{res.rows_checked}')
if not res.ok:
    print(f'CHAIN_ERROR:{res.reason}')
"@
    $res = docker compose exec -T web python -c "$auditCmd"
    return $res
}

# --- Action: LIST ---
if ($Action -eq "list") {
    Write-Host "`n=== Available Demo Snapshots ===" -ForegroundColor Green
    $snapshots = Get-ChildItem -Path $SnapshotsDir -Directory
    if ($snapshots.Count -eq 0) {
        Write-Host "No snapshots found in $SnapshotsDir" -ForegroundColor Yellow
        exit 0
    }
    foreach ($snap in $snapshots) {
        $dumpPath = Join-Path $snap.FullName "db.dump"
        $storagePath = Join-Path $snap.FullName "storage"
        $dumpSizeMB = 0
        if (Test-Path $dumpPath) {
            $dumpSizeMB = [math]::Round(((Get-Item $dumpPath).Length / 1MB), 2)
        }
        $storageFiles = 0
        if (Test-Path $storagePath) {
            $storageFiles = (Get-ChildItem -Path $storagePath -Recurse -File).Count
        }
        Write-Host ("  [{0}] - Created: {1} - DB Dump: {2} MB - Vault Files: {3}" -f $snap.Name, $snap.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $dumpSizeMB, $storageFiles)
    }
    Write-Host ""
    exit 0
}

# Ensure name was supplied for save/restore
if ([string]::IsNullOrWhiteSpace($Name)) {
    Write-Error "Error: Snapshot name is required for '$Action'."
    Show-Usage
    exit 1
}

# Sanitize name
$CleanName = $Name -replace '[^a-zA-Z0-9_\-]', '_'
$TargetDir = Join-Path $SnapshotsDir $CleanName

# --- Action: SAVE ---
if ($Action -eq "save") {
    Write-Host "`n=== Saving Snapshot: '$CleanName' ===" -ForegroundColor Green
    if (Test-Path $TargetDir) {
        if (-not $Force) {
            Write-Error "Snapshot '$CleanName' already exists. Use -Force to overwrite."
            exit 1
        }
        Write-Host "Snapshot directory exists; overwriting as requested (-Force)." -ForegroundColor Yellow
        Remove-Item -Path $TargetDir -Recurse -Force
    }

    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    $TargetStorage = Join-Path $TargetDir "storage"
    New-Item -ItemType Directory -Path $TargetStorage -Force | Out-Null

    # 1. Capture State Metrics
    $stateInfo = Get-StateSummary
    $metaFile = Join-Path $TargetDir "snapshot_metadata.txt"
    $stateInfo | Out-File -FilePath $metaFile -Encoding UTF8

    # 2. Dump Database safely using container-internal path to avoid binary stdout corruption
    Write-Host "Dumping PostgreSQL database (fdr_sentinel)..." -ForegroundColor Cyan
    docker compose exec -T db pg_dump -U fdr_owner -d fdr_sentinel -F c -f /tmp/sentinel_dump.tmp
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Database dump failed with exit code $LASTEXITCODE."
        exit $LASTEXITCODE
    }
    $targetDump = Join-Path $TargetDir "db.dump"
    docker compose cp "db:/tmp/sentinel_dump.tmp" "$targetDump"
    docker compose exec -T db rm -f /tmp/sentinel_dump.tmp

    # 3. Copy Local Vault Storage
    Write-Host "Copying encrypted document vault storage..." -ForegroundColor Cyan
    $localVault = Join-Path $ProjectRoot "storage"
    if (Test-Path $localVault) {
        Copy-Item -Path "$localVault\*" -Destination $TargetStorage -Recurse -Force
    }
    $copiedFiles = (Get-ChildItem -Path $TargetStorage -Recurse -File).Count

    Write-Host "`nSnapshot '$CleanName' successfully saved!" -ForegroundColor Green
    Write-Host "  Destination:  $TargetDir"
    Write-Host "  Vault Files:  $copiedFiles"
    Write-Host "`nCaptured State:"
    foreach ($line in $stateInfo) {
        Write-Host "  $line"
    }
    Write-Host ""
    exit 0
}

# --- Action: RESTORE ---
if ($Action -eq "restore") {
    Write-Host "`n=== Restoring Snapshot: '$CleanName' ===" -ForegroundColor Green
    if (-not (Test-Path $TargetDir)) {
        Write-Error "Snapshot '$CleanName' does not exist in $SnapshotsDir."
        exit 1
    }
    $dumpFile = Join-Path $TargetDir "db.dump"
    if (-not (Test-Path $dumpFile)) {
        Write-Error "Database dump file '$dumpFile' is missing."
        exit 1
    }

    # 1. Stop web container to prevent active pool connections during restore
    Write-Host "Stopping web service..." -ForegroundColor Cyan
    docker compose stop web | Out-Null

    # 2. Restore PostgreSQL dump
    Write-Host "Restoring PostgreSQL database (fdr_sentinel)..." -ForegroundColor Cyan
    docker compose cp "$dumpFile" "db:/tmp/sentinel_restore.tmp"
    
    # Run pg_restore with --clean --if-exists
    docker compose exec -T db pg_restore -U fdr_owner -d fdr_sentinel --clean --if-exists /tmp/sentinel_restore.tmp
    # Note: pg_restore might emit non-fatal warnings (e.g. drop table if not exists), exit code 0 or 1 is normal
    docker compose exec -T db rm -f /tmp/sentinel_restore.tmp

    # 3. Restore Local Vault Storage
    Write-Host "Restoring encrypted document vault storage..." -ForegroundColor Cyan
    $localVault = Join-Path $ProjectRoot "storage"
    if (-not (Test-Path $localVault)) {
        New-Item -ItemType Directory -Path $localVault -Force | Out-Null
    } else {
        Get-ChildItem -Path $localVault -Recurse | Remove-Item -Force -Recurse
    }
    $snapStorage = Join-Path $TargetDir "storage"
    if (Test-Path $snapStorage) {
        Copy-Item -Path "$snapStorage\*" -Destination $localVault -Recurse -Force
    }

    # 4. Start web service and await health
    Write-Host "Starting web service..." -ForegroundColor Cyan
    docker compose start web | Out-Null

    Write-Host "Awaiting web application readiness (http://localhost:8000/healthz)..." -ForegroundColor Cyan
    $ready = $false
    $retries = 30
    while ($retries -gt 0 -and -not $ready) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:8000/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                $ready = $true
            }
        } catch {
            # Continue polling
        }
        $retries--
    }

    if (-not $ready) {
        Write-Error "Application failed to become healthy within 30 seconds."
        exit 1
    }

    # 5. Verify restored state and audit chain
    Write-Host "`nSnapshot '$CleanName' successfully restored!" -ForegroundColor Green
    $postState = Get-StateSummary
    Write-Host "`nRestored State:"
    foreach ($line in $postState) {
        Write-Host "  $line"
    }

    $auditCheck = Verify-AuditChain
    Write-Host "`nAudit Chain Verification:"
    foreach ($line in $auditCheck) {
        Write-Host "  $line"
    }
    Write-Host ""
    exit 0
}
