ThermalTwin v0.4 — 전기차 배터리 열관리 디지털 트윈 (Windows 배포판)

[실행]
  ThermalTwin.exe 를 더블클릭하세요. 검은 창이 뜨고, 잠시 뒤 브라우저에서 화면이 열립니다.
  Python 등 다른 설치는 필요 없습니다. 끝낼 때는 검은 창을 닫으세요.
  * 압축을 먼저 푼 뒤 실행하세요 (zip 안에서 바로 실행하면 동작하지 않습니다).
  * ThermalTwin.exe 옆의 _internal 폴더를 지우거나 옮기지 마세요.

[처음 실행할 때 경고가 뜨면]
  "Windows의 PC 보호" 창 → '추가 정보' → '실행' 을 누르세요.
  "스마트 앱 컨트롤이 이 앱을 차단했습니다" 가 뜨면, 이 배포판은 디지털 서명이 없어 그 PC 에서는 실행할 수 없습니다.

[가상 CAN 차량 시연]
  명령 프롬프트에서:  ThermalTwin.exe can-demo
  화면 위 '차량' 목록에서 can_demo 를 고르세요.

[기록 위치]
  %LOCALAPPDATA%\ThermalTwin  (logs: 처리 기록, datasets: 업로드한 실차 데이터)

[데이터 출처]
  실차 데이터: Liu et al., Nature Communications 16, 1137 (2025), MIT License
