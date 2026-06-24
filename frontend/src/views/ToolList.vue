<template>
  <div class="tool-list">
    <div class="page-header">
      <h2 class="page-title">工具列表</h2>
      <div class="header-search">
        <el-input
          v-model="searchQuery"
          placeholder="搜索工具..."
          clearable
          :prefix-icon="Search"
          style="width: 280px"
          @input="handleSearch"
        />
      </div>
    </div>
    <el-card shadow="hover">
      <div v-if="loading" class="loading-state">
        <el-skeleton :rows="5" animated />
      </div>
      <div v-else-if="filteredTools.length === 0" class="empty-state">
        <el-empty description="未找到匹配的工具" />
      </div>
      <el-table v-else :data="filteredTools" stripe>
        <el-table-column prop="name" label="工具名称" min-width="160">
          <template #default="{ row }">
            <el-link type="primary">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="280" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.available ? 'success' : 'info'" size="small" effect="plain">
              {{ row.available ? '可用' : '不可用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              size="small"
              :disabled="!row.available"
              @click="handleExecuteTool(row)"
            >
              <el-icon><CaretRight /></el-icon>执行
            </el-button>
            <el-button text size="small" @click="viewToolDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 工具详情对话框 -->
    <el-dialog v-model="detailVisible" title="工具详情" width="600px">
      <el-descriptions v-if="selectedTool" :column="1" border>
        <el-descriptions-item label="名称">{{ selectedTool.name }}</el-descriptions-item>
        <el-descriptions-item label="分类">{{ selectedTool.category }}</el-descriptions-item>
        <el-descriptions-item label="描述">{{ selectedTool.description }}</el-descriptions-item>
        <el-descriptions-item label="用法">{{ selectedTool.usage || '无' }}</el-descriptions-item>
        <el-descriptions-item label="是否可用">
          <el-tag :type="selectedTool.available ? 'success' : 'info'" size="small">
            {{ selectedTool.available ? '可用' : '不可用' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- 高危工具二次确认对话框 -->
    <el-dialog
      v-model="showConfirmDialog"
      title="⚠️ 高危操作确认"
      width="500px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <div style="display: flex; align-items: center; gap: 12px; padding: 20px 0;">
        <el-icon size="40" color="#f56c6c"><Warning /></el-icon>
        <div>
          <p style="margin: 0; font-size: 16px; font-weight: 500; color: #f56c6c;">
            您即将执行高危工具【{{ pendingTool?.name }}】
          </p>
          <p style="margin: 8px 0 0 0; color: #606266;">
            该操作可能对目标系统造成影响，请确认您已获得合法授权！
          </p>
        </div>
      </div>
      <template #footer>
        <el-button @click="cancelExecute">取消</el-button>
        <el-button type="danger" @click="confirmExecute">确认执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, CaretRight, Warning } from '@element-plus/icons-vue'
import request from '../utils/axios'

// 高危工具列表 - 需要二次确认
const HIGH_RISK_TOOLS = ['sqlmap', 'hydra', 'msfconsole', 'metasploit', 'exploit', 'nc', 'ncat', 'crackmapexec', 'smbexec', 'winexe']

const loading = ref(false)
const searchQuery = ref('')
const tools = ref([])
const detailVisible = ref(false)
const selectedTool = ref(null)
const showConfirmDialog = ref(false)
const pendingTool = ref(null)
const pendingParams = ref('')

const filteredTools = computed(() => {
  if (!searchQuery.value) return tools.value
  const q = searchQuery.value.toLowerCase()
  return tools.value.filter(t =>
    t.name.toLowerCase().includes(q) ||
    t.category.toLowerCase().includes(q) ||
    t.description.toLowerCase().includes(q)
  )
})

// 默认工具数据
const defaultTools = [
  { name: 'nmap', category: '信息收集', description: '网络端口扫描和服务探测工具', available: true, usage: 'nmap -sV -p- target_ip' },
  { name: 'ping', category: '信息收集', description: 'ICMP网络连通性测试工具', available: true, usage: 'ping target_ip' },
  { name: 'whois', category: '信息收集', description: '域名注册信息查询工具', available: true, usage: 'whois domain.com' },
  { name: 'dig', category: '信息收集', description: 'DNS域名解析查询工具', available: true, usage: 'dig domain.com' },
  { name: 'curl', category: '信息收集', description: 'HTTP请求测试工具', available: true, usage: 'curl -I http://target.com' },
  { name: 'sqlmap', category: '漏洞利用', description: '自动化SQL注入检测和利用工具（高危）', available: true, usage: 'sqlmap -u "http://target.com/page?id=1"' },
  { name: 'xsser', category: '漏洞利用', description: 'XSS跨站脚本攻击检测工具', available: true, usage: 'xsser --url "http://target.com"' },
  { name: 'hydra', category: '密码破解', description: '多协议暴力破解工具（高危）', available: true, usage: 'hydra -l user -P pass.txt target ssh' },
  { name: 'metasploit', category: '漏洞利用', description: '渗透测试框架（高危）', available: true, usage: 'msfconsole' },
  { name: 'nikto', category: '漏洞扫描', description: 'Web服务器漏洞扫描工具', available: true, usage: 'nikto -h http://target.com' },
  { name: 'dirb', category: '信息收集', description: 'Web目录爆破工具', available: true, usage: 'dirb http://target.com' },
  { name: 'burpsuite', category: '代理工具', description: 'Web应用安全测试代理工具', available: true, usage: 'burpsuite' },
  { name: 'crackmapexec', category: '漏洞利用', description: '网络服务批量检测工具（高危）', available: true, usage: 'crackmapexec smb target_ip' },
  { name: 'empire', category: '后渗透', description: 'PowerShell后渗透框架（高危）', available: true, usage: 'empire' },
  { name: 'bloodhound', category: '后渗透', description: 'Active Directory域分析工具', available: true, usage: 'bloodhound' }
]

async function fetchTools() {
  loading.value = true
  try {
    const res = await request.get('/api/tools')
    tools.value = res.list || res || defaultTools
  } catch {
    // 后端不可用时使用默认数据
    tools.value = defaultTools
  } finally {
    loading.value = false
  }
}

function handleSearch() {}

function viewToolDetail(row) {
  selectedTool.value = row
  detailVisible.value = true
}

// 处理工具执行 - 先检查是否高危
function handleExecuteTool(row) {
  // 检查是否为高危工具
  const isHighRisk = HIGH_RISK_TOOLS.includes(row.name.toLowerCase())
  
  if (isHighRisk) {
    // 高危工具 - 先弹出参数输入框，再确认
    ElMessageBox.prompt(`请输入目标参数执行 ${row.name}`, '执行工具', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputPlaceholder: '目标地址/IP'
    }).then(async ({ value }) => {
      // 保存待执行的工具和参数
      pendingTool.value = row
      pendingParams.value = value
      // 弹出高危确认对话框
      showConfirmDialog.value = true
    }).catch(() => {})
  } else {
    // 非高危工具 - 直接执行
    ElMessageBox.prompt(`请输入目标参数执行 ${row.name}`, '执行工具', {
      confirmButtonText: '执行',
      cancelButtonText: '取消',
      inputPlaceholder: '目标地址/IP'
    }).then(async ({ value }) => {
      await doExecuteTool(row, value)
    }).catch(() => {})
  }
}

// 确认执行高危工具
async function confirmExecute() {
  if (!pendingTool.value) return
  
  try {
    await doExecuteTool(pendingTool.value, pendingParams.value)
    ElMessage.success(`高危工具 ${pendingTool.value.name} 已启动执行`)
  } finally {
    showConfirmDialog.value = false
    pendingTool.value = null
    pendingParams.value = ''
  }
}

// 取消执行高危工具
function cancelExecute() {
  showConfirmDialog.value = false
  pendingTool.value = null
  pendingParams.value = ''
  ElMessage.info('已取消执行高危工具')
}

// 真正执行工具的核心函数
async function doExecuteTool(tool, params) {
  try {
    const res = await request.post(`/api/tools/${encodeURIComponent(tool.name)}/execute`, {
      arguments: { target: params }
    })
    ElMessage.success(`工具 ${tool.name} 已启动执行`)
  } catch (err) {
    ElMessage.error('工具执行失败: ' + (err.response?.data?.detail || err.message || '未知错误'))
  }
}

onMounted(() => {
  fetchTools()
})
</script>

<style scoped>
.tool-list {
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
