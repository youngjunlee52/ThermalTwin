# ThermalTwin.exe 코드 서명 안내

## 왜 필요한가

서명 없는 `ThermalTwin.exe`는 Windows **스마트 앱 컨트롤**이 켜진 PC에서 실행 자체가 막힙니다. 받는 사람이 '그래도 실행'을 고를 수도 없습니다. 이 개발 PC도 스마트 앱 컨트롤이 켜져 있어서, 빌드한 exe가 "did not meet the Enterprise signing level requirements"로 차단됐습니다.

Microsoft 문서에 따르면 스마트 앱 컨트롤은 **Microsoft 신뢰 루트 프로그램에 속한 인증기관(CA)이 발급한 코드 서명 인증서**로 서명한 앱의 실행을 허용합니다. 스마트 앱 컨트롤이 꺼진 PC에서도 서명이 있으면 "알 수 없는 게시자" 경고가 줄어듭니다.

> 한계: 공식 문서는 신뢰된 인증서 서명을 허용 조건으로 설명합니다. 하지만 Microsoft Q&A에는 서명했는데도 새 게시자라서 차단됐다는 사용자 보고가 있습니다. 서명 후 반드시 스마트 앱 컨트롤이 켜진 이 PC에서 실행해 확인하세요.

## 1. 인증서 받기 (한국 개인 개발자 기준)

| 방법 | 대상·조건 | 비용 (2026년 조사, 구매 전 공식 가격 확인) | 키 보관 |
|---|---|---|---|
| **Microsoft Artifact Signing** (옛 Trusted Signing) | 개인은 **미국·캐나다만**. 한국은 **조직(사업자)** 만 가능 | 월 약 $9.99 (유료 Azure 구독 필요) | Microsoft 클라우드 |
| **OV 코드 서명 인증서** (Sectigo, Certum, DigiCert 등) | 개인 신청 가능 (신분·주소 확인) | Sectigo 약 $220/년, Certum 클라우드형 더 저렴하다는 보고, DigiCert $439/년~ | USB 토큰 또는 클라우드 HSM (2023년부터 의무) |
| **SignPath Foundation** (오픈소스 무료) | OSI 오픈소스 라이선스, 공개 저장소, CI 자동 빌드, 서명 정책 공개. 게시자 이름이 SignPath Foundation이 됨 | 무료 | SignPath 클라우드 |

추천:
- **개인으로 지금 배포하려면 OV 인증서**가 현실적입니다. 실물 USB 토큰이 부담되면 클라우드형(예: Certum SimplySign)을 고르세요. 앱이 PC에 가상 카드 리더를 만들어 주므로 이 안내의 절차가 그대로 동작합니다.
- **GitHub에 오픈소스로 공개할 계획이면 SignPath**를 검토하세요. 이 경우 빌드를 GitHub Actions로 옮겨야 합니다(이 문서의 로컬 서명 절차와 다름).
- 사업자가 있으면 Artifact Signing이 가장 저렴합니다. 다만 `signtool` + Azure 서명 플러그인 방식이라 아래 스크립트와는 별도로 설정해야 합니다.

공통 주의:
- 2026년부터 코드 서명 인증서의 최대 유효기간이 짧아져 매년 갱신해야 한다는 판매처 안내가 있습니다. 갱신하면 다시 빌드·서명하세요.
- 신청할 때 신분증, 주소 증빙, 전화 확인 등을 요구합니다. 발급까지 며칠 걸릴 수 있습니다.

## 2. 인증서가 PC에서 보이는지 확인

토큰을 꽂거나 클라우드 서명 앱에 로그인한 뒤, PowerShell에서 아래 명령을 실행합니다.

```powershell
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Format-List Subject, Thumbprint, NotAfter
```

여기 나온 `Thumbprint`(지문)를 다음 단계에서 씁니다. PFX 파일로 받았다면 이 단계는 건너뜁니다.

## 3. 빌드 + 서명 + 압축 (한 번에)

명령 프롬프트(cmd)에서 프로젝트 폴더로 이동한 뒤 아래 중 하나를 실행합니다.

```bat
set THERMALTWIN_SIGN_THUMBPRINT=여기에지문
build_exe.bat
```

```bat
set THERMALTWIN_SIGN_PFX=C:\certs\codesign.pfx
build_exe.bat
```

`build_exe.bat`이 하는 일:
1. PyInstaller로 `release\ThermalTwin\` 폴더를 만듭니다.
2. `packaging\sign_release.ps1`이 서명 없는 `.exe/.dll/.pyd`(현재 66개)에 SHA-256 서명과 타임스탬프를 붙입니다. 이미 Python 재단이나 Microsoft 서명이 있는 30개는 그대로 둡니다.
3. 바이너리 96개가 모두 유효하게 서명됐는지 검증합니다. 하나라도 실패하면 압축하지 않고 멈춥니다.
4. `release\ThermalTwin_v0.4_Windows.zip`을 만듭니다.

서명은 타임스탬프 서버와 통신하므로 약 3분 걸립니다. 서명 단계만 따로 실행하려면 아래 명령을 씁니다.

```bat
powershell -ExecutionPolicy Bypass -File packaging\sign_release.ps1 -Thumbprint 여기에지문
```

타임스탬프 서버는 기본이 Sectigo이고, `-TimestampServer http://timestamp.digicert.com`처럼 바꿀 수 있습니다. 타임스탬프가 있으면 인증서가 만료된 뒤에도 이미 서명한 파일은 유효합니다.

## 4. 배포 전 확인

1. `release\ThermalTwin\ThermalTwin.exe` 오른쪽 클릭 → 속성 → **디지털 서명** 탭에 본인 이름이 보이는지 확인합니다.
2. **이 PC(스마트 앱 컨트롤 켜짐)에서 zip을 새 폴더에 풀고 `ThermalTwin.exe`를 더블클릭**합니다. 여기서 열리면 스마트 앱 컨트롤을 통과한 것입니다.
3. 가능하면 다른 PC 한 대에서도 실행해 봅니다.

## 5. 보안 수칙

- PFX 파일과 비밀번호는 프로젝트 폴더나 OneDrive, Git에 두지 마세요. 토큰이나 클라우드 HSM 사용을 권장합니다.
- 서명은 "이 파일을 누가 만들었고 바뀌지 않았다"는 보증입니다. 내가 빌드한 파일에만 서명하세요.
- 인증서가 유출되면 즉시 발급처에 폐지(revoke)를 요청하세요.

## 절차 시험 기록 (2026-10-27)

진짜 인증서 없이 절차만 확인했습니다. 메모리에서 만든 시험용 자체 서명 인증서(저장소에 설치하지 않음)를 썼습니다.
- 바이너리 96개 중 서명 없던 66개 서명, Sectigo 공개 타임스탬프 부착, 기존 Python 재단·Microsoft 서명 30개 유지를 확인했습니다.
- 서명 후에도 exe 내부 PyInstaller 묶음(12개 항목, launcher 포함)이 정상으로 읽혔습니다.
- 시험용 인증서는 신뢰되지 않으므로 스마트 앱 컨트롤 통과 여부는 **진짜 인증서로만** 확인할 수 있습니다.

## 참고 자료

- Microsoft, [Sign your app for Smart App Control compliance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)
- Microsoft, [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- Microsoft, [Artifact Signing 빠른 시작 (자격 국가)](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
- [SignPath Foundation (오픈소스 무료 서명)](https://signpath.org/)
- [Sectigo 코드 서명 인증서 가이드 2026](https://sslinsights.com/sectigo-code-signing-certificate-guide/)
- [저가 OV 인증서·Certum 클라우드 서명 후기](https://gabrielmoraru.com/obtaining-cheap-ev-ov-certificates-singing-your-application/)
