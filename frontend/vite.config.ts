import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // 前后端联调：/api 代理到后台（docs/backend/integration.md）；VITE_API_PROXY_TARGET 可改，默认本机 8000
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';
  return {
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: { host: '0.0.0.0', port: 5280, strictPort: true, proxy: { '/api': { target: apiTarget, changeOrigin: true } } },
    preview: { host: '0.0.0.0', port: 5281, strictPort: true },
    optimizeDeps: {
      include: ['ant-design-vue', '@ant-design/icons-vue', 'echarts', 'vue-echarts', '@antv/x6', 'dayjs', 'pinia', 'vue-router'],
    },
    build: { chunkSizeWarningLimit: 4000 },
  };
});
