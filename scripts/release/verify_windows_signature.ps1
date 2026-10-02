param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Thumbprint,
    [Parameter(Mandatory)][string]$ProductVersion,
    [switch]$TestCertificate
)
$ErrorActionPreference = 'Stop'
if ($Thumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
    throw 'Configure the exact SignPath certificate thumbprint before signing.'
}
$file = Get-Item -LiteralPath $Path
Write-Output "Reading Authenticode signature: $($file.Name)"
$signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
if (-not $signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $Thumbprint) {
    throw "Unexpected or absent signing certificate: $($file.Name)"
}
if ($file.VersionInfo.ProductName -ne 'Infernux' -or $file.VersionInfo.ProductVersion -ne $ProductVersion) {
    throw "Unexpected product metadata: $($file.Name)"
}
# The test certificate is self-signed. Trust only the configured certificate,
# temporarily, on the disposable Windows CI runner. Never trust it for releases.
$store = $null
$added = $false
$testCertificate = $signature.SignerCertificate
try {
    if ($TestCertificate) {
        if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
            throw 'Test certificate trust is restricted to disposable GitHub-hosted runners.'
        }
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
        if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Temporary CI test certificate trust requires an elevated disposable runner.'
        }
        # CurrentUser Root can display a blocking Windows trust-confirmation
        # dialog. Use only the disposable runner's machine store, never a
        # developer machine, and remove precisely the certificate added here.
        Write-Output 'Preparing temporary pinned test certificate trust on the disposable runner'
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', 'LocalMachine')
        $store.Open('ReadWrite')
        if ($store.Certificates.Find('FindByThumbprint', $Thumbprint, $false).Count -eq 0) {
            $store.Add($testCertificate)
            $added = $true
        }
        Write-Output 'Checking signature with the pinned test certificate'
        $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    }
    if ($signature.Status -ne 'Valid') {
        throw "Invalid Authenticode signature for $($file.Name): $($signature.Status)"
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "Missing Authenticode timestamp: $($file.Name)"
    }
    Write-Output "Verified $($file.Name): $($signature.SignerCertificate.Subject)"
} finally {
    if ($store) {
        if ($added) { $store.Remove($testCertificate) }
        $store.Close()
    }
}
