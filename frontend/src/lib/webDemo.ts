/**
 * 웹 데모 (GitHub Pages): 서버 없이 브라우저 안에서 엔진을 돌립니다.
 * public/pyworker.js 가 Pyodide 로 backend/*.py 를 실행하고, 여기서는 화면 코드가 그대로 쓰도록
 * WebSocket('/ws/battery-simulation') 과 fetch('/api/…') 를 그 워커로 연결합니다.
 */
type Pending = (res: [number, string, string]) => void;

const BASE = import.meta.env.BASE_URL;
let worker: Worker;
let ready = false;
const readyWaiters: (() => void)[] = [];
const pending = new Map<number, Pending>();
const sockets = new Set<FakeSocket>();
let nextId = 1;

function whenReady(): Promise<void> {
  return ready ? Promise.resolve() : new Promise((r) => readyWaiters.push(r));
}

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(_url: string) {
    void whenReady().then(() => {
      if (this.readyState !== 0) return;
      this.readyState = 1;
      sockets.add(this);
      this.onopen?.(new Event('open'));
    });
  }
  send(data: string) {
    if (this.readyState === 1) worker.postMessage({ type: 'ws', data });
  }
  close() {
    this.readyState = 3;
    sockets.delete(this);
  }
  addEventListener() {}
  removeEventListener() {}
}

function apiPath(input: RequestInfo | URL): URL | null {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(raw, window.location.href);
  if (url.origin !== window.location.origin) return null;
  const i = url.pathname.indexOf('/api/');
  return i >= 0 ? new URL(url.pathname.slice(i) + url.search, window.location.origin) : null;
}

async function callApi(url: URL, method: string, body: string): Promise<Response> {
  await whenReady();
  const id = nextId++;
  const res = await new Promise<[number, string, string]>((resolve) => {
    pending.set(id, resolve);
    worker.postMessage({ type: 'api', id, method, path: url.pathname, query: Object.fromEntries(url.searchParams), body });
  });
  return new Response(res[2], { status: res[0], headers: { 'Content-Type': res[1] } });
}

function overlay() {
  const el = document.createElement('div');
  el.id = 'tt-loading';
  el.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;'
    + 'background:rgba(10,14,19,.92);color:#e6edf3;font-family:system-ui,"Malgun Gothic",sans-serif;text-align:center;padding:16px';
  el.innerHTML = '<div style="max-width:440px"><div style="font-size:20px;font-weight:700;margin-bottom:10px">ThermalTwin 웹 데모 준비 중</div>'
    + '<div id="tt-loading-step" style="font-size:14px;color:#22d3ee;margin-bottom:12px">시작하는 중</div>'
    + '<div style="font-size:13px;line-height:1.6;color:#9aa7b4">설치 없이 브라우저 안에서 계산 엔진(Python)을 실행합니다.<br>'
    + '처음 한 번은 20~40초 걸릴 수 있습니다. 창을 닫지 말고 잠시 기다려 주세요.</div></div>';
  document.body.appendChild(el);
  return el;
}

export function installWebDemo() {
  const box = overlay();
  worker = new Worker(`${BASE}pyworker.js`);
  worker.onmessage = (ev: MessageEvent) => {
    const m = ev.data;
    if (m.type === 'frame') {
      for (const s of sockets) s.onmessage?.(new MessageEvent('message', { data: m.data }));
    } else if (m.type === 'api') {
      pending.get(m.id)?.(m.res);
      pending.delete(m.id);
    } else if (m.type === 'status') {
      const step = document.getElementById('tt-loading-step');
      if (step) step.textContent = m.text;
    } else if (m.type === 'ready') {
      ready = true;
      box.remove();
      readyWaiters.splice(0).forEach((r) => r());
    } else if (m.type === 'error') {
      const step = document.getElementById('tt-loading-step');
      if (step) step.textContent = `불러오기 실패: ${m.text} — 새로고침(F5) 해 보세요. 크롬·엣지 최신 버전을 권장합니다.`;
    }
  };
  worker.postMessage({ type: 'init', base: new URL(BASE, window.location.href).href });

  const realWS = window.WebSocket;
  (window as unknown as { WebSocket: unknown }).WebSocket = new Proxy(realWS, {
    construct(target, args: [string, ...unknown[]]) {
      return String(args[0]).includes('/ws/battery-simulation') ? new FakeSocket(args[0]) : new target(...(args as [string]));
    },
  });

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = apiPath(input);
    if (!url) return realFetch(input, init);
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const body = typeof init?.body === 'string' ? init.body : '';
    return callApi(url, method, body);
  };

  // <a href="/api/log.csv"> 같은 내려받기 링크
  document.addEventListener('click', (ev) => {
    const a = (ev.target as HTMLElement | null)?.closest?.('a');
    const url = a && apiPath(a.getAttribute('href') || '');
    if (!url) return;
    ev.preventDefault();
    void callApi(url, 'GET', '').then(async (r) => {
      const blob = new Blob([await r.text()], { type: r.headers.get('Content-Type') || 'text/plain' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = url.pathname.split('/').pop() || 'download';
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    });
  }, true);
}
