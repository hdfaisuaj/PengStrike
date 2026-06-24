<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { getGlobalWebSocket, loadFrontendConfig } from './utils/websocket'

onMounted(async () => {
  // 先拉前端配置（后端地址），再连 WebSocket
  await loadFrontendConfig()
  const ws = getGlobalWebSocket()
  ws.connect()
})
</script>