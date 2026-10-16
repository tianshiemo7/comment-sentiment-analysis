import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 后端 FastAPI 推理服务地址。
 * 对应 src/inference/app.py，由 `uvicorn src.inference.app:app --port 8000` 启动。
 */
const BACKEND_TARGET = 'http://localhost:8000'

/** /api 与 /health 由 Vite 代理转发到后端，前端代码里一律使用同源相对路径。 */
const proxy = {
  '/api': { target: BACKEND_TARGET, changeOrigin: true },
  '/health': { target: BACKEND_TARGET, changeOrigin: true },
}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    strictPort: true, // 端口固定 3000：被占用时直接报错，不静默顺延
    proxy,
  },
  // 生产构建预览时同样需要代理，方便本地验证 dist/ 产物
  preview: {
    port: 3000,
    strictPort: true,
    proxy,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
