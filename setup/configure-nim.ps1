param(
    [string]$Repo = "C:\CSP\Youtube"
)

$ErrorActionPreference = "Stop"
$Repo = [System.IO.Path]::GetFullPath($Repo)
$EnvFile = Join-Path $Repo ".env"

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repo not found: $Repo"
}

$secure = Read-Host "NVIDIA NIM API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($key)) {
    throw "NVIDIA API key cannot be empty"
}
if ($key -match "\s") {
    throw "NVIDIA API key cannot contain whitespace"
}

$lines = @()
if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
    $lines = @(Get-Content -LiteralPath $EnvFile | Where-Object { $_ -notmatch '^\s*NVIDIA_API_KEY\s*=' })
}
$lines += "NVIDIA_API_KEY=$key"
[System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

try {
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminsSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $usersSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-545")
    $authenticatedSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-11")

    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetOwner($currentSid)
    $acl.SetAccessRuleProtection($true, $false)

    foreach ($sid in @($currentSid, $systemSid, $adminsSid)) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }

    Set-Acl -LiteralPath $EnvFile -AclObject $acl

    $verified = Get-Acl -LiteralPath $EnvFile
    $badRules = @($verified.Access | Where-Object {
        ($_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]) -eq $usersSid) -or
        ($_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]) -eq $authenticatedSid)
    })
    if ($badRules.Count -gt 0) {
        throw "Unsafe broad ACL entries remain on .env"
    }
}
catch {
    throw "Could not secure .env ACL: $($_.Exception.Message)"
}

$key = $null
Write-Host "NVIDIA NIM configuration saved locally."
Write-Host "File: $EnvFile"
Write-Host "ACL: current user, SYSTEM and Administrators only."
Write-Host "The key was not printed and is ignored by Git."
Write-Host "Restart CSP Studio only if an already-open provider instance was created before configuration."
