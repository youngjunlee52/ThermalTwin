# ThermalTwin — 이전 대화 정리 (claude.ai "Title Options" 프로젝트)

claude.ai 프로젝트 "Title Options"에서 나눈 대화를 정리한 문서입니다. 앞으로의 코딩 작업은 이 문서와 `README.md`를 기준으로 이어갑니다.

## 1. 출발점 — v0.1 MVP 요구사항

- 역할: 자동차 열관리(TMS) 소프트웨어 시니어 풀스택 개발자
- 제품: **ThermalTwin**, 전기차 배터리 열관리 디지털 트윈 시뮬레이션 웹 대시보드
- 구조: 루트 아래 `frontend/`, `backend/` 완전 분리
  - Frontend: React (Vite), TypeScript, Tailwind CSS, Recharts
  - Backend: Python FastAPI, Uvicorn, Websockets
- 백엔드
  - CORS 전체 허용
  - `/ws/battery-simulation` 웹소켓에서 1초마다 다음 JSON 전송
    `{ time_step, current_soc, battery_temp, cooling_power, risk_status: 'Safe' | 'Warning' | 'Danger' }`
  - 온도는 서서히 상승하고, 임계 온도를 넘으면 위험 상태가 바뀜
- 프론트엔드
  - 엔지니어링 툴 느낌의 다크 테마
  - 좌측 사이드바: 시뮬레이션 컨트롤 (충전 속도, 냉각수 유량 슬라이더)
  - 우측 메인 대시보드: `useEffect`로 웹소켓 연결, 온도·SOC 실시간 카드, Danger일 때 붉게 깜빡임

### v0.1 설계 요점 (Claude 답변)

- 집중 열용량(lumped) 모델
  - 발열 ∝ C-rate²: `Q_gen = 1.2 kW · C-rate²`
  - 냉각 = `0.015 × 유량 × (T_pack − 25 °C)`, 칠러 최대 8 kW
  - 열용량 150 kJ/K, 팩 75 kWh, 1 tick = 시뮬레이션 30초 (30배 가속)
  - 40 °C 이상 Warning, 50 °C 이상 Danger
  - SOC 100 %가 되면 주행 모드로 전환, 20 %가 되면 다시 충전
- 슬라이더 값은 웹소켓 `{type:"control", charge_power_kw, coolant_flow_lpm}` / `{type:"reset"}`으로 백엔드에 실제 반영
- 프론트 파일: `types/simulation.ts`, `hooks/useBatterySimulation.ts` (2초 자동 재연결, 최근 120 프레임 유지), `components/Sidebar|Dashboard|MetricCard|TrendChart.tsx`
- Tailwind v4 `@theme` 토큰: surface `#0a0e13`, panel `#11171f`, panel-2 `#18202b`, line `#232e3c`, accent `#22d3ee`, `animate-danger-blink`
- v0.1 원본 코드: `Downloads\ThermalTwin_MVP.zip`, `문서\ThermalTwin\ThermalTwin`
- 덧붙인 안내: Windows venv 활성화 (`.venv\Scripts\Activate.ps1`), 실행 정책 오류 시 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 2. v0.2 + 발표 자료

- v0.2 코드: `Downloads\ThermalTwin_v02.zip`, `문서\TTv2\ThermalTwin_v02`
- "ThermalTwin 소개" 슬라이드 12장 (Claude Slides, PDF: `Downloads\ThermalTwin 소개.pdf`)
  1 표지 · 2 배터리 열관리가 어려운 4가지 이유 · 3 논문 3편 → 기능 매핑 · 4 시스템 구조 (차량 → 센서 16개 → 트윈) · 5 물리 모델 (발열식, 노화 저항, 셀 열회로, 냉각판) · 6 실시간 모니터 · 7 SOH 추정 (실제 85/75/100 % → 추정 85.0/73.5/100 %) · 8 위험 판정·BMS 출력 제한 · 9 냉각판 설계 탐색 · 10 기술 스택·실행 · 11 한계·다음 단계 · 12 공개 데이터
- 슬라이드는 v0.2 기준이라 v0.3 결과로 업데이트 가능 (아직 안 함)

## 3. v0.3 요청 (사용자)

1. PINN 신경망 대리모델로 **코어 온도 예측**
2. 센서 데이터로 **열전달 식을 미리 학습**시켜 사용
3. 셀 96개인데 온도센서는 16개뿐. **센서 수·위치에 따라 코어 온도 추정 오차가 달라지는 문제 해결**
4. 단순 예측이 아니라 **실시간 수집·조작** 가능하게
5. **실제 전기차 데이터** 사용
6. 처음 개요대로 가기 위한 **문제점 찾기**

## 4. v0.3 결과 (Claude 답변 요약)

1. **PINN 코어 온도**
   - 표면온도 + 셀 발열 이력 → 코어온도 (NumPy, 학습 완료 가중치 `backend/models/pinn.npz`)
   - 손실에 `C·dTc/dt = q − (Tc−Ts)/R_in` 잔차를 넣고 R_in·C도 함께 학습
   - R_in: 데이터시트 0.120 / 실제 0.1619 / 학습 0.1618, C: 실제 2062 / 학습 1985 (약 4 % 오차)
   - 코어 RMSE: 물리 0.78 K → PINN 신경망 0.30 K → **PINN 하이브리드 0.21 K (대표값)**
   - 신경망과 하이브리드가 2 K 넘게 다르면 경고
2. **센서 수·위치 문제**
   - 셀 전압 96개로 셀별 발열 식별 → 센서 없는 셀도 추정
   - 임의 센서 조합 보정(GP 공분산), 셀별 불확실성 지도, 최적 센서 위치 계산, 히트맵에서 센서 추가·제거
   - 코어 오차 (시뮬레이션)

     | 센서 수 | 4 | 8 | 16 | 32 |
     |---|---|---|---|---|
     | 온도센서만 | 2.3 K | 2.1 K | 2.2 K | 2.2 K |
     | 셀 전압 함께 | 0.46 K | 0.45 K | 0.45 K | 0.45 K |

     (README에는 "센서 4개 0.53 K ~ 32개 0.46 K"로 적혀 있어 값이 조금 다름. 재검증 필요)
   - 오차가 0이 되는 게 아니라 "크게 줄이고 수치로 보여준다"
3. **실시간 수집·조작**
   - `/ws/ingest`, `POST /api/telemetry` 입력 → 매번 제어 명령 (충전 출력 상한, 냉각수 유량) 응답
   - 10분 예측 기반 디레이팅, 펌프 자동 제어, 처리 로그 CSV
   - `4_demo_gateway.bat`: 외부 "차량"이 데이터 송신 + 명령 추종 시연
4. **실차 데이터**
   - Liu et al., *Nature Communications* 16, 1137 (2025), 300대 EV 공개 데이터 (MIT) 중 1대
   - 96S, 셀 전압 96 + 온도 프로브 32, 10초 간격. 충전 2구간 + 주행 1구간 재생, 원본 CSV 업로드 자동 변환
   - 교차검증 (프로브 일부로 나머지 예측): 트윈 0.78–1.13 K vs 평균 대입 1.0–1.65 K
   - 시뮬레이션 학습 설정을 그대로 쓰면 평균보다 나빴고, 실차로 공간 패턴 재학습 후 위 결과

## 5. 남은 문제점

1. 실차 **코어 온도 참값 없음** → PINN은 시뮬레이션 라벨로 학습. 코어 열전대 셀 시험 데이터로 재학습 필요 (실사용까지 가장 큰 과제)
2. 실차 냉각 구조·프로브 위치·OCV 표 비공개 → 모듈당 프로브 4개 균등 배치 가정
3. 공개 데이터 온도 분해능 1 °C → 약 0.3 K 오차 바닥
4. CAN 직접 연결·인증·암호화 미구현 (JSON 게이트웨이까지)
5. 제어 명령은 권고용. 실제 차량 제어는 ISO 26262 기능안전 필요
6. 단일 차량, 메모리 로그만 (다중 차량·시계열 DB 없음), 열폭주 반응 모델 없음
7. 시뮬레이션 "실제 차량"도 셀당 2노드 단순 모델 → 시뮬레이션 검증 수치는 낙관적일 수 있음

## 6. 산출물 위치

| 항목 | 위치 |
|---|---|
| v0.4 프로젝트 (현재 작업 폴더) | `문서\T3\ThermalTwin_v04` |
| v0.3 원본 | `OneDrive\Desktop\ThermalTwin_v03.zip` (`문서\T3\ThermalTwin_v03` 폴더에는 v0.4 수정이 들어가 있음) |
| 검증결과·남은문제 문서 | claude.ai "Title Options" 프로젝트의 `ThermalTwin-v0.3-검증결과-및-남은문제` |
| 스크린샷 | v3_monitor.png, v3_replay.png, v3_센서.png, v3_PINN.png (claude.ai 대화 첨부) |
| 발표 자료 (v0.4 반영) | Artifact "ThermalTwin 소개" https://claude.ai/artifact/3eTvCgz3rN3cMRJWy9poRK |
