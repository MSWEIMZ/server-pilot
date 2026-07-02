Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---- Paths ----
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dashboardPy = Join-Path $scriptDir "dashboard.py"
$port = 8765

# ---- Dashboard Process Management ----
function Start-Dashboard {
    $script:pyProc = Start-Process -FilePath "python" `
        -ArgumentList "`"$dashboardPy`" --no-browser --interval 10" `
        -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    try { Start-Process "http://localhost:$port" } catch {}
}

function Stop-Dashboard {
    if ($script:pyProc -and -not $script:pyProc.HasExited) {
        try { $script:pyProc.Kill() } catch {}
        $script:pyProc = $null
    }
}

# ---- Tray Icon ----
$icon = [System.Drawing.SystemIcons]::Application
$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = $icon
$notifyIcon.Text = "Server Pilot"

# ---- Context Menu ----
$ctx = New-Object System.Windows.Forms.ContextMenuStrip

# Status header
$itemStatus = New-Object System.Windows.Forms.ToolStripMenuItem
$itemStatus.Text = [char]0x25CF + " Running"
$itemStatus.Enabled = $false
$itemStatus.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$null = $ctx.Items.Add($itemStatus)
$null = $ctx.Items.Add("-")

# Open Dashboard
$itemOpen = New-Object System.Windows.Forms.ToolStripMenuItem
$itemOpen.Text = "Open Dashboard"
$itemOpen.Add_Click({ try { Start-Process "http://localhost:$port" } catch {} })
$null = $ctx.Items.Add($itemOpen)

# Force Refresh
$itemRefresh = New-Object System.Windows.Forms.ToolStripMenuItem
$itemRefresh.Text = "Force Refresh"
$itemRefresh.Add_Click({
    Stop-Dashboard
    Start-Sleep -Milliseconds 500
    Start-Dashboard
})
$null = $ctx.Items.Add($itemRefresh)
$null = $ctx.Items.Add("-")

# Stop
$itemStop = New-Object System.Windows.Forms.ToolStripMenuItem
$itemStop.Text = "Stop Dashboard"
$itemStop.Add_Click({
    Stop-Dashboard
    $itemStatus.Text = [char]0x25CF + " Stopped"
    $itemStatus.ForeColor = [System.Drawing.Color]::Red
    $notifyIcon.Text = "Server Pilot (Stopped)"
})
$null = $ctx.Items.Add($itemStop)

# Restart
$itemRestart = New-Object System.Windows.Forms.ToolStripMenuItem
$itemRestart.Text = "Restart"
$itemRestart.Add_Click({
    Stop-Dashboard
    Start-Sleep -Milliseconds 500
    Start-Dashboard
    $itemStatus.Text = [char]0x25CF + " Running"
    $itemStatus.ForeColor = [System.Drawing.Color]::FromArgb(16,185,129)
    $notifyIcon.Text = "Server Pilot"
})
$null = $ctx.Items.Add($itemRestart)
$null = $ctx.Items.Add("-")

# Exit
$itemExit = New-Object System.Windows.Forms.ToolStripMenuItem
$itemExit.Text = "Exit"
$itemExit.Add_Click({
    Stop-Dashboard
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
})
$null = $ctx.Items.Add($itemExit)

$notifyIcon.ContextMenuStrip = $ctx

# Double-click = open browser
$notifyIcon.Add_DoubleClick({
    try { Start-Process "http://localhost:$port" } catch {}
})

# ---- Start ----
$notifyIcon.Visible = $true
Start-Dashboard

$notifyIcon.ShowBalloonTip(3000,
    "Server Pilot",
    "Dashboard running at http://localhost:$port`nRight-click icon for options.",
    [System.Windows.Forms.ToolTipIcon]::Info)

[System.Windows.Forms.Application]::Run()
