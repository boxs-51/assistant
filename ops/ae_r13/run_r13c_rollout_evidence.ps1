param(
    [string]$GatewayUrl = "http://127.0.0.1:8000",
    [string]$Python = "python",
    [Parameter(Mandatory = $true)]
    [string]$Log,
    [string]$Output = ".\artifacts\r13c-rollout-evidence.json",
    [string]$DeployedSha = "5a75ac1483e15fecb91fbdeaa8626e5b62c4c54e",
    [string]$ExpectedSha = "5a75ac1483e15fecb91fbdeaa8626e5b62c4c54e",
    [string]$EnvironmentClass = "local-controlled",
    [ValidateSet("NONE", "EXPLAIN")]
    [string]$LogSampling = "NONE",
    [string]$SamplingNote = "",
    [int]$ServerRestartCount = 1,
    [double]$HoldSeconds = 8,
    [string]$SqlitePath = ".\data\gateway_storage.db",
    [string]$PostgresUrl = "",
    [string]$RestartScript = "",
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function UtcNowIso {
    return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
}

function Ensure-Parent([string]$Path) {
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

function Wait-GatewayReady([string]$BaseUrl, [int]$TimeoutSeconds = 60) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $uri = $BaseUrl.TrimEnd("/") + "/health"
            $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Gateway did not become reachable within $TimeoutSeconds seconds."
}

function Run-Probe(
    [string]$Label,
    [string]$ProbePath,
    [string]$Gateway,
    [double]$Hold
) {
    Write-Host ""
    Write-Host "=== $Label ==="
    $start = UtcNowIso
    & $Python $ProbePath --gateway-url $Gateway --hold-seconds $Hold
    $exitCode = $LASTEXITCODE
    $end = UtcNowIso
    if ($exitCode -ne 0) {
        throw "$Label probe failed closed with exit code $exitCode."
    }
    return @{
        Start = $start
        End = $end
    }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ProbePath = Join-Path $PSScriptRoot "r13c_client_reconnect_probe.py"
$CollectorPath = Join-Path $PSScriptRoot "r13c_collect_evidence.py"

Push-Location $RepoRoot
try {
    if (-not (Test-Path $ProbePath)) {
        throw "Missing probe: $ProbePath"
    }
    if (-not (Test-Path $CollectorPath)) {
        throw "Missing collector: $CollectorPath"
    }

    Ensure-Parent $Output

    if (-not $PostgresUrl -and -not $SqlitePath) {
        throw "Specify -SqlitePath or -PostgresUrl."
    }
    if ($PostgresUrl -and $PSBoundParameters.ContainsKey("SqlitePath")) {
        throw "Use exactly one durable inventory source: SQLite or PostgreSQL."
    }
    if ($ServerRestartCount -lt 1) {
        throw "-ServerRestartCount must be >= 1."
    }

    Write-Host "AE-R13-C one-command rollout evidence pipeline"
    Write-Host "Gateway: $GatewayUrl"
    Write-Host "Deployed SHA: $DeployedSha"
    Write-Host "Expected audited SHA: $ExpectedSha"
    Write-Host "Log: $Log"
    Write-Host ""
    Write-Host "The pipeline is observation/read-only only."
    Write-Host "It never deletes legacy JSON or mutates the durable database."

    $windowStart = UtcNowIso

    if ($RestartScript) {
        $restartPath = Resolve-Path $RestartScript
        Write-Host ""
        Write-Host "Running operator-provided restart script: $restartPath"
        & $restartPath
        if (-not $?) {
            throw "Restart script failed."
        }
        Wait-GatewayReady $GatewayUrl
    }
    elseif (-not $NonInteractive) {
        Write-Host ""
        Write-Host "Restart/deploy the target server now while unsampled INFO JSON capture is active."
        Read-Host "Press ENTER after the restarted server is healthy"
        Wait-GatewayReady $GatewayUrl
    }
    else {
        throw "-NonInteractive requires -RestartScript so the restart is inside the recorded observation window."
    }

    $cycleA = Run-Probe "CYCLE_A" $ProbePath $GatewayUrl $HoldSeconds

    if (-not $NonInteractive) {
        Write-Host ""
        Write-Host "Cycle A complete."
        Write-Host "The collector will fail closed if its inventory/batch event is absent."
        Read-Host "Press ENTER to start fresh process-style Cycle B"
    }

    Start-Sleep -Seconds 2
    $cycleB = Run-Probe "CYCLE_B" $ProbePath $GatewayUrl $HoldSeconds
    $windowEnd = UtcNowIso

    $collectorArgs = @(
        $CollectorPath,
        "--log", $Log,
        "--output", $Output,
        "--deployed-sha", $DeployedSha,
        "--expected-sha", $ExpectedSha,
        "--environment-class", $EnvironmentClass,
        "--server-restart-count", "$ServerRestartCount",
        "--log-sampling", $LogSampling,
        "--sampling-note", $SamplingNote,
        "--window-start", $windowStart,
        "--window-end", $windowEnd,
        "--cycle-a-start", $cycleA.Start,
        "--cycle-a-end", $cycleA.End,
        "--cycle-b-start", $cycleB.Start,
        "--cycle-b-end", $cycleB.End
    )

    if ($PostgresUrl) {
        $collectorArgs += @("--postgres-url", $PostgresUrl)
    }
    else {
        $collectorArgs += @("--sqlite-path", $SqlitePath)
    }

    Write-Host ""
    Write-Host "=== COLLECT / READ-ONLY INVENTORY ==="
    & $Python @collectorArgs
    $collectorExit = $LASTEXITCODE

    Write-Host ""
    Write-Host "Evidence: $Output"
    Write-Host "Window: $windowStart -> $windowEnd"
    Write-Host "Cycle A: $($cycleA.Start) -> $($cycleA.End)"
    Write-Host "Cycle B: $($cycleB.Start) -> $($cycleB.End)"

    if ($collectorExit -eq 0) {
        Write-Host "R13C_PIPELINE=PRELIMINARY_PASS"
        exit 0
    }
    if ($collectorExit -eq 2) {
        Write-Host "R13C_PIPELINE=HOLD"
        exit 2
    }
    Write-Host "R13C_PIPELINE=COLLECTOR_FAILED_CLOSED"
    exit 3
}
finally {
    Pop-Location
}
