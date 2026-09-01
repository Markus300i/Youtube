param(
    [string]$Repo = "C:\CSP\Youtube"
)

$ErrorActionPreference = "Stop"
$Repo = [System.IO.Path]::GetFullPath($Repo)
$EnvFile = Join-Path $Repo ".env"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "NIM .env not found: $EnvFile"
}

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
    $sid = $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier])
    ($sid -eq $usersSid) -or ($sid -eq $authenticatedSid)
})
if ($badRules.Count -gt 0) {
    throw "Unsafe broad ACL entries remain on .env"
}

Write-Host "Secured NIM .env ACL."
Write-Host "File: $EnvFile"
Write-Host "Allowed principals: current user, SYSTEM, Administrators."
Write-Host "No .env contents were printed."
