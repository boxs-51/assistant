#requires -Version 5.1
<#
.SYNOPSIS
  Bounded native-Windows reproduction for issue #242 / PR #429.
.DESCRIPTION
  Operator-only evidence tool on a NON-MERGE branch. No production/CI changes.
  Verifies repository, frozen PR HEAD and test blob, Python 3.12.10, and the
  original in-test 2.0-second result deadline. Stops on first non-PASS.
.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\r6d-429-windows-collector.ps1 -RepoPath E:\assistant -MaxAttempts 12 -WatchdogSeconds 120
.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\r6d-429-windows-collector.ps1 -RepoPath E:\assistant -PythonExe C:\Python312\python.exe
#>
param(
    [string]$RepoPath = (Get-Location).Path,
    [ValidateRange(1,12)][int]$MaxAttempts = 12,
    [ValidateRange(30,120)][int]$WatchdogSeconds = 120,
    [string]$PythonExe = ""
)
$ErrorActionPreference = "Stop"
$expectedHead = "3e339eb5e077a564498ae9f928c9fc247c2c6f4b"
$expectedBlob = "55356fd9fd5be6637c3bfbccb16cfd73aab88fc2"
$testFile = "cl/tests/test_r6_d_client_invocation_ledger.py"
$testNode = "cl/tests/test_r6_d_client_invocation_ledger.py::test_prepared_restart_can_execute_only_after_explicit_invoke"
$utf8 = New-Object System.Text.UTF8Encoding($false)

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Native Windows is required. WSL/Linux/Wine is not equivalent."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git.exe is missing from PATH."
}

$repoLines = @(& git -C $RepoPath rev-parse --show-toplevel)
$repoExit = $LASTEXITCODE
if ($repoExit -ne 0 -or $repoLines.Count -ne 1) {
    throw "RepoPath is not a readable Git worktree (exit=$repoExit)."
}
$repo = ([string]$repoLines[0]).Trim()
$remoteLines = @(& git -C $repo remote get-url origin)
$remoteExit = $LASTEXITCODE
if ($remoteExit -ne 0 -or $remoteLines.Count -ne 1) {
    throw "Could not resolve one Git origin URL (exit=$remoteExit)."
}
$remote = ([string]$remoteLines[0]).Trim().TrimEnd("/")
$allowedOrigins = @(
    "https://github.com/boxs-51/assistant",
    "https://github.com/boxs-51/assistant.git",
    "git@github.com:boxs-51/assistant",
    "git@github.com:boxs-51/assistant.git",
    "ssh://git@github.com/boxs-51/assistant",
    "ssh://git@github.com/boxs-51/assistant.git"
)
if ($remote -notin $allowedOrigins) {
    throw "Unexpected repository origin: $remote"
}
Write-Host "Verified repository origin: $remote"

# Probe the exact Python executable, rather than trusting a command name.
# Avoid embedded string quotes in Python -c: Windows PowerShell 5.1/native argv
# rewriting can strip those quotes, causing SyntaxError hidden by old preflight.
$probe = 'import sys; print(sys.executable); print(sys.version_info.major,sys.version_info.minor,sys.version_info.micro,sep=chr(46))'
function Resolve-Python31210 {
    param([string]$Exe, [string[]]$PrefixArgs = @(), [string]$Label)
    try {
        $lines = @(& $Exe @PrefixArgs -c $probe 2>$null)
        $probeExit = $LASTEXITCODE
        if ($probeExit -ne 0) {
            Write-Host "Python probe [$Label]: exit=$probeExit"
            return $null
        }
    }
    catch {
        Write-Host "Python probe [$Label]: failed: $($_.Exception.Message)"
        return $null
    }
    if ($lines.Count -ne 2) {
        Write-Host "Python probe [$Label]: expected 2 lines (exe, version); got $($lines.Count)."
        return $null
    }
    $path = ([string]$lines[0]).Trim()
    $version = ([string]$lines[1]).Trim()
    if ($version -ne "3.12.10") {
        Write-Host "Python probe [$Label]: found '$version'; required 3.12.10."
        return $null
    }
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Host "Python probe [$Label]: invalid executable location '$path'."
        return $null
    }
    Write-Host "Verified Python 3.12.10: $path (via $Label)"
    return $path
}

$basePython = $null
if ($PythonExe) {
    $basePython = Resolve-Python31210 -Exe $PythonExe -Label "explicit override"
    if (-not $basePython) {
        throw "Explicit -PythonExe does not execute Python 3.12.10."
    }
}
else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $basePython = Resolve-Python31210 -Exe "py" -PrefixArgs @("-3.12") -Label "py -3.12"
    }
    if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $basePython = Resolve-Python31210 -Exe "python" -Label "python PATH"
    }
    if (-not $basePython -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
        $basePython = Resolve-Python31210 -Exe "python3" -Label "python3 PATH"
    }
    if (-not $basePython) {
        foreach ($candidate in @(
            (Join-Path $repo ".venv\Scripts\python.exe"),
            (Join-Path $repo "venv\Scripts\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
            (Join-Path $env:ProgramFiles "Python312\python.exe")
        )) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $basePython = Resolve-Python31210 -Exe $candidate -Label "installed path"
                if ($basePython) { break }
            }
        }
    }
    if (-not $basePython) {
        throw "Python 3.12.10 not found. Diagnose: py -0p; py -3.12 -V; python -V; where.exe python. You may pass -PythonExe <absolute path>."
    }
}

$stamp = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0,8)
$worktree = Join-Path $env:TEMP "assistant-r6d429-$stamp"
$venv = Join-Path $env:TEMP "assistant-r6d429-venv-$stamp"
$out = Join-Path $env:USERPROFILE "R6D-429-CORRECTED-$stamp"
$zip = "$out.zip"
New-Item -ItemType Directory -Path $out -Force | Out-Null
$didPush = $false
$overall = "NOT_RUN"
$experimentError = $null

try {
    Write-Host "Fetching exact frozen PR #429 HEAD..."
    & git -C $repo fetch --no-tags origin pull/429/head
    if ($LASTEXITCODE -ne 0) { throw "Fetch PR429 failed." }
    $fetchedLines = @(& git -C $repo rev-parse FETCH_HEAD)
    if ($LASTEXITCODE -ne 0 -or $fetchedLines.Count -ne 1) { throw "FETCH_HEAD is invalid." }
    $fetched = ([string]$fetchedLines[0]).Trim()
    if ($fetched -ne $expectedHead) {
        throw "PR #429 HEAD changed ($fetched). Pinned evidence run refused."
    }
    & git -C $repo worktree add --detach $worktree $expectedHead
    if ($LASTEXITCODE -ne 0) { throw "Could not create isolated worktree." }
    Push-Location -LiteralPath $worktree
    $didPush = $true

    $actualHead = (& git rev-parse HEAD | Select-Object -First 1).Trim()
    $actualBlob = (& git hash-object -- $testFile | Select-Object -First 1).Trim()
    if ($actualHead -ne $expectedHead -or $actualBlob -ne $expectedBlob) {
        throw "Frozen HEAD / R6-D test Git blob mismatch."
    }
    if (@(& git status --porcelain).Count -gt 0) {
        throw "Isolated worktree is not clean."
    }

    Write-Host "Creating isolated Python 3.12.10 virtual environment..."
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed." }
    $python = Join-Path $venv "Scripts\python.exe"
    $venvVersionProbe = 'import sys; print(sys.version_info.major,sys.version_info.minor,sys.version_info.micro,sep=chr(46))'
    $ver = (& $python -c $venvVersionProbe | Select-Object -First 1).Trim()
    if ($LASTEXITCODE -ne 0 -or $ver -ne "3.12.10") { throw "venv Python is not 3.12.10: $ver" }
    Write-Host "Installing pinned repository test requirements..."
    & $python -m pip install -r requirements-test.txt
    if ($LASTEXITCODE -ne 0) { throw "Test requirements installation failed." }

    $preflight = [ordered]@{
        issue = 242
        pr = 429
        repo_origin = $remote
        head = $actualHead
        test_blob = $actualBlob
        python = $ver
        python_executable = $python
        os = [Environment]::OSVersion.VersionString
        node = $testNode
        max_attempts = $MaxAttempts
        watchdog_seconds = $WatchdogSeconds
        original_test_timeout_seconds = 2.0
        started_utc = [DateTime]::UtcNow.ToString("o")
        collector = "R6D_429_GITHUB_NONMERGE_BRANCH_DOTNET_PROCESS"
    }
    [IO.File]::WriteAllText((Join-Path $out "preflight.json"), ($preflight | ConvertTo-Json -Depth 4), $utf8)
    $manifest = Join-Path $out "manifest.jsonl"
    $overall = "NO_RED_WITHIN_BOUND"

    for ($i = 1; $i -le $MaxAttempts; $i++) {
        Write-Host ("Attempt {0}/{1}..." -f $i,$MaxAttempts)
        $started = [DateTime]::UtcNow
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $stdout = Join-Path $out ("attempt-{0:D2}.stdout.txt" -f $i)
        $stderr = Join-Path $out ("attempt-{0:D2}.stderr.txt" -f $i)
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $python
        $psi.Arguments = '-B -m pytest -q -s -p no:cacheprovider "' + $testNode + '"'
        $psi.WorkingDirectory = $worktree
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
        $child = New-Object System.Diagnostics.Process
        $child.StartInfo = $psi
        $timedOut = $false
        $exitCode = $null
        $stdoutText = ""
        $stderrText = ""
        $outputComplete = $false
        try {
            if (-not $child.Start()) { throw "Could not start isolated pytest process." }
            $outTask = $child.StandardOutput.ReadToEndAsync()
            $errTask = $child.StandardError.ReadToEndAsync()
            if (-not $child.WaitForExit($WatchdogSeconds * 1000)) {
                $timedOut = $true
                Write-Warning "External process watchdog expired."
                & "$env:SystemRoot\System32\taskkill.exe" /PID $child.Id /T /F | Out-Null
                $null = $child.WaitForExit(10000)
            }
            if ($child.HasExited) { $exitCode = [int]$child.ExitCode }
            $outDone = $outTask.Wait(10000)
            $errDone = $errTask.Wait(10000)
            if ($outDone) { $stdoutText = $outTask.Result }
            if ($errDone) { $stderrText = $errTask.Result }
            $outputComplete = $outDone -and $errDone
        }
        finally {
            $child.Dispose()
            $sw.Stop()
            [IO.File]::WriteAllText($stdout, $stdoutText, $utf8)
            [IO.File]::WriteAllText($stderr, $stderrText, $utf8)
        }
        $diag = $stdoutText.Contains("R6-D-WIN-D0 diagnostic=") -or $stderrText.Contains("R6-D-WIN-D0 diagnostic=")
        $status = if ($timedOut) { "WATCHDOG" }
            elseif ($null -eq $exitCode -or -not $outputComplete) { "COLLECTOR_ERROR" }
            elseif ($exitCode -ne 0) { "RED" }
            elseif ($diag) { "DIAGNOSTIC_FOUND" }
            else { "PASS" }
        $row = [ordered]@{
            attempt = $i
            started_utc = $started.ToString("o")
            ended_utc = [DateTime]::UtcNow.ToString("o")
            duration_seconds = [math]::Round($sw.Elapsed.TotalSeconds,3)
            exit_code = $exitCode
            status = $status
            diagnostic_found = $diag
            stdout = (Split-Path $stdout -Leaf)
            stderr = (Split-Path $stderr -Leaf)
        }
        [IO.File]::AppendAllText($manifest, ($row | ConvertTo-Json -Compress) + [Environment]::NewLine, $utf8)
        Write-Host ("Attempt {0}: {1} (exit={2})" -f $i,$status,$exitCode)
        if ($status -ne "PASS") {
            $overall = "STOPPED_FIRST_$status"
            Write-Warning "Preserving the first non-PASS. No retry-to-PASS."
            break
        }
        $blobAfter = (& git hash-object -- $testFile | Select-Object -First 1).Trim()
        if ($LASTEXITCODE -ne 0 -or $blobAfter -ne $expectedBlob) {
            $overall = "STOPPED_SOURCE_CHANGED"
            throw "R6-D test changed while running."
        }
    }
}
catch {
    $experimentError = $_.Exception.ToString()
    $overall = "COLLECTOR_SETUP_OR_RUNTIME_ERROR"
    [IO.File]::WriteAllText((Join-Path $out "collector-error.txt"), $experimentError, $utf8)
}
finally {
    if ($didPush) { Pop-Location }
    $finalRecord = [ordered]@{
        result = $overall
        finished_utc = [DateTime]::UtcNow.ToString("o")
        exact_pr_head = $expectedHead
        evidence_dir = $out
    }
    [IO.File]::WriteAllText((Join-Path $out "result.json"), ($finalRecord | ConvertTo-Json), $utf8)
    Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip -Force
    Write-Host "RESULT: $overall"
    Write-Host "EVIDENCE ZIP: $zip"
    Write-Host "Upload the unmodified ZIP for independent evidence review."
}
if ($null -ne $experimentError) { throw $experimentError }
