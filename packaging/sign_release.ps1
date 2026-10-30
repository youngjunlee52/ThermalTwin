<#
ThermalTwin 배포판 코드 서명 — release\ThermalTwin 안의 서명 없는 .exe/.dll/.pyd 에 모두 서명하고 검증합니다.

  이미 서명된 파일(Python 재단·Microsoft 서명)은 그대로 둡니다.
  스마트 앱 컨트롤은 실행 파일뿐 아니라 함께 불러오는 DLL·pyd 도 검사하므로 전부 서명합니다.
  타임스탬프를 넣어, 인증서가 만료된 뒤에도 서명이 유효하게 남습니다.

사용 (인증서가 Windows 인증서 저장소에 있을 때 — USB 토큰·Certum SimplySign 등은 연결하면 저장소에 나타남):
  powershell -ExecutionPolicy Bypass -File packaging\sign_release.ps1 -Thumbprint <인증서 지문>
PFX 파일일 때:
  powershell -ExecutionPolicy Bypass -File packaging\sign_release.ps1 -PfxPath C:\certs\codesign.pfx
  (비밀번호는 실행 중에 묻습니다)
인증서 지문 찾기:
  Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Format-List Subject, Thumbprint, NotAfter
#>
param(
    [string]$Path = (Join-Path $PSScriptRoot "..\release\ThermalTwin"),
    [string]$Thumbprint,
    [string]$PfxPath,
    [System.Security.SecureString]$PfxPassword,
    [string]$TimestampServer = "http://timestamp.sectigo.com",
    [switch]$NoTimestamp,
    [switch]$AllowUntrusted     # 시험용 자체 서명 인증서로 절차만 확인할 때
)
$ErrorActionPreference = "Stop"
$CodeSigningOid = "1.3.6.1.5.5.7.3.3"

function Get-SigningCert {
    if ($Thumbprint) {
        $t = ($Thumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
        $c = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My | Where-Object Thumbprint -eq $t | Select-Object -First 1
        if (-not $c) { throw "인증서 저장소에서 지문 $t 를 찾지 못했습니다. USB 토큰·서명 앱이 연결돼 있는지 확인하세요." }
        return $c
    }
    if ($PfxPath) {
        if (-not $PfxPassword) { $PfxPassword = Read-Host "PFX 비밀번호" -AsSecureString }
        $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2((Resolve-Path $PfxPath).Path, $PfxPassword, $flags)
    }
    $all = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.NotAfter -gt (Get-Date) })
    if ($all.Count -eq 1) { return $all[0] }
    throw "인증서를 지정하세요: -Thumbprint <지문> 또는 -PfxPath <파일>. 저장소의 코드 서명 인증서 $($all.Count)개."
}

$cert = Get-SigningCert
if (-not $cert.HasPrivateKey) { throw "인증서에 개인 키가 없습니다 (토큰 연결·PFX 확인)." }
if (-not ($cert.EnhancedKeyUsageList.ObjectId -contains $CodeSigningOid)) { throw "코드 서명 용도(EKU)가 없는 인증서입니다: $($cert.Subject)" }
if ($cert.NotAfter -lt (Get-Date)) { throw "만료된 인증서입니다 ($($cert.NotAfter))." }
Write-Host "서명 인증서: $($cert.Subject)  (만료 $($cert.NotAfter.ToString('yyyy-MM-dd')))"

$root = (Resolve-Path $Path).Path
$files = Get-ChildItem $root -Recurse -Include *.exe, *.dll, *.pyd
$todo = $files | Where-Object { (Get-AuthenticodeSignature $_.FullName).Status -ne "Valid" }
Write-Host "바이너리 $($files.Count)개 중 서명할 파일 $(@($todo).Count)개 (이미 서명된 $($files.Count - @($todo).Count)개는 그대로)"

$i = 0
foreach ($f in $todo) {
    $i++
    $opts = @{ FilePath = $f.FullName; Certificate = $cert; HashAlgorithm = "SHA256" }
    if (-not $NoTimestamp) { $opts.TimestampServer = $TimestampServer }
    for ($try = 1; $try -le 3; $try++) {
        $r = Set-AuthenticodeSignature @opts
        if ($r.Status -eq "Valid" -or ($AllowUntrusted -and $r.SignerCertificate)) { break }
        Start-Sleep -Seconds (2 * $try)     # 타임스탬프 서버 일시 오류 대비
    }
    Write-Progress -Activity "코드 서명" -Status $f.Name -PercentComplete (100 * $i / [Math]::Max(1, @($todo).Count))
}
Write-Progress -Activity "코드 서명" -Completed

# 검증: 모든 바이너리가 유효한 서명을 가져야 함
$bad = @()
foreach ($f in $files) {
    $s = Get-AuthenticodeSignature $f.FullName
    $ok = $s.Status -eq "Valid" -or ($AllowUntrusted -and $s.SignerCertificate -and $s.SignerCertificate.Thumbprint -eq $cert.Thumbprint)
    if (-not $ok) { $bad += "$($s.Status)  $($f.FullName.Substring($root.Length + 1))" }
    elseif (-not $NoTimestamp -and $s.SignerCertificate.Thumbprint -eq $cert.Thumbprint -and -not $s.TimeStamperCertificate) {
        $bad += "타임스탬프 없음  $($f.FullName.Substring($root.Length + 1))"
    }
}
if ($bad.Count) {
    Write-Host "서명 확인 실패 $($bad.Count)개:" -ForegroundColor Red
    $bad | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" }
    exit 1
}
Write-Host "서명 완료: 바이너리 $($files.Count)개 모두 서명 확인" -ForegroundColor Green
if ($AllowUntrusted) { Write-Host "(시험용 인증서 — 다른 PC 에서는 신뢰되지 않습니다)" -ForegroundColor Yellow }
exit 0
