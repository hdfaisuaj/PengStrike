<template>
  <div class="session-list">
    <div class="page-header">
      <h2 class="page-title">会话列表</h2>
      <div style="display:flex;align-items:center;gap:8px">
        <el-tag v-if="ws.connectionState.value === 'connected'" type="success" size="small" effect="dark">已连接</el-tag>
        <el-tag v-else-if="ws.connectionState.value === 'connecting'" type="warning" size="small" effect="dark">
          <el-icon class="is-loading"><Loading /></el-icon> 连接中
        </el-tag>
        <el-tag v-else type="danger" size="small" effect="dark">未连接</el-tag>
        <el-button type="primary" @click="createSession">
          <el-icon><Plus /></el-icon>新建会话
        </el-button>
      </div>
    </div>

    <el-card shadow="hover">
      <el-table v-if="!loading && sessions.length > 0" :data="sessions" stripe @row-click="goToDetail">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="会话名称" min-width="180">
          <template #default="{ row }">
            <el-link type="primary">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="target" label="目标" min-width="160" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="toolCount" label="工具数" width="80" align="center" />
        <el-table-column prop="createdAt" label="创建时间" width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click.stop="goToDetail(row)">
              详情
            </el-button>
            <el-button text type="danger" size="small" @click.stop="deleteSession(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else-if="loading" class="loading-state">
        <el-skeleton :rows="5" animated />
      </div>
      <div v-else class="empty-state">
        <el-empty description="暂无会话" />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Loading } from '@element-plus/icons-vue'
import request from '../utils/axios'
import { getGlobalWebSocket } from '../utils/websocket'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const sessions = ref([])
const ws = getGlobalWebSocket()

// ★ 自动刷新定时器
let _refreshTimer = null

// ★ 监听路由中的 refresh 参数，从详情返回时自动刷新
watch(() => route.query.refresh, (val) => {
  if (val) {
    fetchSessions()
  }
})

function statusType(status) {
  const map = { active: 'success', paused: 'warning', completed: 'info', failed: 'danger' }
  return map[status] || 'info'
}

function statusLabel(status) {
  const map = { active: '运行中', paused: '已暂停', completed: '已完成', failed: '失败' }
  return map[status] || status
}

async function fetchSessions() {
  loading.value = true
  try {
    const res = await request.get('/api/sessions')
    sessions.value = Array.isArray(res) ? res : (res?.items || [])
  } catch (e) {
    console.error('[SessionList] 获取会话列表失败:', e)
    sessions.value = []
    // 仅在首次加载失败时提示，避免频繁弹窗
    if (!sessions.value.length && !loading.value) {
      ElMessage.error('加载会话列表失败，请确认后端服务是否正常运行')
    }
  } finally {
    loading.value = false
  }
}

async function createSession() {
  ElMessageBox.prompt('请输入目标 IP 或域名', '新建会话', {
    confirmButtonText: '下一步',
    cancelButtonText: '取消',
    inputPattern: /.+/,
    inputErrorMessage: '目标不能为空',
    inputPlaceholder: '192.168.1.1'
  }).then(async ({ value: target }) => {
    // 第二步：输入自定义名称
    ElMessageBox.prompt(`目标: ${target}\n（可选）输入会话名称，留空则使用目标地址`, '会话名称', {
      confirmButtonText: '创建',
      cancelButtonText: '使用默认名称',
      inputValue: target,
    }).then(async ({ value: name }) => {
      await doCreate(target, name)
    }).catch(async () => {
      await doCreate(target, target)
    })
  }).catch(() => {})
}

async function doCreate(target, name) {
  try {
    const res = await request.post('/api/sessions', { target, name })
    ElMessage.success('创建成功')
    router.push({ name: 'SessionDetail', params: { id: res.id } })
  } catch (e) {
    ElMessage.error('创建失败: ' + (e.response?.data?.detail || e.message))
  }
}

function goToDetail(row) {
  router.push({ name: 'SessionDetail', params: { id: row.id } })
}

async function deleteSession(row) {
  ElMessageBox.confirm(`确定删除会话 "${row.name}"？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(async () => {
    try {
      await request.delete(`/api/sessions/${row.id}`)
      ElMessage.success('删除成功')
      fetchSessions()
    } catch {}
  }).catch(() => {})
}

onMounted(() => {
  fetchSessions()
  // ★ 每 30 秒自动刷新列表（显示最新状态）
  _refreshTimer = window.setInterval(fetchSessions, 30000)
})

// ★ 组件卸载时清除定时器
onUnmounted(() => {
  if (_refreshTimer) {
    window.clearInterval(_refreshTimer)
    _refreshTimer = null
  }
})
</script>

<style scoped>
.session-list {
  max-width: 1200px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.page-title {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.loading-state, .empty-state {
  padding: 40px 0;
}

.el-table {
  cursor: pointer;
}
</style>