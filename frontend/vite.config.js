import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 关键：禁用 Vite/http-proxy 对响应的缓冲
        // 确保 SSE 分片到达后立即转发给浏览器
        configure: (proxy, options) => {
          proxy.on('proxyRes', (proxyRes, req, res) => {
            // ★ 使用 includes 判断 SSE 类型，兼容浏览器多样化的 Accept 头
            const reqAccept = String(req.headers.accept || req.headers['accept'] || '')
            const resContentType = String(proxyRes.headers['content-type'] || '')
            const isSSE = reqAccept.includes('text/event-stream') ||
                          reqAccept.includes('text/event-stream') ||
                          resContentType.includes('text/event-stream')
            if (isSSE) {
              // 禁用代理层缓冲
              proxyRes.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
              proxyRes.headers['X-Accel-Buffering'] = 'no'
              proxyRes.headers['Connection'] = 'keep-alive'
              // 强制立即发送响应头，不等待数据攒够
              res.flushHeaders()
              // 禁用 Node.js socket 的 Nagle 算法（TCP 无延迟发送）
              if (res.socket) {
                res.socket.setNoDelay(true)
              }
            }
          })
          // 请求发出前也加禁用缓冲头
          proxy.on('proxyReq', (proxyReq, req, res) => {
            proxyReq.setHeader('X-Accel-Buffering', 'no')
            proxyReq.setHeader('Cache-Control', 'no-cache')
            proxyReq.setHeader('Connection', 'keep-alive')
          })
        },
      },
      '/api/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      }
    }
  }
})
