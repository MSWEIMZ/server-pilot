$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Port = 8765
$ConfigPath = Join-Path $Root "scripts\server_config.json"
$DashboardPath = Join-Path $Root "scripts\web\dashboard.py"
$LogPath = Join-Path $Root "dashboard.log"
$ErrorLogPath = Join-Path $Root "dashboard-error.log"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Error "Missing scripts\server_config.json. Configure the server first."
    exit 1
}

if (-not (Test-Path -LiteralPath $DashboardPath)) {
    Write-Error "Dashboard script not found: $DashboardPath"
    exit 1
}

function Test-DashboardPort {
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return [bool]$listeners
    } catch {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.Connect("127.0.0.1", $Port)
            return $true
        } catch {
            return $false
        } finally {
            $client.Dispose()
        }
    }
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
$pythonArgs = @("scripts\web\dashboard.py", "--port", $Port.ToString(), "--no-browser")

if (-not $python) {
    $python = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($python) {
        $pythonArgs = @("-3") + $pythonArgs
    }
}

if (-not $python) {
    Write-Error "Python was not found. Install Python 3 and add python.exe or py.exe to PATH."
    exit 1
}

if (-not (Test-DashboardPort)) {
    $startParams = @{
        FilePath = $python.Source
        ArgumentList = $pythonArgs
        WorkingDirectory = $Root
        WindowStyle = "Hidden"
        RedirectStandardOutput = $LogPath
        RedirectStandardError = $ErrorLogPath
    }
    Start-Process @startParams | Out-Null
}

$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    if (Test-DashboardPort) {
        $ready = $true
        break
    }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    Write-Warning "Dashboard did not confirm port readiness; opening the browser anyway. Check dashboard-error.log."
}

Start-Process "http://127.0.0.1:$Port"
