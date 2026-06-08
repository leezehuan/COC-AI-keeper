import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/coc/',
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        monitor: 'monitor.html'
      }
    }
  },
  server: {
    port: Number(process.env.VITE_PORT || 5173),
    proxy: {
      '/coc/api': 'http://localhost:8000',
      '/coc/assets': 'http://localhost:8000'
    }
  }
});
