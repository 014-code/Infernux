param(
    [Parameter(Mandatory)][string]$ReleaseDirectory,
    [Parameter(Mandatory)][string]$HubVersion,
    [Parameter(Mandatory)][string]$ProductVersion,
    [string]$Thumbprint = '',
    [switch]$Signed
)
$ErrorActionPreference = 'Stop'
$root = (Get-Item -LiteralPath $ReleaseDirectory).FullName
$installer = Join-Path $root "InfernuxHubInstaller-$HubVersion-windows-x64.exe"
$archivePath = Join-Path $root "InfernuxHub-$HubVersion-windows-x64-full.zip"
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("infernux-signature-check-" + [guid]::NewGuid().ToString('N') + '.exe')
try {
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entries = @($archive.Entries | Where-Object { $_.FullName -eq 'Infernux Hub.exe' })
        if ($entries.Count -ne 1) { throw 'Expected exactly one Hub executable in the update archive.' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $temporary)
    } finally { $archive.Dispose() }
    foreach ($file in @($installer, $temporary)) {
        if ($Signed) {
            & "$PSScriptRoot/verify_windows_signature.ps1" -Path $file -Thumbprint $Thumbprint -ProductVersion $ProductVersion
        } elseif ((Get-AuthenticodeSignature -LiteralPath $file).Status -ne 'NotSigned') {
            throw 'Unsigned publication cannot contain test-signed or unexpectedly signed executables.'
        }
    }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
