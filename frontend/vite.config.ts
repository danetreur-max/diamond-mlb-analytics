import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({ mode }) => ({ plugins: [react()], cacheDir: '.vite-cache', server: { proxy: { '/api': loadEnv(mode, '.', '').VITE_API_TARGET || 'http://127.0.0.1:8000' } } }));
