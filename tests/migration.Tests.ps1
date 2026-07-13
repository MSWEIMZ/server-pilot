$scriptPath = Join-Path $PSScriptRoot "..\scripts\migrate_local_installations.ps1"
. $scriptPath

Describe "Server Pilot local migration plan" {
    It "returns the six managed Skill installation targets" {
        $plan = Get-MigrationPlan -CanonicalRoot "C:\Users\WEI\server-pilot" -UserProfile "C:\Users\WEI"
        $plan.Count | Should Be 6
        ($plan | Where-Object { $_.Target -eq "C:\Users\WEI\.codex\skills\my-server-ssh" }).Count | Should Be 1
    }

    It "rejects a canonical directory outside the user profile" {
        $threw = $false
        try { Get-MigrationPlan -CanonicalRoot "C:\Windows" -UserProfile "C:\Users\WEI" } catch { $threw = $true }
        $threw | Should Be $true
    }
}
