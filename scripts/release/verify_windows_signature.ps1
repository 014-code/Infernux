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
try {
    if ($TestCertificate) {
        if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
            throw 'Test certificate trust is restricted to disposable GitHub-hosted runners.'
        }
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', 'CurrentUser')
        $store.Open('ReadWrite')
        if ($store.Certificates.Find('FindByThumbprint', $Thumbprint, $false).Count -eq 0) {
            $store.Add($signature.SignerCertificate)
            $added = $true
        }
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
        if ($added) { $store.Remove($signature.SignerCertificate) }
        $store.Close()
    }
}
