import { ref } from 'vue'
import axios from '../utils/axios'

// ★ 全局常量（优化：放宽心跳超时、适配后台标签页节流）
const MAX_RECONNECT_ATTEMPTS = 20
const RECONNECT_INTERVAL = 5000
const HEARTBEAT_INTERVAL = 60000   // 每 60 秒发一次 ping（放宽，避免长命令执行时误判断线）
const HEARTBEAT_TIMEOUT = 180000   // 等待 180 秒响应（适配 nikto/gobuster 等长命令）

// ★ 前端配置缓存（从 /api/config/frontend 获取）
let backendHost = ''
let backendPort = 8000
let configLoaded = false
let configLoading = false
let configCallbacks = []

export function loadFrontendConfig() {
  if (configLoading) {
    return new Promise((resolve) => { configCallbacks.push(resolve) })
  }
  if (configLoaded) {
    return Promise.resolve()
  }
  configLoading = true
  return axios.get('/api/config/frontend')
    .then(res => {
      const data = res && res.data !== undefined ? res.data : res
      backendHost = data.backend_host || window.location.hostname || '127.0.0.1'
      backendPort = data.backend_port || 8000
      configLoaded = true
      configLoading = false
      configCallbacks.forEach(cb => cb())
      configCallbacks = []
    })
    .catch(() => {
      // 获取失败时用当前页面地址推断
      backendHost = window.location.hostname || '127.0.0.1'
      backendPort = 8000
      configLoaded = true
      configLoading = false
      configCallbacks.forEach(cb => cb())
      configCallbacks = []
    })
}

// ★ 模块级全局变量，所有组件共享同一套连接
let ws = null
let isConnecting = false // ★ 新增：连接锁，防止并发创建多个连接
let reconnectAttempts = 0
let reconnectTimer = null
let heartbeatTimer = null
let heartbeatTimeoutTimer = null
let heartbeatLastSentPingTime = 0   // ★ 记录上次发送 ping 的时间，用于计算往返延迟
let intentionalClose = false

// ★ 连接状态枚举值: 'disconnected' | 'connecting' | 'connected' | 'error'
const connectionState = ref('disconnected')
const connected = ref(false)
const messages = ref([])
const onMessageCallback = ref(null)
const messageHandlers = {}  // ★ 新增：按类型注册的消息处理器

// ★ 新增：注册指定类型的消息处理函数
export function registerMessageHandler(type, handler) {
  if (!messageHandlers[type]) {
    messageHandlers[type] = []
  }
  messageHandlers[type].push(handler)
  // 返回取消注册的函数
  return () => {
    messageHandlers[type] = messageHandlers[type].filter(h => h !== handler)
    if (messageHandlers[type].length === 0) {
      delete messageHandlers[type]
    }
  }
}

function connect() {
  // ★ 修改：严格单例检查，同时检查 OPEN 和 CONNECTING 状态
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    console.log('[WebSocket] 单例保护：已有活跃/连接中连接，跳过重复连接，readyState:', ws.readyState)
    return
  }

  // ★ 新增：连接锁，防止并发调用 connect()
  if (isConnecting) {
    console.log('[WebSocket] 连接锁保护：已有连接正在建立中')
    return
  }
  isConnecting = true

  connectionState.value = 'connecting'
  intentionalClose = false

  // ★ 根据前端配置或当前页面地址构造 WebSocket URL
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const hostname = backendHost || window.location.hostname || '127.0.0.1'
  const port = backendPort || 8000
  const wsUrl = `${scheme}//${hostname}:${port}/api/ws`
  // ★ 修改：添加实例ID用于调试追踪
  const connId = Math.random().toString(36).substr(2, 8)
  console.log(`[WS-连接] 发起连接 @ ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}, URL=${wsUrl}, 实例ID=${connId}`)

  ws = new WebSocket(wsUrl)

  ws.onopen = () => {
    const openTs = Date.now()
    console.log(`[WS-连接] ✅ 连接成功 @ ${new Date(openTs).toLocaleTimeString('zh-CN', { hour12: false })}, readyState=${ws.readyState}`)
    isConnecting = false // ★ 新增：重置连接锁
    connected.value = true
    connectionState.value = 'connected'
    reconnectAttempts = 0
    startHeartbeat()
  }

  ws.onmessage = (event) => {
    const recvTs = Date.now()
    if (heartbeatTimeoutTimer) {
      clearTimeout(heartbeatTimeoutTimer)
      heartbeatTimeoutTimer = null
    }
    try {
      const data = JSON.parse(event.data)
      // ★ 修复：响应服务端 ping，完善双向保活机制
      if (data.type === 'ping' && data.server) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          const clientPongTs = Date.now()
          console.log(`[WS-心跳] 收到服务端 ping @ ${new Date(recvTs).toLocaleTimeString('zh-CN', { hour12: false })}, 回复 pong @ ${new Date(clientPongTs).toLocaleTimeString('zh-CN', { hour12: false })}`)
          ws.send(JSON.stringify({ type: 'pong', client: true, timestamp: clientPongTs }))
        }
        return // 服务端 ping/pong 不进入消息列表和回调
      }
      // ★ 客户端 ping 的响应 pong，打详细日志
      if (data.type === 'pong') {
        const lastPingTs = heartbeatLastSentPingTime || recvTs
        console.log(`[WS-心跳] 收到 pong @ ${new Date(recvTs).toLocaleTimeString('zh-CN', { hour12: false })}, 往返耗时=${(recvTs - lastPingTs)}ms`)
        return // pong 消息也不进入消息列表
      }
      // ★ 新增：调用按类型注册的消息处理器
      if (data.type && messageHandlers[data.type]) {
        messageHandlers[data.type].forEach(handler => {
          try {
            handler(data)
          } catch (err) {
            console.error(`[WebSocket] 消息处理器异常 (type=${data.type}):`, err)
          }
        })
      }
      // ★ 修改：优化日志输出，只显示type而非完整对象
      console.log('[WebSocket] 收到消息:', data.type || data)
      messages.value.push(data)
      if (onMessageCallback.value) {
        onMessageCallback.value(data)
      }
    } catch {
      messages.value.push(event.data)
      if (onMessageCallback.value) {
        onMessageCallback.value(event.data)
      }
    }
  }

  ws.onclose = (event) => {
    const closeTs = Date.now()
    // ★ 修改：增加 wasClean 字段输出
    console.log(`[WS-连接] ❌ 连接关闭 @ ${new Date(closeTs).toLocaleTimeString('zh-CN', { hour12: false })}, code=${event.code}, reason=${event.reason || '(空)'}, wasClean=${event.wasClean}, readyState=${event.target?.readyState}`)
    isConnecting = false // ★ 新增：重置连接锁
    connected.value = false
    connectionState.value = 'disconnected'
    stopHeartbeat()

    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }

    // ★ 修复：code:1000 是正常有序关闭（双方握手），跳过自动重连
    //    code:1005 是无关闭帧的异常断开，必须触发重连
    if (event.code === 1000) {
      console.log('[WebSocket] code:1000 为正常关闭，不触发重连')
      ws = null
      return
    }

    if (!intentionalClose && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      const nextTryAt = new Date(Date.now() + RECONNECT_INTERVAL).toLocaleTimeString('zh-CN', { hour12: false })
      console.log(`[WS-重连] ${RECONNECT_INTERVAL/1000}秒后（${nextTryAt}）第 ${reconnectAttempts + 1}/${MAX_RECONNECT_ATTEMPTS} 次尝试重连`)
      reconnectTimer = setTimeout(() => {
        reconnectAttempts++
        connect()
      }, RECONNECT_INTERVAL)
    } else if (!intentionalClose) {
      console.log(`[WS-重连] ⚠ 已达到最大重试次数 (${MAX_RECONNECT_ATTEMPTS})，停止自动重连`)
    }
  }

  ws.onerror = (error) => {
    const errTs = Date.now()
    console.error(`[WS-错误] @ ${new Date(errTs).toLocaleTimeString('zh-CN', { hour12: false })}: WebSocket error, readyState=${ws?.readyState}, type=${error.type}`)
    // ★ 修复：不主动关闭连接。onerror 仅表示发生了错误事件，
    //  不代表连接已断开，很多临时网络错误后连接仍然正常。
    //  如果连接真的断了，onclose 会随后触发，由它处理重连。
    isConnecting = false
    connectionState.value = 'error'
  }
}

function disconnect() {
  intentionalClose = true
  stopHeartbeat()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (ws) {
    ws.close()
    ws = null
  }
  connected.value = false
  connectionState.value = 'disconnected'
  isConnecting = false // ★ 新增：重置连接锁
}

function send(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(typeof data === 'string' ? data : JSON.stringify(data))
  }
}

function onMessage(callback) {
  onMessageCallback.value = callback
}

function clearMessages() {
  messages.value = []
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      const pingTs = Date.now()
      heartbeatLastSentPingTime = pingTs
      console.log(`[WS-心跳] 发送 ping @ ${new Date(pingTs).toLocaleTimeString('zh-CN', { hour12: false })}, 超时=${HEARTBEAT_TIMEOUT/1000}s`)
      ws.send(JSON.stringify({ type: 'ping', timestamp: pingTs }))
      heartbeatTimeoutTimer = setTimeout(() => {
        const timeoutTs = Date.now()
        console.log(`[WS-心跳] ⚠ 心跳超时 @ ${new Date(timeoutTs).toLocaleTimeString('zh-CN', { hour12: false })}, 距离上次 ping ${(timeoutTs - pingTs)/1000}s 未收到响应，关闭连接`)
        if (ws) ws.close()
      }, HEARTBEAT_TIMEOUT)
    }
  }, HEARTBEAT_INTERVAL)
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
  if (heartbeatTimeoutTimer) {
    clearTimeout(heartbeatTimeoutTimer)
    heartbeatTimeoutTimer = null
  }
  heartbeatLastSentPingTime = 0
}

// ★ 全局单例实例缓存
let globalWSInstance = null

export function getGlobalWebSocket() {
  if (!globalWSInstance) {
    console.log('[WebSocket] 创建全局单例实例') // ★ 新增：调试日志
    globalWSInstance = {
      connectionState,
      connected,
      messages,
      connect,
      disconnect,
      send,
      onMessage,
      clearMessages,
      loadFrontendConfig,
    }
  }
  return globalWSInstance
}

// ★ 模块加载日志
console.log(`[WS-模块] 已加载 @ ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}, 心跳间隔=${HEARTBEAT_INTERVAL/1000}s, 超时=${HEARTBEAT_TIMEOUT/1000}s`)
// ★ 不再自动连接：Vite 热更新会重复执行，改为在 App.vue 的 onMounted 中手动调用 connect()
