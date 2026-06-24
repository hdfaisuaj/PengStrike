<template>
  <div class="settings-container">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- LLM API 配置 - 自定义模型 -->
      <el-tab-pane label="🤖 LLM配置" name="llm">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>当前使用模型</span>
            </div>
          </template>

          <!-- 顶部：当前模型选择下拉框 -->
          <el-form label-width="140px" class="config-form">
            <el-form-item label="选择模型">
              <el-select
                v-model="activeModelId"
                placeholder="请选择已添加的模型"
                style="width: 100%"
                @change="onActiveModelChange"
                :disabled="customModels.length === 0"
              >
                <el-option
                  v-for="item in customModels"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                >
                  <span>{{ item.name }}</span>
                  <span class="model-option-sub">{{ item.model }}</span>
                </el-option>
              </el-select>
            </el-form-item>
            <el-form-item v-if="activeModel">
              <el-descriptions :column="2" border size="small" style="width: 100%">
                <el-descriptions-item label="API 地址" :span="2">
                  <code class="desc-code">{{ activeModel.api_base }}</code>
                </el-descriptions-item>
                <el-descriptions-item label="模型标识" :span="1">
                  <el-tag>{{ activeModel.model }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="状态" :span="1">
                  <el-tag type="success">使用中</el-tag>
                </el-descriptions-item>
              </el-descriptions>
            </el-form-item>
            <el-form-item v-else>
              <el-empty description="暂无选中模型，请在下方添加" :image-size="80" />
            </el-form-item>
          </el-form>
        </el-card>

        <!-- ★ 备用模型配置（主 API 失败时自动降级） -->
        <el-card class="settings-card" style="margin-top:16px">
          <template #header>
            <div class="card-header">
              <span>🔄 备用模型（可选）</span>
              <el-tag v-if="fallbackConfigured" type="success" size="small">已配置</el-tag>
              <el-tag v-else type="info" size="small">未配置</el-tag>
            </div>
          </template>
          <div style="color:#8b949e;font-size:12px;margin-bottom:12px">
            主 API 调用连续失败时，自动切换到备用模型，确保渗透测试不中断。
          </div>
          <el-form label-width="140px" class="config-form">
            <el-form-item label="API 地址">
              <el-input v-model="fallbackForm.api_base" placeholder="https://api.deepseek.com/v1" clearable />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input v-model="fallbackForm.api_key" type="password" show-password placeholder="sk-..." clearable />
            </el-form-item>
            <el-form-item label="模型标识">
              <el-input v-model="fallbackForm.model" placeholder="deepseek-chat / qwen-plus" clearable />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveFallbackConfig" :loading="savingFallback">
                💾 保存备用模型
              </el-button>
              <el-button v-if="fallbackConfigured" type="danger" plain @click="clearFallbackConfig">
                清除备用
              </el-button>
            </el-form-item>
          </el-form>

          <!-- ★ 新增：从已添加模型一键导入 -->
          <div v-if="customModels.length > 0" class="fallback-import">
            <el-divider content-position="center">或者从已添加模型导入</el-divider>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
              <el-select
                v-model="importFallbackModelId"
                placeholder="选择已添加的模型"
                style="flex:1;min-width:200px"
                clearable
                @change="onImportFallbackModel"
              >
                <el-option
                  v-for="item in importableModels"
                  :key="item.id"
                  :label="item.name"
                  :value="item.id"
                >
                  <span>{{ item.name }}</span>
                  <span class="model-option-sub">{{ item.model }} · {{ item.api_base }}</span>
                </el-option>
              </el-select>
              <el-button
                type="success"
                :disabled="!importFallbackModelId"
                @click="fillFallbackFromSelected"
              >
                ⬇️ 一键填入备用模型
              </el-button>
            </div>
            <div v-if="importedModelInfo" class="imported-info">
              <el-tag type="success" size="small" effect="plain" closable @close="clearImportedFallback">
                ✅ 已导入: {{ importedModelInfo.name }} ({{ importedModelInfo.model }})
              </el-tag>
            </div>
          </div>
        </el-card>

        <el-divider content-position="center">添加自定义LLM</el-divider>

        <!-- 中部：新增自定义LLM表单 -->
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>添加自定义模型</span>
            </div>
          </template>
          <el-form
            ref="formRef"
            :model="formData"
            :rules="formRules"
            label-width="140px"
            class="config-form"
          >
            <el-form-item label="模型名称" prop="name">
              <el-input
                v-model="formData.name"
                placeholder="例如：我的本地模型"
                clearable
              />
              <span class="form-tip">展示用名称，方便识别</span>
            </el-form-item>
            <el-form-item label="API 地址" prop="api_base">
              <el-input
                v-model="formData.api_base"
                placeholder="http://localhost:3001/v1"
                clearable
              />
              <span class="form-tip">完整的 API 请求地址</span>
            </el-form-item>
            <el-form-item label="API Key" prop="api_key">
              <el-input
                v-model="formData.api_key"
                type="password"
                show-password
                placeholder="sk-..."
                clearable
              />
            </el-form-item>
            <el-form-item label="模型标识" prop="model">
              <el-input
                v-model="formData.model"
                placeholder="gpt-4o / qwen-plus / deepseek-chat"
                clearable
              />
              <span class="form-tip">接口调用时使用的模型字段名</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleAdd" :loading="adding">
                ➕ 添加
              </el-button>
              <el-button @click="resetForm">
                🔄 重置
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-divider content-position="center">已添加的LLM列表 ({{ customModels.length }})</el-divider>

        <!-- 底部：已添加LLM列表 -->
        <el-card class="settings-card">
          <el-table
            :data="customModels"
            style="width: 100%"
            empty-text="暂无自定义模型，请在上方添加"
            stripe
          >
            <el-table-column label="模型名称" width="180">
              <template #default="{ row }">
                <div class="model-name-cell">
                  <el-icon v-if="row.id === activeModelId" class="active-icon" color="#67C23A">
                    <Check />
                  </el-icon>
                  <span>{{ row.name }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="API 地址" min-width="240">
              <template #default="{ row }">
                <code class="desc-code">{{ row.api_base }}</code>
              </template>
            </el-table-column>
            <el-table-column label="模型标识" width="160">
              <template #default="{ row }">
                <el-tag size="small">{{ row.model }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  :type="row.id === activeModelId ? 'success' : 'default'"
                  :disabled="row.id === activeModelId"
                  @click="setActive(row.id)"
                >
                  {{ row.id === activeModelId ? '使用中' : '设为当前' }}
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  @click="handleDelete(row.id)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- AutoPilot 配置 -->
      <el-tab-pane label="🚀 AutoPilot" name="autopilot">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>自动巡航 (AutoPilot)</span>
              <el-tag :type="autopilotStatus.enabled ? 'success' : 'info'">
                {{ autopilotStatus.enabled ? '已开启' : '已关闭' }}
              </el-tag>
            </div>
          </template>
          <el-form label-width="180px" class="config-form">
            <el-form-item label="AutoPilot 开关">
              <el-switch
                v-model="autopilotStatus.enabled"
                active-text="开启"
                inactive-text="关闭"
                @change="toggleAutopilot"
              />
            </el-form-item>
            <el-form-item label="最大执行步数">
              <el-input-number v-model="systemConfig.auto_pilot_max_steps" :min="5" :max="100" @change="onFieldChange('system','auto_pilot_max_steps',$event)" />
            </el-form-item>
            <el-form-item label="LLM 输出最大 Token">
              <el-input-number v-model="systemConfig.llm_max_tokens" :min="256" :max="16384" :step="256" @change="onFieldChange('system','llm_max_tokens',$event)" />
            </el-form-item>
            <el-form-item label="上下文最大 Token">
              <el-input-number v-model="systemConfig.context_max_tokens" :min="1024" :max="32768" :step="1024" @change="onFieldChange('system','context_max_tokens',$event)" />
            </el-form-item>
            <el-form-item label="保留最近对话轮数">
              <el-input-number v-model="systemConfig.context_reserve_recent" :min="2" :max="20" @change="onFieldChange('system','context_reserve_recent',$event)" />
            </el-form-item>
            <el-form-item label="当前状态">
              <el-tag :type="autopilotStatus.status === 'running' ? 'success' : 'info'">
                {{ autopilotStatus.status === 'running' ? '执行中' : '空闲' }}
              </el-tag>
            </el-form-item>
            <el-form-item label="当前步数">
              <span>{{ autopilotStatus.current_step }} / {{ autopilotStatus.max_steps }}</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveSystemConfig" :loading="saving">
                💾 保存配置
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- 实时日志 -->
      <el-tab-pane label="📝 实时日志" name="logs">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>系统日志</span>
              <el-button text size="small" @click="refreshLogs">🔄 刷新</el-button>
            </div>
          </template>
          <div class="log-controls">
            <el-select v-model="logFilter" size="small" style="width: 150px" @change="refreshLogs">
              <el-option label="全部日志" value="all" />
              <el-option label="系统日志" value="pengstrike" />
              <el-option label="安全日志" value="security" />
            </el-select>
            <el-input-number v-model="logLines" :min="50" :max="500" size="small" @change="refreshLogs" />
          </div>
          <div class="log-container">
            <div v-for="(log, index) in logs" :key="index" class="log-item" :class="log.level">
              <span class="log-time">{{ log.timestamp }}</span>
              <span class="log-source">[{{ log.source }}]</span>
              <span class="log-message">{{ log.message }}</span>
            </div>
            <el-empty v-if="logs.length === 0" description="暂无日志记录" />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- MCP 服务器 -->
      <el-tab-pane label="🌐 MCP服务器" name="mcp">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>MCP 服务器管理</span>
              <el-tag :type="mcpStatus.running ? 'success' : 'danger'">
                {{ mcpStatus.running ? '运行中' : '已停止' }}
              </el-tag>
            </div>
          </template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="服务地址">
              <code>{{ mcpStatus.host }}:{{ mcpStatus.port }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="SSE端点">
              <code>{{ mcpStatus.endpoint }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="运行状态">
              <el-tag :type="mcpStatus.running ? 'success' : 'danger'">
                {{ mcpStatus.running ? '正常运行' : '服务未启动' }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <div class="mcp-actions">
            <el-button
              type="success"
              @click="startMCP"
              :loading="mcpLoading"
              :disabled="mcpStatus.running"
            >
              ▶️ 启动服务器
            </el-button>
            <el-button
              type="danger"
              @click="stopMCP"
              :loading="mcpLoading"
              :disabled="!mcpStatus.running"
            >
              ⏹️ 停止服务器
            </el-button>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 安全配置 -->
      <el-tab-pane label="🛡️ 安全配置" name="security">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>安全防护配置</span>
            </div>
          </template>
          <el-form label-width="180px" class="config-form">
            <el-form-item label="AST 语法注入检测">
              <el-switch v-model="securityConfig.ast_enabled" active-text="开启" inactive-text="关闭" @change="onFieldChange('security','ast_enabled',$event)" />
            </el-form-item>
            <el-form-item label="异常行为检测">
              <el-switch v-model="securityConfig.anomaly_detection_enabled" active-text="开启" inactive-text="关闭" @change="onFieldChange('security','anomaly_detection_enabled',$event)" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveSecurityConfig" :loading="saving">
                💾 保存安全配置
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- 系统配置 -->
      <el-tab-pane label="⚙️ 系统设置" name="system">
        <el-card class="settings-card">
          <template #header>
            <div class="card-header">
              <span>系统设置</span>
            </div>
          </template>
          <el-form label-width="180px" class="config-form">
            <el-form-item label="命令执行超时(秒)">
              <el-input-number v-model="systemConfig.command_timeout" :min="60" :max="1800" @change="onFieldChange('system','command_timeout',$event)" />
            </el-form-item>
            <el-form-item label="日志级别">
              <el-select v-model="systemConfig.log_level" @change="onFieldChange('system','log_level',$event)">
                <el-option label="DEBUG" value="DEBUG" />
                <el-option label="INFO" value="INFO" />
                <el-option label="WARNING" value="WARNING" />
                <el-option label="ERROR" value="ERROR" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveSystemConfig" :loading="saving">
                💾 保存系统配置
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import axios from '../utils/axios'

// ============ 常量 ============
const STORAGE_KEY_LIST = 'pentest_llm_custom_list'
const STORAGE_KEY_ACTIVE = 'pentest_llm_active_id'

// ============ 状态 ============
const activeTab = ref('llm')
const saving = ref(false)
const testing = ref(false)
const mcpLoading = ref(false)
const adding = ref(false)
const formRef = ref(null)

// ============ 自定义LLM - 本地存储 ============
const customModels = ref([])
const activeModelId = ref('')

// ★ 备用模型配置
const fallbackForm = reactive({
  api_base: '',
  api_key: '',
  model: '',
})
const savingFallback = ref(false)
const fallbackConfigured = ref(false)

// ★ 从已添加模型导入备用模型
const importFallbackModelId = ref('')
const importedModelInfo = ref(null)

// 排除当前正在使用的主模型，避免一机两用
const importableModels = computed(() => {
  return customModels.value.filter(m => m.id !== activeModelId.value)
})

function onImportFallbackModel(id) {
  // 选择后自动填充
  if (id) fillFallbackFromSelected()
}

function fillFallbackFromSelected() {
  const model = customModels.value.find(m => m.id === importFallbackModelId.value)
  if (!model) return
  fallbackForm.api_base = model.api_base
  fallbackForm.api_key = model.api_key
  fallbackForm.model = model.model
  importedModelInfo.value = { name: model.name, model: model.model }
  ElMessage.success(`✅ 已导入「${model.name}」的配置，点击"保存备用模型"即可生效`)
}

function clearImportedFallback() {
  importFallbackModelId.value = ''
  importedModelInfo.value = null
  fallbackForm.api_base = ''
  fallbackForm.api_key = ''
  fallbackForm.model = ''
}

// ★ 从后端加载备用模型配置
async function loadFallbackConfig() {
  try {
    const res = await axios.get('/api/config')
    const llm = res?.llm || {}
    if (llm.fallback_base_url) {
      fallbackForm.api_base = llm.fallback_base_url
      fallbackForm.api_key = llm.fallback_api_key || ''
      fallbackForm.model = llm.fallback_model || ''
      fallbackConfigured.value = true
    }
  } catch { /* ignore */ }
}

// 辅助函数：确保 URL 有 http:// 或 https:// 前缀
function normalizeUrl(url) {
  if (!url) return url
  url = url.trim()
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url
  }
  return url
}

async function saveFallbackConfig() {
  if (!fallbackForm.api_base) {
    ElMessage.warning('请填写备用 API 地址')
    return
  }
  // 规范化 URL
  fallbackForm.api_base = normalizeUrl(fallbackForm.api_base)
  savingFallback.value = true
  try {
    await axios.post('/api/config/llm', {
      base_url: activeModel?.api_base || '',
      api_key: activeModel?.api_key || '',
      model: activeModel?.model || 'gpt-3.5-turbo',
      temperature: 0.1,
      max_tokens: 2048,
      timeout: 60,
      max_retries: 3,
      fallback_base_url: fallbackForm.api_base,
      fallback_api_key: fallbackForm.api_key,
      fallback_model: fallbackForm.model,
      fallback_provider: 'openai',
    })
    fallbackConfigured.value = true
    ElMessage.success('备用模型已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e.message))
  } finally {
    savingFallback.value = false
  }
}

async function clearFallbackConfig() {
  try {
    await axios.post('/api/config/llm', {
      base_url: activeModel?.api_base || '',
      api_key: activeModel?.api_key || '',
      model: activeModel?.model || 'gpt-3.5-turbo',
      temperature: 0.1,
      max_tokens: 2048,
      timeout: 60,
      max_retries: 3,
      fallback_base_url: '',
      fallback_api_key: '',
      fallback_model: '',
      fallback_provider: '',
    })
    fallbackForm.api_base = ''
    fallbackForm.api_key = ''
    fallbackForm.model = ''
    fallbackConfigured.value = false
    clearImportedFallback()
    ElMessage.success('备用模型已清除')
  } catch { /* ignore */ }
}

// 计算当前选中的模型对象
const activeModel = computed(() => {
  if (!activeModelId.value) return null
  return customModels.value.find(m => m.id === activeModelId.value) || null
})

// ============ 表单 ============
const formData = reactive({
  name: '',
  api_base: '',
  api_key: '',
  model: ''
})

const formRules = {
  name: [{ required: true, message: '请输入模型名称', trigger: 'blur' }],
  api_base: [{ required: true, message: '请输入 API 地址', trigger: 'blur' }],
  api_key: [{ required: true, message: '请输入 API Key', trigger: 'blur' }],
  model: [{ required: true, message: '请输入模型标识', trigger: 'blur' }]
}

// ============ LocalStorage 读写 ============
function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_LIST)
    customModels.value = raw ? JSON.parse(raw) : []
    activeModelId.value = localStorage.getItem(STORAGE_KEY_ACTIVE) || ''
    // 如果保存的 activeId 对应的模型已被删除，清空选中
    if (activeModelId.value && !customModels.value.find(m => m.id === activeModelId.value)) {
      activeModelId.value = ''
      localStorage.removeItem(STORAGE_KEY_ACTIVE)
    }
  } catch (e) {
    customModels.value = []
    activeModelId.value = ''
  }
}

function saveToStorage() {
  localStorage.setItem(STORAGE_KEY_LIST, JSON.stringify(customModels.value))
  if (activeModelId.value) {
    localStorage.setItem(STORAGE_KEY_ACTIVE, activeModelId.value)
  } else {
    localStorage.removeItem(STORAGE_KEY_ACTIVE)
  }
}

// ============ 操作 ============
function generateId() {
  return Date.now().toString(36) + '-' + Math.random().toString(36).substring(2, 8)
}

function resetForm() {
  formData.name = ''
  formData.api_base = ''
  formData.api_key = ''
  formData.model = ''
  formRef.value?.clearValidate()
}

async function handleAdd() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请完整填写所有字段')
    return
  }

  adding.value = true

  // 规范化 API 地址
  formData.api_base = normalizeUrl(formData.api_base)

  const newModel = {
    id: generateId(),
    name: formData.name.trim(),
    api_base: formData.api_base.trim().replace(/\/+$/, ''),
    api_key: formData.api_key.trim(),
    model: formData.model.trim()
  }

  customModels.value.push(newModel)
  saveToStorage()
  resetForm()
  adding.value = false

  ElMessage.success(`✅ 模型「${newModel.name}」已添加`)
}

function setActive(id) {
  activeModelId.value = id
  saveToStorage()
  const model = customModels.value.find(m => m.id === id)
  // 同步到后端，让聊天接口使用正确的配置
  syncToBackend(model)
  ElMessage.success(`✅ 已切换至「${model?.name || ''}」`)
}

function onActiveModelChange(id) {
  if (id) {
    const model = customModels.value.find(m => m.id === id)
    // 下拉框切换时也要同步
    syncToBackend(model)
    ElMessage.success(`✅ 已切换至「${model?.name || ''}」`)
  }
}

// ============ 同步到后端 ============
async function syncToBackend(model) {
  if (!model) return
  try {
    // 确保 URL 有协议前缀
    const baseUrl = normalizeUrl(model.api_base)
    await axios.post('/api/config/llm', {
      base_url: baseUrl,
      api_key: model.api_key,
      model: model.model,
      temperature: 0.1,
      max_tokens: 2048,
      timeout: 60,
      max_retries: 3,
      // ★ 保留已配置的备用模型
      fallback_base_url: fallbackForm.api_base || '',
      fallback_api_key: fallbackForm.api_key || '',
      fallback_model: fallbackForm.model || '',
      fallback_provider: 'openai',
    })
    console.log('[LLM Sync] 配置已同步到后端:', model.name)
  } catch (e) {
    console.warn('[LLM Sync] 后端同步失败，仅本地生效:', e.message)
  }
}

async function handleDelete(id) {
  const model = customModels.value.find(m => m.id === id)
  if (!model) return

  try {
    await ElMessageBox.confirm(
      `确定要删除模型「${model.name}」吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }

  customModels.value = customModels.value.filter(m => m.id !== id)

  // 若删除的是当前使用的模型，清空选中
  if (activeModelId.value === id) {
    activeModelId.value = ''
  }

  saveToStorage()
  ElMessage.success(`✅ 模型「${model.name}」已删除`)
}

// ============ 安全配置 ============
const securityConfig = ref({
  ast_enabled: true,
  anomaly_detection_enabled: true
})

// ============ 系统配置 ============
const systemConfig = ref({
  auto_pilot_max_steps: 20,
  command_timeout: 300,
  log_level: 'INFO',
  llm_max_tokens: 1024,
  context_max_tokens: 4096,
  context_reserve_recent: 4,
})

// ============ 加载系统配置 ============
const loadSystemConfig = async () => {
  try {
    const res = await axios.get('/api/config')
    const data = (res && res.data !== undefined) ? res.data : res
    if (data && data.system) {
      // 合并后端返回的配置，保留默认值如果有字段缺失
      systemConfig.value = { ...systemConfig.value, ...data.system }
    }
    if (data && data.security) {
      securityConfig.value = { ...securityConfig.value, ...data.security }
    }
    console.log('[Settings] 系统配置已加载:', systemConfig.value)
  } catch (e) {
    console.error('加载系统配置失败:', e)
  }
}

// ============ AutoPilot ============
const autopilotStatus = ref({
  enabled: false,
  max_steps: 20,
  current_step: 0,
  status: 'idle'
})

const loadAutopilotStatus = async () => {
  try {
    const res = await axios.get('/api/system/autopilot')
    const data = (res && res.data !== undefined) ? res.data : res
    if (data && typeof data === 'object') {
      autopilotStatus.value = { ...autopilotStatus.value, ...data }
    }
  } catch (e) {
    console.error('加载AutoPilot状态失败:', e)
  }
}

const toggleAutopilot = async (enabled) => {
  try {
    await axios.post(`/api/system/autopilot/toggle?enabled=${enabled}`)
    ElMessage.success(`✅ AutoPilot已${enabled ? '开启' : '关闭'}`)
  } catch (e) {
    ElMessage.error('操作失败')
    autopilotStatus.value.enabled = !enabled
  }
}

// ============ 日志 ============
const logs = ref([])
const logFilter = ref('all')
const logLines = ref(100)

const refreshLogs = async () => {
  try {
    const res = await axios.get(`/api/system/logs?lines=${logLines.value}&log_type=${logFilter.value}`)
    const data = (res && res.data !== undefined) ? res.data : res
    logs.value = (data && data.logs) || []
  } catch (e) {
    console.error('加载日志失败:', e)
    logs.value = []
  }
}

// ============ MCP ============
const mcpStatus = ref({
  running: false,
  host: '127.0.0.1',
  port: 8911,
  endpoint: ''
})

const loadMCPStatus = async () => {
  try {
    const res = await axios.get('/api/system/mcp')
    const data = (res && res.data !== undefined) ? res.data : res
    if (data && typeof data === 'object') {
      mcpStatus.value = { ...mcpStatus.value, ...data }
    }
  } catch (e) {
    console.error('加载MCP状态失败:', e)
  }
}

const startMCP = async () => {
  mcpLoading.value = true
  try {
    const res = await axios.post('/api/system/mcp/start')
    mcpStatus.value.running = true
    const data = (res && res.data !== undefined) ? res.data : res
    ElMessage.success('✅ ' + (data?.message || '启动成功'))
  } catch (e) {
    ElMessage.error('启动失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    mcpLoading.value = false
  }
}

const stopMCP = async () => {
  mcpLoading.value = true
  try {
    const res = await axios.post('/api/system/mcp/stop')
    mcpStatus.value.running = false
    const data = (res && res.data !== undefined) ? res.data : res
    ElMessage.success('✅ ' + (data?.message || '停止成功'))
  } catch (e) {
    ElMessage.error('停止失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    mcpLoading.value = false
  }
}

// ============ 单个配置项独立保存 ============
import { debounce } from 'lodash-es'

const savingFields = ref({})  // 记录每个字段的保存状态

const saveField = async (section, key, value) => {
  const fieldKey = `${section}.${key}`
  savingFields.value[fieldKey] = true
  try {
    await axios.post('/api/config/set', {
      key_path: fieldKey,
      value: value
    })
    console.log(`[Settings] ${fieldKey} 已保存:`, value)
  } catch (e) {
    ElMessage.error(`保存 ${key} 失败`)
  } finally {
    savingFields.value[fieldKey] = false
  }
}

// 带防抖的保存（用于输入框/数字框 change 时）
const debouncedSave = debounce((section, key, value) => {
  saveField(section, key, value)
}, 800)

function onFieldChange(section, key, value) {
  debouncedSave(section, key, value)
}

// ============ 安全配置保存（逐个字段保存）============
const saveSecurityConfig = async () => {
  saving.value = true
  try {
    const promises = Object.entries(securityConfig.value).map(([key, value]) => {
      return axios.post('/api/config/set', {
        key_path: `security.${key}`,
        value: value
      })
    })
    await Promise.all(promises)
    ElMessage.success('✅ 安全配置已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

// ============ 系统配置保存（逐个字段保存）============
const saveSystemConfig = async () => {
  saving.value = true
  try {
    const promises = Object.entries(systemConfig.value).map(([key, value]) => {
      return axios.post('/api/config/set', {
        key_path: `system.${key}`,
        value: value
      })
    })
    await Promise.all(promises)
    ElMessage.success('✅ 系统配置已保存')
    await loadSystemConfig()
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

// ============ 初始化 ============
onMounted(() => {
  loadFromStorage()
  // 如果有当前选中模型，启动时同步到后端
  if (activeModel.value) {
    syncToBackend(activeModel.value)
  }
  loadSystemConfig()  // 加载系统配置
  loadAutopilotStatus()
  refreshLogs()
  loadMCPStatus()
  loadFallbackConfig()  // 加载备用模型配置
})
</script>

<style scoped>
.settings-container {
  padding: 20px;
}

.settings-card {
  max-width: 900px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: bold;
}

.config-form {
  padding: 10px 0;
}

.form-tip {
  margin-left: 8px;
  font-size: 12px;
  color: #909399;
}

.model-option-sub {
  float: right;
  color: #909399;
  font-size: 12px;
  margin-left: 20px;
}

.model-name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.active-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.desc-code {
  font-size: 12px;
  word-break: break-all;
  color: #409eff;
}

.log-controls {
  display: flex;
  gap: 10px;
  margin-bottom: 15px;
  align-items: center;
}

.log-container {
  max-height: 500px;
  overflow-y: auto;
  background: #1e1e1e;
  border-radius: 4px;
  padding: 10px;
  font-family: monospace;
  font-size: 12px;
}

.log-item {
  padding: 4px 0;
  border-bottom: 1px solid #333;
  color: #d4d4d4;
}

.log-item.INFO {
  color: #4ec9b0;
}

.log-item.ERROR {
  color: #f44747;
}

.log-item.WARNING {
  color: #ce9178;
}

.log-time {
  color: #808080;
  margin-right: 10px;
}

.log-source {
  color: #569cd6;
  margin-right: 10px;
}

.mcp-actions {
  margin-top: 20px;
  display: flex;
  gap: 10px;
  justify-content: center;
}

/* ★ 备用模型导入区域 */
.fallback-import {
  margin-top: 0;
  padding: 0 20px 20px;
  max-width: 900px;
  margin-left: auto;
  margin-right: auto;
}
.imported-info {
  margin-top: 8px;
}
</style>
