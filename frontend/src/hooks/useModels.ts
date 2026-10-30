import { useEffect, useState } from 'react';
import type { ModelsReport } from '../types';

/** PINN 학습 보고서 · 센서 배치 곡선 · 실차 검증 결과 (서버 models/ 폴더) */
export function useModels(): ModelsReport | null {
  const [data, setData] = useState<ModelsReport | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => fetch('/api/models').then((r) => r.json()).then((d) => alive && setData(d)).catch(() => {
      if (alive) window.setTimeout(load, 3000);
    });
    load();
    return () => { alive = false; };
  }, []);
  return data;
}
