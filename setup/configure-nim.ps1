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
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, "FullControl", "Allow")
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $EnvFile -AclObject $acl
}
catch {
    Write-Warning "Could not tighten .env ACL automatically. Review permissions manually."
}

$key = $null
Write-Host "NVIDIA NIM configuration saved locally."
Write-Host "File: $EnvFile"
Write-Host "The key was not printed and is ignored by Git."
Write-Host "Restart CSP Studio only if an already-open provider instance was created before configuration."
