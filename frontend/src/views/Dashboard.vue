<template>
  <div class="dashboard">
    <h2 class="page-title">仪表盘</h2>
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <StatusCard
          title="活跃会话"
          :value="stats.activeSessions"
          :icon="ChatDotRound"
          icon-bg="#e6f7ff"
          icon-color="#1890ff"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatusCard
          title="可用工具"
          :value="stats.availableTools"
          :icon="Tools"
          icon-bg="#f6ffed"
          icon-color="#52c41a"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatusCard
          title="已完成任务"
          :value="stats.completedTasks"
          :icon="CircleCheck"
          icon-bg="#fff7e6"
          icon-color="#faad14"
        />
      </el-col>
      <el-col :xs="12" :sm="6">
        <StatusCard
          title="生成报告"
          :value="stats.totalReports"
          :icon="Document"
          icon-bg="#f0f5ff"
          icon-color="#722ed1"
        />
      </el-col>
    </el-row>
    <el-row :gutter="20" class="content-row">
      <el-col :span="16">
        <el-card class="activity-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>最近活动</span>
              <el-button text size="small" @click="refreshActivity">刷新</el-button>
            </div>
          </template>
          <div v-if="activities.length === 0" class="empty-state">
            <el-empty description="暂无活动记录" />
          </div>
          <div v-else class="activity-list">
              <el-timeline>
                <el-timeline-item
                  v-for="(item, index) in activities"
                  :key="index"
                  :timestamp="item.time"
                  :type="item.type"
                  placement="top"
                >
                  <span
                    v-if="item.session_id"
                    class="activity-link"
                    @click="router.push('/sessions/' + item.session_id)"
                  >
                    {{ item.content }}
                  </span>
                  <span v-else>{{ item.content }}</span>
                </el-timeline-item>
              </el-timeline>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="status-card-panel" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>系统状态</span>
            </div>
          </template>
          <div class="sys-status">
            <div class="status-item">
              <span class="status-label">WebSocket</span>
              <el-tag :type="wsConnected ? 'success' : 'danger'" size="small" effect="plain">
                {{ wsConnected ? '已连接' : '未连接' }}
              </el-tag>
            </div>
            <div class="status-item">
              <span class="status-label">后端服务</span>
              <el-tag :type="backendStatus ? 'success' : 'danger'" size="small" effect="plain">
                {{ backendStatus ? '正常运行' : '异常' }}
              </el-tag>
            </div>
            <div class="status-item">
              <span class="status-label">当前角色</span>
              <el-tag type="info" size="small" effect="plain">{{ currentRole || '未设置' }}</el-tag>
            </div>
            <div class="status-item">
              <span class="status-label">运行时长</span>
              <span class="status-value">{{ uptime }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Tools, CircleCheck, Document } from '@element-plus/icons-vue'
import StatusCard from '../components/StatusCard.vue'
import request from '../utils/axios'
import { getGlobalWebSocket } from '../utils/websocket'

const router = useRouter()
const { connected: wsConnected } = getGlobalWebSocket()
const pollTimer = ref(null)

function formatTime(isoStr) {
  if (!isoStr) return '--'
  try {
    const d = new Date(isoStr)
    const pad = n => String(n).padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch { return '--' }
}

const stats = ref({
  activeSessions: 0,
  availableTools: 0,
  completedTasks: 0,
  totalReports: 0
})

const activities = ref([])
const backendStatus = ref(false)
const currentRole = ref('')
const uptime = ref('计算中...')
let uptimeTimer = null
let uptimeBaseSeconds = 0

function formatUptime(seconds) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) {
    return `${hours}小时 ${minutes}分 ${secs}秒`
  } else if (minutes > 0) {
    return `${minutes}分 ${secs}秒`
  }
  return `${secs}秒`
}

async function fetchSessionsCount() {
  try {
    const res = await request.get('/api/sessions?page=1')
    const data = res.data || res
    const sessions = data?.data || data?.items || []
    stats.value.activeSessions = sessions.filter(s => s.status === 'active').length
    stats.value.completedTasks = sessions.filter(s => s.status === 'completed').length
  } catch { /* polling error - ignore */ }
}

async function fetchDashboard() {
  // 从localStorage读取保存的角色和运行时长
  const savedRole = localStorage.getItem('pentest_current_role')
  const savedUptime = localStorage.getItem('pentest_uptime_seconds')
  const savedStartTime = localStorage.getItem('pentest_start_time')
  
  // ★ 独立健康检查：用 /api/health（不会失败），和 /api/stats 解耦
  try {
    await request.get('/api/health')
    backendStatus.value = true
  } catch {
    backendStatus.value = false
  }
  
  // ★ 获取统计数据（可以失败，不影响健康状态）
  try {
    const res = await request.get('/api/stats')
    const data = res.data || res
    stats.value = {
      activeSessions: data.sessions || 0,
      availableTools: data.tools || 0,
      completedTasks: data.completedTasks || 0,
      totalReports: data.totalReports || 0
    }
    currentRole.value = data.currentRoleName || data.currentRole || savedRole || 'web_pentester'
    
    // 使用后端返回的真实运行时长
    uptimeBaseSeconds = data.uptime_seconds || parseInt(savedUptime) || 0
    uptime.value = data.uptime || formatUptime(uptimeBaseSeconds)
  } catch (e) {
    console.error('获取统计失败（不影响健康状态）:', e)
    // 后端不可用时使用localStorage数据
    currentRole.value = savedRole || 'web_pentester'
    
    // 计算运行时长：如果有开始时间，计算差值；否则使用保存的时长
    if (savedStartTime) {
      const now = Date.now()
      const start = parseInt(savedStartTime)
      uptimeBaseSeconds = Math.floor((now - start) / 1000)
    } else {
      uptimeBaseSeconds = parseInt(savedUptime) || 0
      localStorage.setItem('pentest_start_time', Date.now().toString())
    }
    uptime.value = formatUptime(uptimeBaseSeconds)
  }
  
  // ★ 获取活动列表（新活动 API）
  try {
    const ares = await request.get('/api/activity')
    const adata = ares.data || ares
    const rawItems = Array.isArray(adata?.items) ? adata.items : []
    activities.value = rawItems.map(a => {
      let type = 'info'
      if (a.type === 'session_completed') type = 'success'
      else if (a.type === 'session_failed') type = 'danger'
      else if (a.type === 'tool_execution') type = 'primary'
      return {
        time: formatTime(a.time),
        type: type,
        content: a.content || '',
        session_id: a.session_id || '',
      }
    })
  } catch { /* activities 保持空 */ }
  
  // 启动本地计时器，每秒更新运行时长并保存
  if (!uptimeTimer) {
    uptimeTimer = setInterval(() => {
      uptimeBaseSeconds++
      uptime.value = formatUptime(uptimeBaseSeconds)
      localStorage.setItem('pentest_uptime_seconds', uptimeBaseSeconds.toString())
    }, 1000)
  }
}

function refreshActivity() {
  ElMessage.info('正在刷新...')
  fetchDashboard()
}

onMounted(() => {
  fetchDashboard()
  // ★ 5秒轮询获取活跃会话数
  pollTimer.value = setInterval(() => {
    fetchSessionsCount()
  }, 5000)
})

onUnmounted(() => {
  if (uptimeTimer) {
    clearInterval(uptimeTimer)
    uptimeTimer = null
  }
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
}

.page-title {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 20px;
}

.stats-row {
  margin-bottom: 20px;
}

.content-row {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.empty-state {
  padding: 40px 0;
}

.activity-list {
  padding: 0 8px;
}

.sys-status {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.status-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-label {
  font-size: 14px;
  color: #606266;
}

.status-value {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}

.activity-link {
  cursor: pointer;
  color: #409eff;
  transition: color 0.2s;
}
.activity-link:hover {
  color: #66b1ff;
  text-decoration: underline;
}
</style>
