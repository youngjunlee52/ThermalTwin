import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 개발 모드(npm run dev, http://localhost:5173)에서도 백엔드(8000)와 통신하도록 프록시 설정
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  // 웹 데모(GitHub Pages)는 하위 경로(/ThermalTwin/)에서 열리므로 상대 경로로 빌드
  base: process.env.VITE_WEB_DEMO === '1' ? './' : '/',
  build: { chunkSizeWarningLimit: 1200 },
});
