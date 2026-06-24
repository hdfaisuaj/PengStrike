<template>
  <div class="role-list">
    <div class="page-header">
      <h2 class="page-title">角色管理</h2>
    </div>

    <el-row :gutter="20">
      <el-col v-for="role in roles" :key="role.name" :xs="24" :sm="12" :md="8" :lg="6">
        <el-card
          :class="['role-card', { 'role-active': currentRole === role.name }]"
          shadow="hover"
          @click="switchRole(role)"
        >
          <div class="role-content">
            <div class="role-icon">
              <el-avatar :size="48" :icon="UserFilled" :style="{ backgroundColor: role.color || '#409eff' }" />
            </div>
            <div class="role-info">
              <h3 class="role-name">{{ role.name }}</h3>
              <p class="role-desc">{{ role.description || '暂无描述' }}</p>
            </div>
            <div class="role-status">
              <el-tag v-if="currentRole === role.name" type="success" size="small" effect="dark">当前</el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="hover" class="role-detail-card" v-if="selectedRole">
      <template #header>
        <div class="card-header">
          <span>角色详情</span>
          <el-button text @click="selectedRole = null">关闭</el-button>
        </div>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="角色名称">{{ selectedRole.name }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="currentRole === selectedRole.name ? 'success' : 'info'" size="small">
            {{ currentRole === selectedRole.name ? '当前使用' : '未启用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ selectedRole.description }}</el-descriptions-item>
        <el-descriptions-item label="可用工具" :span="2">
          <el-tag v-for="tool in selectedRole.tools" :key="tool" size="small" style="margin: 2px">
            {{ tool }}
          </el-tag>
          <span v-if="!selectedRole.tools?.length">无</span>
        </el-descriptions-item>
      </el-descriptions>
      <div class="role-actions" v-if="currentRole !== selectedRole.name">
        <el-button type="primary" @click="switchRole(selectedRole)">
          <el-icon><Switch /></el-icon>切换到此角色
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UserFilled, Switch } from '@element-plus/icons-vue'
import request from '../utils/axios'

const roles = ref([])
const currentRole = ref('')
const selectedRole = ref(null)

// 默认角色数据（后端不可用时使用，name 与后端 RoleRegistry 一致）
const defaultRoles = [
  {
    name: 'web_pentester',
    description: 'Web渗透测试专家，专注于Web应用安全评估',
    color: '#409eff',
    tools: ['nmap', 'sqlmap', 'burp', 'dirb', 'nikto', 'xsser', 'ffuf', 'wfuzz']
  },
  {
    name: 'internal_pentester',
    description: '内网渗透专家，专注于内网横向移动和权限提升',
    color: '#67c23a',
    tools: ['nmap', 'metasploit', 'crackmapexec', 'impacket', 'bloodhound', 'responder']
  },
  {
    name: 'vuln_scanner',
    description: '漏洞扫描专家，专注于自动化漏洞发现和验证',
    color: '#e6a23c',
    tools: ['nmap', 'nessus', 'openvas', 'nuclei', 'masscan', 'nikto']
  },
  {
    name: 'red_team',
    description: '红队作战专家，专注于高级持久化和社会工程',
    color: '#f56c6c',
    tools: ['metasploit', 'cobaltstrike', 'empire', 'covenant', 'gophish', 'bloodhound']
  },
  {
    name: 'forensics',
    description: '取证分析专家，专注于数字取证和恶意软件分析',
    color: '#909399',
    tools: ['volatility', 'autopsy', 'wireshark', 'foremost', 'binwalk', 'strings']
  }
]

async function fetchRoles() {
  const savedRole = localStorage.getItem('pentest_current_role')
  try {
    const res = await request.get('/api/roles')
    // 兼容两种格式：axios响应 {data:{code,data,msg}} 或拦截器已解包
    const outer = (res && res.data !== undefined) ? res.data : res
    const payload = (outer && outer.code !== undefined) ? outer : { code: 0, data: outer, msg: '' }
    const data = payload.data || {}
    roles.value = data.roles || defaultRoles
    currentRole.value = data.current || savedRole || 'web_pentester'
  } catch {
    roles.value = defaultRoles
    currentRole.value = savedRole || 'web_pentester'
  }
}

function switchRole(role) {
  if (currentRole.value === role.name) return
  ElMessageBox.confirm(`确定切换到角色「${role.name}」？`, '切换角色', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'info'
  }).then(async () => {
    try {
      await request.post(`/api/roles/${encodeURIComponent(role.name)}/switch`)
    } catch {
      // 后端不可用时使用localStorage保存
    }
    currentRole.value = role.name
    selectedRole.value = role
    localStorage.setItem('pentest_current_role', role.name)
    ElMessage.success(`已切换到角色「${role.name}」`)
  }).catch(() => {})
}

onMounted(() => {
  fetchRoles()
})
</script>

<style scoped>
.role-list {
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

.role-card {
  margin-bottom: 20px;
  cursor: pointer;
  transition: all 0.2s;
}

.role-card:hover {
  transform: translateY(-2px);
}

.role-active {
  border: 2px solid #67c23a;
}

.role-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.role-info {
  flex: 1;
}

.role-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 4px;
}

.role-desc {
  font-size: 13px;
  color: #909399;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-detail-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.role-actions {
  margin-top: 16px;
  text-align: center;
}
</style>
