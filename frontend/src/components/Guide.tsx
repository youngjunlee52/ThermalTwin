/** 처음 사용 안내 — 처음 열면 보이고, 화면 위 '도움말' 로 다시 볼 수 있음 */
export default function Guide({ onClose }: { onClose: () => void }) {
  const steps = [
    { n: 1, title: '무엇을 볼지 고르기', body: "왼쪽 맨 위 '무엇을 볼까요?'에서 고릅니다. 처음에는 '가상 차량'(컴퓨터 속 전기차가 급속충전 중)을 그대로 두세요." },
    { n: 2, title: '배터리 상태 보기', body: "맨 위 큰 글씨가 지금 상태입니다. 초록 '안전'이면 정상이고, 노란 '주의'·빨간 '위험'이면 이유와 '내가 할 일'이 함께 나옵니다." },
    { n: 3, title: '설정 바꿔 보기', body: "왼쪽 '충전 세기'를 최대로, '바깥 온도'를 40 °C 로 올려 보세요. 배터리가 뜨거워지면 프로그램이 스스로 충전을 줄이는 모습을 볼 수 있습니다." },
  ];
  return (
    <section role="dialog" aria-labelledby="guide-title" className="rounded-2xl border border-accent bg-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="guide-title" className="text-lg font-semibold text-ink">처음이신가요? 3단계면 됩니다</h3>
          <p className="text-sm text-ink-2">ThermalTwin 은 전기차 배터리 안쪽 온도를 계산해 너무 뜨거워지기 전에 충전·냉각을 조절하도록 돕는 프로그램입니다.</p>
        </div>
        <button type="button" onClick={onClose} className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white">시작하기</button>
      </div>
      <ol className="mt-4 grid gap-3 md:grid-cols-3">
        {steps.map((s) => (
          <li key={s.n} className="rounded-xl bg-panel-2 p-4">
            <p className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="grid size-6 place-items-center rounded-full bg-accent text-xs text-white">{s.n}</span>{s.title}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-2">{s.body}</p>
          </li>
        ))}
      </ol>
      <ul className="mt-3 space-y-1 text-xs text-muted">
        <li>• 낯선 단어 옆 <span className="inline-grid size-3.5 place-items-center rounded-full bg-panel-2 text-[9px] font-bold ring-1 ring-line">i</span> 에 마우스를 올리면 쉬운 설명이 나옵니다.</li>
        <li>• 화면 위 <b className="text-ink-2">운용</b> 은 현장용(상태·제어), <b className="text-ink-2">R&amp;D</b> 는 분석용(모델·센서·냉각판 설계)입니다.</li>
        <li>• 실제 차량을 연결하려면 '차량 · 연동' 탭을 보세요. 끝낼 때는 검은 서버 창을 닫으면 됩니다.</li>
      </ul>
    </section>
  );
}
