import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { host: '0.0.0.0', port: 5280, strictPort: true },
  preview: { host: '0.0.0.0', port: 5281, strictPort: true },
  optimizeDeps: {
    include: ['ant-design-vue', '@ant-design/icons-vue', 'echarts', 'vue-echarts', '@antv/x6', 'dayjs', 'pinia', 'vue-router'],
  },
  build: { chunkSizeWarningLimit: 4000 },
});
