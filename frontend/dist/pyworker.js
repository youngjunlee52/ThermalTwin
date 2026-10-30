/* 웹 데모 전용: 브라우저 속 Python(Pyodide)에서 ThermalTwin 엔진(backend/*.py)을 그대로 실행 */
importScripts('https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js');

let api = null;
const status = (text) => postMessage({ type: 'status', text });

async function init(base) {
  try {
    status('Python 계산 엔진 내려받는 중');
    const py = await loadPyodide();
    status('수치 계산 라이브러리(NumPy) 준비 중');
    await py.loadPackage(['numpy', 'sqlite3']);
    status('ThermalTwin 모델·실차 데이터 불러오는 중');
    const files = await (await fetch(base + 'py/manifest.json')).json();
    await Promise.all(files.map(async (f) => {
      const buf = new Uint8Array(await (await fetch(base + 'py/' + f)).arrayBuffer());
      const dir = ('/tt/' + f).split('/').slice(0, -1).join('/');
      py.FS.mkdirTree(dir);
      py.FS.writeFile('/tt/' + f, buf);
    }));
    status('디지털 트윈 시작하는 중');
    py.runPython("import sys, os; os.environ['THERMALTWIN_DB']='off'; sys.path.insert(0, '/tt/backend'); import webapi");
    api = py.pyimport('webapi');
    postMessage({ type: 'ready' });
    setInterval(() => {
      try {
        const s = api.tick();
        if (s) postMessage({ type: 'frame', data: s });
      } catch (e) {
        console.error(e);
      }
    }, 1000);
  } catch (e) {
    postMessage({ type: 'error', text: String(e && e.message ? e.message : e) });
  }
}

onmessage = (ev) => {
  const m = ev.data;
  if (m.type === 'init') init(m.base);
  else if (m.type === 'ws' && api) api.on_message(m.data);
  else if (m.type === 'api') {
    let out;
    try {
      out = api ? JSON.parse(api.api(m.method, m.path, JSON.stringify(m.query), m.body || '')) : [503, 'application/json', '{"detail":"loading"}'];
    } catch (e) {
      out = [500, 'application/json', JSON.stringify({ detail: String(e) })];
    }
    postMessage({ type: 'api', id: m.id, res: out });
  }
};
