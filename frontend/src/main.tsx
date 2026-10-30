import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App';
import { installWebDemo } from './lib/webDemo';

// GitHub Pages 웹 데모 빌드에서만: 서버 대신 브라우저 속 Python 엔진 사용
if (import.meta.env.VITE_WEB_DEMO === '1') installWebDemo();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
