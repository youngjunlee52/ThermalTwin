import type { ReactNode } from 'react';
import { Card } from '../ui';

function A({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline">{children}</a>;
}

const FEATURES: { paper: string; idea: string; done: string }[] = [
  { paper: 'Wang et al. 2023 (BINN)', idea: '물리식 잔차를 손실에 넣고 물리 파라미터를 함께 학습',
    done: 'PINN 코어온도 가상센서 (NumPy). 열전달 식 C·dTc/dt = q − (Tc−Ts)/R_in 잔차 + 일부 라벨. R_in·C 역추정 → 트윈 보정. 노화 저항식으로 SOH 추정' },
  { paper: 'Shen et al. 2025 (CNN 트윈)', idea: '센서가 적은 대형 팩 온도장을 물리+데이터로 실시간 추정',
    done: '셀 전압 96개 기반 셀 저항 식별 + 학습 공분산(GP) 보정 → 임의 센서 배치에서 96셀 표면·코어·불확실성 추정' },
  { paper: 'Jiang et al. 2024 (MOBO)', idea: '냉각판 형상의 온도 vs 펌프 동력 trade-off',
    done: '냉각판 모델(굽힘·입출구 손실) + 펌프 성능곡선·운전 한계 + 245개 설계 전수 탐색 + 최고온도·온도편차·펌프 전력 3목적 파레토' },
  { paper: 'Liu et al. 2025 (300대 EV 데이터)', idea: '실차 BMS 데이터 공개 (셀 전압·온도 프로브)',
    done: '실차 로그 재생·변환기·업로드, 프로브 일부만 쓰고 나머지로 검증 (교차검증)' },
];

// 사용자가 알아야 할 사용 범위 (과제 목록이 아님)
const SCOPE: { title: string; desc: string }[] = [
  { title: '제어 명령은 권고용', desc: '충전 출력 상한·냉각수 유량 명령은 BMS 에 대한 권고입니다. 실제 차량을 직접 제어하려면 BMS 쪽에 자동차 기능안전(ISO 26262) 장치가 있어야 합니다.' },
  { title: '정확도 수치의 근거', desc: '코어온도·SOH 정확도는 시뮬레이션 차량(셀당 2노드)과 공개 실차 데이터(온도 1 °C 단위)로 확인한 값입니다. 다른 차량에 쓰려면 그 차량 데이터로 다시 확인해야 합니다.' },
  { title: '가정값이 들어간 부분', desc: '실차 프로브 위치(모듈당 4개 균등), 펌프 성능곡선, 열폭주 모델의 음극 질량(셀 무게의 30 %)은 공개 자료가 없어 가정했습니다. 화면에 "가정"으로 표시돼 있습니다.' },
  { title: '볼 수 없는 것', desc: '온도센서가 없는 셀 하나만 냉각이 안 되는 경우는 센서 수와 상관없이 놓칠 수 있습니다 (R&D › 센서 배치 분석 참고).' },
];

const DATA: { group: string; items: { name: ReactNode; desc: string }[] }[] = [
  {
    group: '실제 전기차 주행·충전 데이터',
    items: [
      { name: <A href="https://github.com/HoraceLiu1010/Multi-modal-SOH-estimation-framework">★ 포함됨: 300대 EV 데이터 샘플 (MIT)</A>,
        desc: '이 앱의 실차 재생 데이터. 한 차량 206일분 중 충전 2구간·주행 1구간을 변환해 포함' },
      { name: <A href="https://www.nature.com/articles/s41467-025-56485-7">300대 EV 필드 데이터 (Nature Communications, 2025)</A>,
        desc: '96S·155 Ah NCM 팩 300대, 3년, SOC·팩 전류/전압·셀 전압. 데이터: ivstskl.changan.com.cn/?p=2697' },
      { name: <A href="https://doi.org/10.17632/7vdkzpnjgj.2">Audi e-tron BMS 로그 (Pozzato et al., Joule 2023)</A>,
        desc: '95 kWh 팩, 약 3,750시간, 주행·충전 중 팩 전류·전압·모듈 온도·SOC (Mendeley Data)' },
      { name: <A href="https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles">BMW i3 실주행 72회 (TUM, IEEE DataPort)</A>,
        desc: '배터리 전압·전류·온도·SOC + 난방 회로 데이터. DOI 10.21227/6jr9-5235 (무료 IEEE 계정 필요)' },
      { name: <A href="https://www.anl.gov/taps/downloadable-dynamometer-database">Argonne D3 (Downloadable Dynamometer Database)</A>,
        desc: '미국 DOE 연구소의 EV 차대동력계 시험 데이터. 사용 시 출처 표기 필요' },
    ],
  },
  {
    group: '셀 시험 데이터 (노화·온도)',
    items: [
      { name: <A href="https://www.batteryarchive.org">Battery Archive (Sandia 등)</A>, desc: 'BINN 논문이 사용한 Sandia LFP/NCA/NMC 수명 데이터 포함, 여러 기관 데이터 통합' },
      { name: <A href="https://doi.org/10.1038/s41560-019-0356-8">Severson et al. 2019 급속충전 124셀 (Nature Energy)</A>, desc: '급속충전 조건별 수명 + 내부저항. BINN 의 노화 저항식 근거. 데이터: data.matr.io/1' },
      { name: <A href="https://calce.umd.edu/battery-data">CALCE Battery Data (메릴랜드대)</A>, desc: '여러 셀의 사이클·온도·드라이브 사이클 시험' },
      { name: <A href="https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/">NASA PCoE 배터리 데이터</A>, desc: '18650 노화 데이터 (셀 온도 포함)' },
      { name: <A href="https://howey.eng.ox.ac.uk/data-and-code/">Oxford Battery Degradation Dataset</A>, desc: '파우치 셀 드라이브 사이클 노화' },
      { name: <A href="https://data.mendeley.com/datasets/b5mj79w5w9/2">LG 18650HG2 (McMaster, Mendeley Data)</A>, desc: '여러 온도에서 EV 드라이브 사이클 시험, SOC 추정 벤치마크용' },
    ],
  },
  {
    group: '물리 모델 파라미터 · 팩 사양',
    items: [
      { name: <A href="https://docs.pybamm.org/en/latest/source/api/parameters/parameter_sets.html">PyBaMM 파라미터 세트</A>, desc: 'Chen2020(LG M50 21700) 등 실측 기반 전기화학·열 파라미터 (Chen et al., J. Electrochem. Soc. 2020)' },
      { name: <A href="https://www.batterydesign.net/">BatteryDesign.net</A>, desc: '셀 1,550종·양산 팩 1,800종 데이터베이스(엑셀), 냉각 방식·발열 해설' },
      { name: <A href="https://doi.org/10.1016/j.ensm.2017.05.013">Feng et al. 2018, 열폭주 메커니즘 리뷰</A>, desc: 'SEI 분해·분리막 붕괴 등 열폭주 단계별 온도 — 위험 판정 기준의 근거' },
    ],
  },
  {
    group: '국내 공개 데이터',
    items: [
      { name: <A href="https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=71347">AI-Hub 자율주행 고장진단 데이터</A>, desc: '아이오닉·코나·니로 OBD 배터리 셀 전압/편차 (정상·주의·결함 라벨). 내국인만 신청 가능' },
      { name: <A href="https://www.data.go.kr/dcat/metadata/15153595">한국환경공단 전기차 충전 합성데이터 (공공데이터포털)</A>, desc: '충전 건별 충전량·시간 — 충전 시나리오 설계용 (배터리 내부 데이터는 아님)' },
    ],
  },
];

const PARAMS: [string, string, string][] = [
  ['셀', '156 Ah, 3.7 V NCM 각형, 2.75 kg, 비열 980 J/kgK', 'Shen et al. 2025 Table 1, 4'],
  ['팩', '96S (8모듈 × 12셀), 약 55 kWh, 355 V', '가정 (300대 EV 데이터와 같은 96S 구성)'],
  ['내부저항', 'R0 = 0.35 mΩ(25 °C) × Arrhenius × SOC 계수 + B1(e^(B2·δ)−1)', '형태: Wang et al. 2023 식 (19)–(20), 값은 가정'],
  ['발열', 'q = I²R0 − I·T·dU/dT (Bernardi)', 'Shen et al. 2025 식 (1), Wang et al. 2023 식 (2)'],
  ['열회로', '코어 ↔ 표면 R_in 0.12 K/W, 갭필러 2 mm(k 2.5), 셀 간 단열패드', 'Forgez 모델(Wang 2023 식 3), Shen 2025 구조'],
  ['냉각판', '모듈당 병렬 서펜타인 3채널, 물-글리콜 50/50, Shah–London 마찰계수·Nusselt, Hausen 발달영역 보정', 'Jiang et al. 2024 식 (1)–(5), (10)–(11)'],
  ['판정 기준', '코어 45 °C 주의 / 55 °C 위험, 셀 간 ΔT 목표 < 5 K, 10 °C 미만 급속충전 제한', '설계 한계값(가정), ΔT 기준은 Jiang 2024 인용'],
];

export default function AboutView() {
  return (
    <div className="space-y-4">
      <Card title="논문 · 데이터 → 이 프로젝트에 반영한 것">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-2 py-2 font-medium">논문</th>
                <th className="px-2 py-2 font-medium">핵심 아이디어</th>
                <th className="px-2 py-2 font-medium">ThermalTwin 구현</th>
              </tr>
            </thead>
            <tbody className="text-ink-2">
              {FEATURES.map((f) => (
                <tr key={f.paper} className="border-b border-line/60 align-top">
                  <td className="px-2 py-2 font-medium text-ink whitespace-nowrap">{f.paper}</td>
                  <td className="px-2 py-2">{f.idea}</td>
                  <td className="px-2 py-2">{f.done}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="모델 가정과 파라미터">
          <ul className="space-y-2 text-sm">
            {PARAMS.map(([k, v, src]) => (
              <li key={k} className="grid grid-cols-[5.5rem_1fr] gap-2">
                <span className="text-muted">{k}</span>
                <span className="text-ink-2">{v}<br /><span className="text-[11px] text-muted">출처: {src}</span></span>
              </li>
            ))}
          </ul>
          <p className="mt-3 rounded-lg bg-panel-2 p-3 text-xs leading-relaxed text-ink-2">
            ⚠ 이 모델은 실시간 계산을 위한 축약 모델이며, 일부 값은 문헌 형태를 따른 가정값입니다.
            실제 차량 성능을 주장하려면 아래 공개 데이터나 실측으로 파라미터를 보정하고 오차를 보고해야 합니다.
          </p>
        </Card>

        <Card title="사용 범위 — 이것만은 알고 쓰세요">
          <ul className="space-y-2 text-sm text-ink-2">
            {SCOPE.map((r) => (
              <li key={r.title}><b className="text-ink">{r.title}</b> — {r.desc}</li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="실제 전기차 배터리 데이터 출처">
        <div className="grid gap-5 lg:grid-cols-2">
          {DATA.map((g) => (
            <div key={g.group}>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{g.group}</h4>
              <ul className="space-y-2.5 text-sm">
                {g.items.map((it, i) => (
                  <li key={i}>
                    <div>{it.name}</div>
                    <div className="text-xs text-ink-2">{it.desc}</div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
