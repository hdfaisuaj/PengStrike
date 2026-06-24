<template>
  <div class="report-list">
    <div class="page-header">
      <h2 class="page-title">报告管理</h2>
      <div class="header-actions">
        <el-button type="primary" @click="generateReport">
          <el-icon><Plus /></el-icon>生成报告
        </el-button>
      </div>
    </div>

    <el-card shadow="hover">
      <div v-if="loading" class="loading-state">
        <el-skeleton :rows="5" animated />
      </div>
      <div v-else-if="reports.length === 0" class="empty-state">
        <el-empty description="暂无报告" />
      </div>
      <el-table v-else :data="reports" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="title" label="报告名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary">{{ row.title }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="sessionName" label="关联会话" min-width="160" />
        <el-table-column prop="format" label="格式" width="80">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.format?.toUpperCase() || 'PDF' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="reportStatusType(row.status)" size="small" effect="plain">
              {{ reportStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="生成时间" width="180" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button
              text
              type="primary"
              size="small"
              :disabled="row.status !== 'completed'"
              @click="downloadReport(row)"
            >
              <el-icon><Download /></el-icon>下载
            </el-button>
            <el-button
              text
              type="danger"
              size="small"
              @click="deleteReport(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="generateVisible" title="生成报告" width="500px">
      <el-form :model="reportForm" label-position="top">
        <el-form-item label="报告标题">
          <el-input v-model="reportForm.title" placeholder="输入报告标题" />
        </el-form-item>
        <el-form-item label="关联会话">
          <el-select v-model="reportForm.sessionId" placeholder="选择会话" clearable style="width: 100%">
            <el-option
              v-for="s in sessionOptions"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="输出格式">
          <el-radio-group v-model="reportForm.format">
            <el-radio value="pdf">PDF</el-radio>
            <el-radio value="html">HTML</el-radio>
            <el-radio value="markdown">Markdown</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="generateVisible = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="confirmGenerate">
          生成
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Download } from '@element-plus/icons-vue'
import request from '../utils/axios'

const loading = ref(false)
const reports = ref([])
const generateVisible = ref(false)
const generating = ref(false)
const sessionOptions = ref([])

const reportForm = ref({
  title: '',
  sessionId: '',
  format: 'pdf'
})

function reportStatusType(status) {
  const map = { pending: 'warning', generating: 'warning', completed: 'success', failed: 'danger' }
  return map[status] || 'info'
}

function reportStatusLabel(status) {
  const map = { pending: '等待中', generating: '生成中', completed: '已完成', failed: '失败' }
  return map[status] || status
}

async function fetchReports() {
  loading.value = true
  try {
    const res = await request.get('/api/reports')
    reports.value = res?.items || []
  } catch {
    reports.value = []
  } finally {
    loading.value = false
  }
}

async function fetchSessions() {
  try {
    const res = await request.get('/api/sessions')
    // 兼容不同返回格式
    let list = res.list || res || []
    sessionOptions.value = (Array.isArray(list) ? list : []).map(s => ({
      id: s.id || '',
      name: s.name || s.target || s.id || ''
    }))
  } catch {}
}

function generateReport() {
  reportForm.value = { title: '', sessionId: '', format: 'pdf' }
  generateVisible.value = true
}

async function confirmGenerate() {
  if (!reportForm.value.title) {
    ElMessage.warning('请输入报告标题')
    return
  }
  generating.value = true
  try {
    await request.post('/api/reports/generate', {
      session_id: reportForm.value.sessionId,
      format: reportForm.value.format
    })
    ElMessage.success('报告生成任务已提交')
    generateVisible.value = false
    fetchReports()
  } catch {} finally {
    generating.value = false
  }
}

async function downloadReport(row) {
  const filename = row.filename || `${row.id}.${row.format || 'html'}`
  window.open(`/api/reports/download/${encodeURIComponent(filename)}`, '_blank')
}

async function deleteReport(row) {
  ElMessageBox.confirm(`确定删除报告「${row.title}」？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(async () => {
    try {
      await request.delete(`/api/reports/${encodeURIComponent(row.filename || row.id)}`)
      ElMessage.success('删除成功')
      fetchReports()
    } catch {
      ElMessage.error('后端暂不支持删除报告')
    }
  }).catch(() => {})
}

onMounted(() => {
  fetchReports()
  fetchSessions()
})
</script>

<style scoped>
.report-list {
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
</style>