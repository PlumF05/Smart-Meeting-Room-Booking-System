import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const connection = {
  host: '127.0.0.1',
  port: 5173,
  strictPort: true,
  proxy: {
    '/api': { target: process.env.SMRBS_API_TARGET || 'http://127.0.0.1:8000' },
  },
}

export default defineConfig({
  plugins: [vue()],
  server: connection,
  preview: connection,
})
