<template>
  <div class="session-detail" ref="sessionDetailRef">
    <!-- 顶部操作栏 -->
    <div class="page-header">
      <el-button text @click="goBack">
        <el-icon><ArrowLeft /></el-icon>返回
      </el-button>
      <h2 class="page-title">{{ session.name || '会话详情' }}</h2>
      <!-- WebSocket 连接状态提示 -->
      <div v-if="!wsConnected" class="ws-status">
        <el-tag type="danger" size="small" effect="dark">
          <el-icon class="is-loading"><Loading /></el-icon>
          {{ wsReconnecting ? 'WebSocket 重连中...' : 'WebSocket 已断开' }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-tag :type="statusType(session.status)" size="default" effect="dark">
          {{ statusLabel(session.status) }}
        </el-tag>
        <el-button v-if="session.status === 'completed' || session.status === 'failed'"
          type="primary" size="small" @click="downloadReport">
          <el-icon><DataAnalysis /></el-icon>下载报告
        </el-button>
        <el-button v-if="session.status === 'paused'" type="success" size="small" @click="confirmStartAutoPilot">
          <el-icon><VideoPlay /></el-icon>恢复 AutoPilot
        </el-button>
        <el-button v-else type="success" size="small" :disabled="session.status === 'active'" @click="confirmStartAutoPilot">
          <el-icon><VideoPlay /></el-icon>启动 AutoPilot
        </el-button>
        <el-button type="warning" size="small" :disabled="session.status !== 'active'" @click="pauseSession">
          <el-icon><VideoPause /></el-icon>暂停
        </el-button>
        <el-button v-if="session.status === 'active'" type="danger" size="small" :loading="aborting" @click="abortSession">
          <el-icon><CircleClose /></el-icon>{{ aborting ? '正在终止...' : '终止' }}
        </el-button>
        <!-- ★ 新增：策略设置和状态查询 -->
        <el-button v-if="session.status === 'active'" type="info" size="small" @click="showStrategyDialog = true">
          <el-icon><Setting /></el-icon>策略设置
        </el-button>
        <el-button v-if="session.status === 'active'" type="info" size="small" @click="queryStatus">
          <el-icon><InfoFilled /></el-icon>状态查询
        </el-button>
      </div>
    </div>

    <!-- 双栏主体：通过拖拽调整宽度 -->
    <div class="dual-panel" ref="dualPanelRef">
      <!-- ======== 左栏：实时日志（含 AutoPilot 进展） ======== -->
      <div class="left-panel" :style="{ width: leftWidth + 'px' }">
        <div class="left-inner" style="display:flex;flex-direction:column;height:100%">
          <!-- 实时日志（默认 100%） -->
          <div class="log-area" style="flex:1;display:flex;flex-direction:column;min-height:150px">
            <div class="panel-header">
              <span><el-icon><Notebook /></el-icon> 实时日志</span>
              <div style="display:flex;gap:4px;align-items:center">
                <el-button text size="small" @click="clearLogs">清空</el-button>
              </div>
            </div>
            <!-- ★ 阶段进度条 -->
            <div v-if="autoPilotPhase || autoPilotPhaseName" class="phase-progress-bar"
              :class="{
                active: autoPilotState === 'active' || autoPilotState === 'running',
                paused: autoPilotState === 'paused' || autoPilotState === 'PAUSED',
                completed: autoPilotState === 'completed'
              }">
              <div class="phase-info">
                <el-tag size="small" type="warning" effect="dark" style="font-size:11px">阶段</el-tag>
                <span class="phase-name">{{ autoPilotPhaseName || getPhaseLabel(autoPilotPhase) }}</span>
                <span class="phase-state" v-if="autoPilotState">
                  <el-icon size="12">
                    <VideoPause v-if="autoPilotState === 'paused' || autoPilotState === 'PAUSED'" />
                    <VideoPlay v-else-if="autoPilotState === 'active' || autoPilotState === 'RUNNING'" />
                    <CircleClose v-else-if="autoPilotState === 'aborted'" />
                    <SuccessFilled v-else-if="autoPilotState === 'completed'" />
                    <InfoFilled v-else />
                  </el-icon>
                  {{ autoPilotState === 'RUNNING' || autoPilotState === 'active' ? '运行中' :
                     autoPilotState === 'PAUSED' || autoPilotState === 'paused' ? '已暂停' :
                     autoPilotState === 'aborted' ? '已中止' :
                     autoPilotState === 'completed' ? '已完成' : autoPilotState }}
                </span>
                <span class="phase-tool-count" :class="{ 'tool-count-flash': toolCount > 0 }">
                  <el-icon size="12"><Tools /></el-icon> {{ toolCount }}
                </span>
                <span v-if="autoPilotDescription" class="phase-desc">{{ autoPilotDescription }}</span>
              </div>
              <!-- ★ 进度条（百分比） -->
              <div class="phase-progress-track" style="width:100%">
                <div class="phase-progress-fill" :style="{ width: autoPilotProgress + '%' }">
                  <span class="progress-label" v-if="autoPilotProgress > 8">{{ autoPilotProgress }}%</span>
                </div>
              </div>
              <div v-if="pendingCommand" class="pending-command">
                <span class="pending-label">当前命令:</span>
                <code class="pending-cmd">{{ pendingCommand }}</code>
              </div>
            </div>
            <div class="log-container" ref="logContainerRef">
              <!-- ★ 统一时间线：每条日志一行，带类型标签 -->
              <div v-for="(log, i) in logTimeline" :key="'log-'+i"
                class="tl-item" :class="['type-'+log.category, log.cls]">
                <span class="tl-time">{{ formatLogTime(log.time) }}</span>
                <el-tag :type="log.type" size="small" effect="dark" class="tl-tag" disable-transitions>
                  {{ log.label }}
                </el-tag>
                <div class="tl-message">
                  {{ log.message }}
                </div>
                <div v-if="log.detail" class="tl-detail">
                  <pre>{{ log.detail }}</pre>
                </div>
              </div>
              <div v-if="logTimeline.length === 0" class="log-empty">暂无日志</div>
            </div>
          </div>
          <!-- 拖拽手柄（垂直） -->
        </div>
      </div>

      <!-- 拖拽手柄 -->
      <div class="resize-handle" @mousedown="startResize"></div>

      <!-- ======== 右栏：AI 对话 ======== -->
      <div class="right-panel" :style="{ width: 'calc(100% - ' + leftWidth + 'px)' }">
        <div class="panel-header">
          <span><el-icon><ChatDotSquare /></el-icon> AI 对话</span>
          <div class="chat-status">
            <el-tag v-if="chatLoading" type="warning" size="small" effect="dark" class="loading-tag">
              <el-icon class="is-loading"><Loading /></el-icon> AI 思考中...
            </el-tag>
            <el-tag v-if="executingTool" type="info" size="small" effect="dark" class="loading-tag">
              <el-icon class="is-loading"><Loading /></el-icon> 执行工具中...
            </el-tag>
            <el-button v-if="chatLoading || executingTool" type="danger" size="small" @click="stopExecution">
              <el-icon><CircleClose /></el-icon> 停止
            </el-button>
            <el-button v-if="session.status === 'paused'" type="success" size="small" @click="confirmStartAutoPilot">
              <el-icon><VideoPlay /></el-icon> 恢复 AutoPilot
            </el-button>
            <el-button text size="small" @click="clearChat">清空对话</el-button>
          </div>
        </div>

        <!-- 聊天消息列表 -->
        <div class="chat-messages" ref="chatMessagesRef">
          <div v-for="(msg, i) in chatMessages" :key="i" class="chat-msg" :class="msg.role">
            <div class="msg-avatar">
              <el-avatar :icon="msg.role === 'user' ? UserFilled : ChatDotSquare" :size="32"
                :style="{ background: msg.role === 'user' ? '#409eff' : '#67C23A' }" />
            </div>
            <div class="msg-content">
              <div class="msg-name">{{ msg.role === 'user' ? '你' : 'AI' }}</div>
              <div class="msg-bubble">
                <!-- AI 消息：优先显示伪流式文本 + 结构化渲染 -->
                <template v-if="msg.role === 'assistant'">
                  <!-- 流式显示中：显示累积文本 + 闪烁光标 -->
                  <template v-if="isStreaming(i)">
                    <div class="msg-text">
                      {{ getDisplayText(i, msg) }}
                      <span class="msg-streaming-cursor"></span>
                    </div>
                  </template>
                  <!-- 内容为空但还在加载 -->
                  <template v-else-if="!getDisplayText(i, msg) && !msg.tool_calls?.length">
                    <div class="msg-text" style="color:#8b949e">{{ chatLoading ? '正在思考...' : '' }}</div>
                  </template>
                  <!-- 完整内容：尝试结构化渲染 -->
                  <template v-else>
                    <template v-if="parseAiSections(getDisplayText(i, msg))">
                      <div class="ai-structured">
                        <div v-for="(section, si) in parseAiSections(getDisplayText(i, msg))" :key="si" class="ai-section-card">
                          <div class="ai-section-header" :class="section.key">
                            {{ section.label }}
                          </div>
                          <div class="ai-section-body">
                            <template v-for="(sline, sli) in section.lines" :key="sli">
                              {{ sline }}<br v-if="sline === '' || sli < section.lines.length - 1" />
                            </template>
                          </div>
                        </div>
                      </div>
                    </template>
                    <!-- 非结构化内容原样显示 -->
                    <div v-else class="msg-text" v-html="renderMarkdown(getDisplayText(i, msg))"></div>
                  </template>
                </template>
                <!-- 用户消息：完整显示 -->
                <template v-else>
                  <div v-if="msg.content" class="msg-text">{{ msg.content }}</div>
                </template>
                <!-- 显示工具调用信息 -->
                <div v-if="msg.tool_calls && msg.tool_calls.length" class="msg-tool-calls">
                  <div v-for="(tc, ti) in msg.tool_calls" :key="ti" class="tool-call-badge">
                    <el-tag type="warning" size="small">
                      <el-icon><Tools /></el-icon> {{ tc.function && tc.function.name ? tc.function.name : '工具调用' }}
                    </el-tag>
                    <code class="tool-call-args">{{ extractCommandArg(tc) }}</code>
                  </div>
                </div>
                <!-- 显示工具执行结果 -->
                <div v-if="msg.tool_result" class="msg-tool-result">
                  <div class="tool-result-header">📋 执行结果</div>
                  <pre class="tool-result-output">{{ msg.tool_result }}</pre>
                </div>
              </div>
            </div>
          </div>

          <!-- 工具失败/手动执行决策弹窗 -->
          <div v-if="pendingDecision" class="decision-overlay">
            <div class="decision-panel" :class="{ 'decision-manual': pendingDecision.is_manual }">
              <div class="decision-header">
                <el-icon :color="pendingDecision.is_manual ? '#409EFF' : '#E6A23C'">
                  <component :is="pendingDecision.is_manual ? InfoFilled : WarningFilled" />
                </el-icon>
                <span>{{ pendingDecision.is_manual ? '💡 此命令建议在终端手动执行' : '⚠️ 命令执行失败，需要你的决定' }}</span>
              </div>
              <div class="decision-reason">{{ pendingDecision.reason }}</div>
              <div v-if="pendingDecision.is_manual" class="decision-manual-hint">
                <p>操作步骤：</p>
                <ol>
                  <li>复制下方命令</li>
                  <li>在 Kali 终端中执行</li>
                  <li>将执行结果粘贴回聊天框发送</li>
                  <li>AI 将继续分析结果</li>
                </ol>
              </div>
              <div class="decision-steps">
                <div
                  v-for="(step, si) in pendingDecision.next_steps"
                  :key="si"
                  class="decision-step-btn"
                  :class="{
                    'step-primary': step.action === 'manual_execution',
                    'step-skip': step.action === 'skip'
                  }"
                  @click="executeUserDecision(step)"
                >
                  <span class="step-label">{{ step.label }}</span>
                  <code v-if="step.command" class="step-command">{{ step.command }}</code>
                </div>
              </div>
            </div>
          </div>

          <div v-if="chatMessages.length === 0" class="chat-empty">
            <el-empty description="开始对话，AI 将协助完成渗透测试" :image-size="80" />
          </div>
        </div>

        <!-- 输入框 -->
        <div class="chat-input-area">
          <el-input
            v-model="chatInput"
            type="textarea"
            :rows="3"
            placeholder="输入消息，按 Enter 发送..."
            :disabled="chatLoading || executingTool"
            @keydown.enter.prevent="sendChatMessage"
          />
          <div class="chat-input-actions">
            <span class="char-count">{{ chatInput.length }} / 5000</span>
            <el-button type="primary" :loading="chatLoading || executingTool" @click="sendChatMessage">
              发送
            </el-button>
          </div>
        </div>
      </div>
    </div>
    <!-- ★ 新增：自定义垂直拖拽条，替代原生 CSS resize --> 
    <div class="box-resize-bar" @mousedown="startBoxResize"></div>
  </div>

  <!-- ★ 旧：阶段级确认对话框（保留，用于 AutoPilot 阶段切换确认） -->
  <el-dialog v-model="confirmDialogVisible" title="AutoPilot 确认" width="500px"
    :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false" :lock-scroll="false">
    <div style="font-size:14px; line-height:1.8;">{{ confirmDialogMessage }}</div>
    <template #footer>
      <el-button @click="onConfirmDialogAbort" type="danger">中止</el-button>
      <el-button @click="onConfirmDialogConfirm" type="primary">继续执行</el-button>
    </template>
  </el-dialog>

  <!-- ★ 新：命令确认对话框：每条命令三按钮 + 修改输入 + 跳过输入 -->
  <el-dialog v-model="commandDialogVisible" title="AutoPilot 命令确认" width="600px"
    :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false" :lock-scroll="false"
    destroy-on-close>
    <div v-if="commandDialogData" style="font-size:14px;line-height:1.8;">
      <div style="margin-bottom:12px">
        <el-tag type="warning" size="small" style="margin-bottom:8px">⚠️ AI 准备执行以下命令</el-tag>
      </div>
      <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:12px;font-family:monospace;">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">原因</div>
        <div style="color:#58a6ff">{{ commandDialogData.reason || 'AutoPilot 自动执行' }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">命令</div>
        <div style="color:#c9d1d9;white-space:pre-wrap;word-break:break-all;">{{ commandDialogData.command }}</div>
      </div>

      <!-- 修改命令输入框 -->
      <div v-if="showModifyInput" style="margin-bottom:12px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">修改命令</div>
        <el-input v-model="commandModifyInput" type="textarea" :rows="3"
          placeholder="修改命令后点击「执行修改后的命令」" style="font-family:monospace" />
      </div>

      <!-- 指定下一步输入框（跳过时） -->
      <div v-if="skipInputVisible" style="margin-bottom:12px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">你想让 AI 下一步做什么？</div>
        <el-input v-model="skipUserInput" type="textarea" :rows="2"
          placeholder="例如：跳过这个，直接扫描 Web 服务" />
      </div>
    </div>
    <template #footer>
      <div style="display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
        <el-button type="danger" @click="onCommandAbort">终止 AutoPilot</el-button>
        <el-button v-if="!showModifyInput" @click="onCommandSkip">跳过</el-button>
        <el-button v-if="!showModifyInput" type="primary" @click="onCommandModifyBtn">修改命令</el-button>
        <el-button v-if="showModifyInput" type="warning" @click="onCommandModifyConfirm">执行修改后的命令</el-button>
        <el-button v-if="!showModifyInput && !skipInputVisible" type="success" @click="onCommandExecute">允许执行</el-button>
        <el-button v-if="skipInputVisible && skipUserInput" type="primary" @click="onCommandSkipWithInput">指定下一步并跳过</el-button>
        <el-button v-if="skipInputVisible && !skipUserInput" @click="onCommandSkip">直接跳过</el-button>
      </div>
    </template>
  </el-dialog>

  <!-- ★ 恢复 AutoPilot 确认对话框 -->
  <el-dialog v-model="resumeDialogVisible" title="恢复 AutoPilot" width="500px"
    :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false" :lock-scroll="false">
    <div style="font-size:14px;line-height:1.8;">
      <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:12px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">当前阶段</div>
        <div style="color:#c9d1d9">{{ resumeSessionData.phase || '未知' }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">上次状态</div>
        <div style="color:#c9d1d9">{{ resumeSessionData.lastStatus || '等待执行' }}</div>
      </div>
      <p>确认恢复 AutoPilot 自动渗透？</p>
      <p style="color:#8b949e;font-size:12px">恢复后不会自动继续执行，需要您确认下一步操作。</p>
    </div>
    <template #footer>
      <el-button @click="resumeDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="onResumeConfirm">确认恢复</el-button>
    </template>
  </el-dialog>

  <!-- ★ 新增：策略设置对话框 -->
  <el-dialog v-model="showStrategyDialog" title="设置渗透策略" width="600px"
    :close-on-click-modal="false" :close-on-press-escape="true">
    <div style="font-size:14px;line-height:1.8;">
      <p style="color:#8b949e;font-size:12px;margin-bottom:8px">
        设置策略后，AI 将在后续决策中参考您的策略（例如：优先检测 SQL 注入、跳过暴力破解等）
      </p>
      <el-input v-model="strategyText" type="textarea" :rows="6"
        placeholder="请输入渗透策略，例如：&#10;- 优先检测 SQL 注入漏洞&#10;- 跳过暴力破解&#10;- 重点关注 Web 应用防火墙绕过" />
    </div>
    <template #footer>
      <el-button @click="showStrategyDialog = false">取消</el-button>
      <el-button type="primary" @click="sendStrategy">确认发送</el-button>
    </template>
  </el-dialog>

  <!-- ★ 新增：状态查询结果显示 -->
  <el-dialog v-model="showStatusDialog" title="AutoPilot 当前状态" width="500px">
    <div v-if="autoPilotStatus" style="font-size:14px;line-height:1.8;">
      <div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;margin-bottom:12px">
        <div style="color:#8b949e;font-size:12px;margin-bottom:4px">会话 ID</div>
        <div style="color:#c9d1d9">{{ autoPilotStatus.session_id }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">目标</div>
        <div style="color:#c9d1d9">{{ autoPilotStatus.target }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">当前阶段</div>
        <div style="color:#c9d1d9">{{ autoPilotStatus.current_stage }} - {{ autoPilotStatus.stage_name }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">漏洞数 / 利用尝试</div>
        <div style="color:#c9d1d9">{{ autoPilotStatus.vuln_count }} / {{ autoPilotStatus.exploit_count }}</div>
        <div style="color:#8b949e;font-size:12px;margin:8px 0 4px">暂停 / 停止</div>
        <div style="color:#c9d1d9">{{ autoPilotStatus.is_paused ? '是' : '否' }} / {{ autoPilotStatus.is_stopped ? '是' : '否' }}</div>
      </div>
    </div>
    <div v-else style="text-align:center;color:#8b949e;padding:20px">
      暂无状态数据，请先启动 AutoPilot
    </div>
  </el-dialog>

  <!-- ★ 凭证展示卡片 -->
  <div v-if="credentials.length > 0" class="credential-panel">
    <div class="credential-header">
      <el-icon style="margin-right:6px;color:#e6a23c"><WarningFilled /></el-icon>
      <span style="font-weight:600">AutoPilot 发现的凭证 ({{ credentials.length }})</span>
    </div>
    <div class="credential-list">
      <div v-for="(cred, i) in credentials" :key="i" class="credential-item">
        <el-tag :type="cred.type === 'SSH Private Key' ? 'danger' : 'warning'" size="small" effect="dark">
          {{ cred.type }}
        </el-tag>
        <code class="credential-value">{{ cred.value }}</code>
        <span class="credential-source" v-if="cred.source">来源: {{ cred.source }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, ArrowRight, ArrowDown, VideoPlay, VideoPause, Notebook,
  Monitor, ChatDotSquare, UserFilled,
  Tools, Loading, CircleClose, WarningFilled, InfoFilled, DataAnalysis, Setting, SuccessFilled
} from '@element-plus/icons-vue'
import request from '../utils/axios'
import { getGlobalWebSocket, registerMessageHandler } from '../utils/websocket'

// ★ 提取工具调用的命令参数摘要
function extractCommandArg(tc) {
  try {
    const fn = tc.function || {}
    const raw = fn.arguments || '{}'
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    const cmd = parsed.command || ''
    if (cmd.length > 80) return cmd.substring(0, 80) + '...'
    return cmd || Object.values(parsed).join(', ').substring(0, 80)
  } catch {
    return (typeof tc?.function?.arguments === 'string' ? tc.function.arguments : '').substring(0, 80)
  }
}

// ============ 路由 ============
const route = useRoute()
const router = useRouter()
const sessionId = route.params.id || 'default'

// ============ 会话状态 ============
const session = ref({})
const logs = ref([])

// ★ 新增：策略设置和状态查询
const showStrategyDialog = ref(false)
const strategyText = ref('')
const showStatusDialog = ref(false)
const autoPilotStatus = ref(null)

function statusType(status) {
  const map = { active: 'success', paused: 'warning', completed: 'info', failed: 'danger' }
  return map[status] || 'info'
}
function statusLabel(status) {
  const map = { active: '运行中', paused: '已暂停', completed: '已完成', failed: '失败' }
  return map[status] || status
}

async function fetchSession() {
  try {
    const res = await request.get(`/api/sessions/${sessionId}`)
    session.value = res
    // ★ 恢复阶段/状态（刷新后重新显示阶段进度条）
    if (res?.phase) autoPilotPhase.value = res.phase
    if (res?.toolCount !== undefined) toolCount.value = res.toolCount
    if (res?.status) autoPilotState.value = res.status
    // ★ 如果会话已暂停，重置运行时变量，进度条按阶段显示
    if (res?.status === 'paused') {
      autoPilotStep.value = 0
      autoPilotProgress.value = 0
      autoPilotDescription.value = '已暂停'
    } else if (res?.phase) {
      // 恢复阶段对应的进度估算
      const phaseMap = {'初始化': 1, '信息收集': 2, '漏洞排序': 3, '漏洞利用': 4, '报告生成': 5}
      const stage = phaseMap[res.phase] || 0
      if (stage > 0) {
        autoPilotProgress.value = (stage - 1) * 20
        autoPilotStep.value = stage
      }
    }
  } catch { /* ignore */ }
  // ★ 获取凭证 + 检查进度（独立调用，不阻塞 session 加载）
  fetchCredentials()
  checkProgress()
}

async function fetchCredentials() {
  try {
    const res = await request.get(`/api/sessions/${sessionId}/credentials`)
    const data = res.data || res
    if (data?.credentials?.length > 0) {
      credentials.value = data.credentials
    }
  } catch { /* ignore */ }
}

async function checkProgress() {
  try {
    const res = await request.get(`/api/sessions/${sessionId}/progress`)
    const data = res.data || res
    if (data?.has_progress && session.value?.status !== 'active' && session.value?.status !== 'completed') {
      savedProgress.value = data.current_stage
      showResumeConfirm()
    }
  } catch { /* ignore */ }
}

function goBack() { router.push({ name: 'SessionList' }) }

async function confirmStartAutoPilot() {
  if (isAutoRunning.value) {
    ElMessage.warning('AI 正在执行中，请等待完成或点击停止')
    return
  }
  if (session.value.status === 'active') {
    ElMessage.warning('会话已在运行中')
    return
  }
  // 如果是已暂停的会话，显示恢复确认对话框
  if (session.value.status === 'paused') {
    showResumeDialog()
    return
  }
  // 新会话，启动确认
  try {
    await ElMessageBox.confirm(
      `确认启动 AutoPilot 自动渗透测试？\n\n` +
      `目标: ${session.value.target || '未设置'}\n` +
      `模式: 全自动（AI 将自动执行命令并推进渗透流程）\n\n` +
      `⚠️ 注意: 启动后 AI 将自动在 Kali 上执行渗透测试命令，请确保目标已授权。`,
      '启动 AutoPilot',
      {
        confirmButtonText: '确认启动',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      }
    )
    await startSession()
  } catch {
    // 用户取消
  }
}

async function startSession() {
  try {
    await request.post(`/api/sessions/${sessionId}/start`)
    ElMessage.success('AutoPilot 已启动')
    fetchSession()
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || '启动失败'
    ElMessage.error(`启动失败: ${msg}`)
  }
}

async function pauseSession() {
  try {
    // 立即中断：先中止前端请求
    stopExecution()
    // 再通知后端暂停
    await request.post(`/api/sessions/${sessionId}/pause`)
    isAutoRunning.value = false
    chatLoading.value = false
    executingTool.value = false
    ElMessage.success('✅ AutoPilot 已立即暂停')
    fetchSession()
  } catch { ElMessage.error('暂停失败') }
}

async function resumeSession() {
  try {
    await request.post(`/api/sessions/${sessionId}/resume`)
    ElMessage.success('会话已恢复')
    fetchSession()
  } catch { ElMessage.error('恢复失败') }
}

// ============ 强制终止会话 ============
const aborting = ref(false)

async function abortSession() {
  aborting.value = true
  try {
    await request.post(`/api/sessions/${sessionId}/abort`)
    ElMessage.success('终止信号已发送，正在回收任务...')
    stopExecution()
    // 不阻塞等待，WebSocket 会收到中止消息并自动更新状态
    fetchSession()
  } catch (e) {
    ElMessage.error(`终止失败: ${e?.response?.data?.detail || e?.message || e}`)
  } finally {
    aborting.value = false
  }
}

// ============ 策略设置 ============
function sendStrategy() {
  if (!strategyText.value.trim()) {
    ElMessage.warning('请输入策略内容')
    return
  }
  const ws = getGlobalWebSocket()
  if (!ws || !ws.send) {
    ElMessage.error('WebSocket 未连接，无法发送策略')
    return
  }
  ws.send({
    type: 'strategy',
    session_id: sessionId,
    text: strategyText.value.trim(),
  })
  showStrategyDialog.value = false
  ElMessage.success('策略已发送，AI 将在后续决策中参考')
  strategyText.value = ''
}

// ============ 状态查询 ============
function queryStatus() {
  const ws = getGlobalWebSocket()
  if (!ws || !ws.send) {
    ElMessage.error('WebSocket 未连接，无法查询状态')
    return
  }
  ws.send({
    type: 'status',
    session_id: sessionId,
  })
  ElMessage.info('状态查询已发送，等待响应...')
}

// ★ WebSocket 状态响应处理器（在 onMounted 中注册）
let unregisterStatusHandler = null
function setupStatusHandler() {
  unregisterStatusHandler = registerMessageHandler('command_response', (data) => {
    if (data.command === 'status' && data.success) {
      autoPilotStatus.value = data.status
      showStatusDialog.value = true
    }
  })
}

async function downloadReport() {
  window.open(`/api/sessions/${sessionId}/report`, '_blank')
  ElMessage.success('报告已生成，正在打开...')
}

// ============ 拖拽调整宽度 ============
const dualPanelRef = ref(null)
const leftWidth = ref(Math.floor(window.innerWidth / 2))
const isResizing = ref(false)

function startResize(e) {
  isResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  const startX = e.clientX
  const startWidth = leftWidth.value

  function onMouseMove(ev) {
    const delta = ev.clientX - startX
    const total = dualPanelRef.value?.offsetWidth || 1200
    const newWidth = Math.max(300, Math.min(total - 300, startWidth + delta))
    leftWidth.value = newWidth
  }

  function onMouseUp() {
    isResizing.value = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// ============ 拖拽调整垂直比例（日志 vs 终端） ============
const logFlex = ref(8)
const terminalFlex = ref(2)
const isVertResizing = ref(false)

function startVertResize(e) {
  isVertResizing.value = true
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
  const startY = e.clientY
  const startLogFlex = logFlex.value
  const startTerminalFlex = terminalFlex.value
  const totalFlex = startLogFlex + startTerminalFlex

  function onMouseMove(ev) {
    const delta = ev.clientY - startY
    // 把像素变化映射到 flex 值变化
    const flexDelta = Math.round(delta / 10)
    const newLogFlex = Math.max(2, Math.min(totalFlex - 1, startLogFlex + flexDelta))
    const newTerminalFlex = totalFlex - newLogFlex
    logFlex.value = newLogFlex
    terminalFlex.value = newTerminalFlex
  }

  function onMouseUp() {
    isVertResizing.value = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// ============ ★ 改造：自定义拖拽条替代原生 CSS resize ============
const sessionDetailRef = ref(null)
const SETTINGS_BOX_KEY = `session_container_height_${sessionId}`
const boxHeight = ref(700)

// ============ WebSocket 连接状态 ============
const wsConnected = ref(true)
const wsReconnecting = ref(false)
let wsReconnectTimer = null
const WS_RECONNECT_INTERVAL = 5000
const MAX_RECONNECT_ATTEMPTS = 20
let wsReconnectAttempts = 0

// ============ ★ 新：命令确认对话框（三按钮 + 修改 + 跳过）============
const commandDialogVisible = ref(false)
const commandDialogData = ref(null)  // { command, reason, target, state, session_id }
const showModifyInput = ref(false)
const commandModifyInput = ref('')
const skipInputVisible = ref(false)  // 跳过时显示用户输入框
const skipUserInput = ref('')

/** 允许执行（不做任何修改） */
function onCommandExecute() {
  const data = commandDialogData.value
  if (!data) return
  commandDialogVisible.value = false
  showModifyInput.value = false
  skipInputVisible.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_command_confirm_response',
      session_id: data.session_id || '',
      action: 'execute',
      modified_command: '',
      user_input: '',
    })
  }
}

/** 点击"修改命令"按钮 → 显示修改输入框 */
function onCommandModifyBtn() {
  showModifyInput.value = true
  commandModifyInput.value = commandDialogData.value?.command || ''
}

/** 确认修改后的命令 */
function onCommandModifyConfirm() {
  const data = commandDialogData.value
  if (!data) return
  commandDialogVisible.value = false
  showModifyInput.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_command_confirm_response',
      session_id: data.session_id || '',
      action: 'modify',
      modified_command: commandModifyInput.value || data.command,
      user_input: '',
    })
  }
}

/** 跳过 → 显示跳过输入框（或直接跳过） */
function onCommandSkip() {
  const data = commandDialogData.value
  if (!data) return
  if (!skipInputVisible.value) {
    // 第一次点击跳过：显示输入框
    skipInputVisible.value = true
    return
  }
  // 已经有输入了 → 发送跳过
  commandDialogVisible.value = false
  showModifyInput.value = false
  skipInputVisible.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_command_confirm_response',
      session_id: data.session_id || '',
      action: 'skip',
      modified_command: '',
      user_input: skipUserInput.value || '',
    })
  }
  skipUserInput.value = ''
}

/** 跳过 + 用户指定了下一步 */
function onCommandSkipWithInput() {
  if (!skipUserInput.value.trim()) {
    onCommandSkip()
    return
  }
  const data = commandDialogData.value
  if (!data) return
  commandDialogVisible.value = false
  showModifyInput.value = false
  skipInputVisible.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_command_confirm_response',
      session_id: data.session_id || '',
      action: 'skip',
      modified_command: '',
      user_input: skipUserInput.value,
    })
  }
  skipUserInput.value = ''
}

/** 终止 AutoPilot */
function onCommandAbort() {
  const data = commandDialogData.value
  if (!data) return
  commandDialogVisible.value = false
  showModifyInput.value = false
  skipInputVisible.value = false
  skipUserInput.value = ''
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_command_confirm_response',
      session_id: data.session_id || '',
      action: 'abort',
      modified_command: '',
      user_input: '',
    })
  }
  // 同时调用 abortSession 清理
  abortSession()
}

// ============ ★ 新：恢复 AutoPilot 确认对话框 ============
const resumeDialogVisible = ref(false)
const resumeSessionData = ref({})

function showResumeDialog() {
  resumeSessionData.value = {
    phase: session.value?.phase || '未知',
    lastStatus: session.value?.status || '等待执行',
  }
  resumeDialogVisible.value = true
}

function showResumeConfirm() {
  // 页面刷新后检测到未完成进度，弹窗询问是否恢复
  const stageNames = {1:'初始化',2:'信息收集',3:'漏洞排序',4:'漏洞利用',5:'报告生成'}
  ElMessageBox.confirm(
    `检测到目标 ${session.value?.target || ''} 有未完成的 AutoPilot 进度（已执行到 ${stageNames[savedProgress.value] || 'Stage'+savedProgress.value}），是否恢复执行？`,
    '检测到未完成任务',
    {
      confirmButtonText: '恢复执行',
      cancelButtonText: '重新开始',
      type: 'info',
    }
  ).then(() => {
    // 用户选择恢复 → 启动 AutoPilot
    confirmStartAutoPilot()
  }).catch(() => {
    // 用户选择重新开始 → 正常启动
    // 不需要额外操作，用户点"启动 AutoPilot"按钮即可
    ElMessage.info('点击"启动 AutoPilot"按钮可重新开始')
  })
}

function onResumeConfirm() {
  resumeDialogVisible.value = false
  // 调用恢复接口
  resumeSession()
}

// ============ ★ 新：阶段进度状态 ============
const autoPilotPhase = ref('')     // 当前阶段（Stage1/2/3/4/5）
const autoPilotState = ref('')     // 当前详细状态（RUNNING/PAUSED/ABORTED）
const pendingCommand = ref('')     // 待执行的命令
const autoPilotStep = ref(0)       // 已执行命令数
const autoPilotPhaseName = ref('') // 阶段中文名（初始化/信息收集/...）
const toolCount = ref(0)        // 工具执行计数
const autoPilotProgress = ref(0)   // 总进度百分比（0~100）
const autoPilotDescription = ref('') // 当前状态描述文本
const credentials = ref([])        // 发现的凭证列表
const savedProgress = ref(0)       // 已保存的进度阶段

// 阶段映射
const PHASE_LABELS = {
  initialization: '初始化',
  reconnaissance: '信息收集',
  vuln_scan: '漏洞扫描',
  exploitation: '漏洞利用',
  privesc: '权限提升',
  lateral: '横向移动',
  collection: '数据收集',
}

function getPhaseLabel(phase) {
  return PHASE_LABELS[phase] || phase || ''
}

// ============ 原有：AutoPilot 确认对话框（保留兼容）============
const confirmDialogVisible = ref(false)
const confirmDialogMessage = ref('')
const confirmDialogSessionId = ref('')
function onConfirmDialogConfirm() {
  confirmDialogVisible.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_confirm_response',
      session_id: confirmDialogSessionId.value,
      confirm: true,
    })
  }
  console.log('[AutoPilot] 已发送确认: session_id=' + confirmDialogSessionId.value)
}
function onConfirmDialogAbort() {
  confirmDialogVisible.value = false
  const ws = getGlobalWebSocket()
  if (ws && ws.send) {
    ws.send({
      type: 'autopilot_confirm_response',
      session_id: confirmDialogSessionId.value,
      confirm: false,
    })
  }
  console.log('[AutoPilot] 已发送中止: session_id=' + confirmDialogSessionId.value)
}

let unregisterAutoPilotHandler = null  // autopilot_log 处理器卸载函数
let unregisterConfirmHandler = null     // 确认请求处理器卸载函数
let unregisterAiResponseHandler = null  // autopilot_ai_response 处理器卸载函数
let unregisterExecResultHandler = null  // autopilot_exec_result 处理器卸载函数
let unregisterCmdConfirmHandler = null  // autopilot_command_confirm_request 处理器卸载函数
let unregisterCmdPendingHandler = null  // autopilot_command_pending 处理器卸载函数
let unregisterCmdModifiedHandler = null // autopilot_command_modified 处理器卸载函数
let unregisterCmdSkippedHandler = null  // autopilot_command_skipped 处理器卸载函数
let unregisterToolStartHandler = null   // tool_exec_started 处理器卸载函数
let unregisterToolBlockedHandler = null // tool_exec_blocked 处理器卸载函数
let unregisterToolResultHandler = null  // tool_exec_result 处理器卸载函数
let unregisterNotificationHandler = null // autopilot_notification 处理器卸载函数
let unregisterStateChangeHandler = null  // autopilot_state_change 处理器卸载函数
let unregisterManualCmdHandler = null   // autopilot_manual_command 处理器卸载函数
let unregisterToolChoiceHandler = null  // autopilot_tool_choice 处理器卸载函数


function loadBoxHeight() {
  try {
    const saved = localStorage.getItem(SETTINGS_BOX_KEY)
    if (saved) {
      boxHeight.value = Math.max(400, parseInt(saved, 10) || 0)
      return
    }
  } catch { /* ignore */ }
  // ★ 默认 700px 固定初始高度，无本地记录时容器直接有尺寸
  boxHeight.value = 700
}

function saveBoxHeight() {
  try {
    localStorage.setItem(SETTINGS_BOX_KEY, String(boxHeight.value))
  } catch { /* ignore */ }
}

/** 鼠标拖拽：底部蓝色条按下滑动调整容器高度 */
function startBoxResize(e) {
  e.preventDefault()
  e.stopPropagation()

  const startY = e.clientY
  const startHeight = boxHeight.value || sessionDetailRef.value?.offsetHeight || 700

  document.body.style.cursor = 'ns-resize'
  document.body.style.userSelect = 'none'

  function onMouseMove(ev) {
    const delta = ev.clientY - startY
    const nextHeight = Math.max(
      400,
      Math.min(window.innerHeight * 5, startHeight + delta)
    )
    boxHeight.value = Math.round(nextHeight)
    saveBoxHeight()
  }

  function onMouseUp() {
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// ============ 工具输出压缩（前端） ============
function compressToolOutput(output, command = '') {
  if (!output || output.length <= 2000) return output
  
  const cmdLower = command.toLowerCase()
  
  // 1. HTML/HTTP 响应
  const isHTML = output.toLowerCase().includes('<html') || output.toLowerCase().includes('<!doctype')
  const isHTTP = output.trim().startsWith('HTTP/')
  if (isHTML || isHTTP) {
    return compressHTMLOutput(output, command)
  }
  
  // 2. nmap 扫描结果
  if (cmdLower.includes('nmap') || cmdLower.startsWith('nmap')) {
    return compressNmapOutput(output, command)
  }
  
  // 3. sqlmap 输出
  if (cmdLower.includes('sqlmap') || output.toLowerCase().includes('sqlmap')) {
    return compressSqlmapOutput(output, command)
  }
  
  // 4. gobuster/ffuf/dirb 目录爆破
  if (['gobuster', 'ffuf', 'dirb', 'dirsearch'].some(t => cmdLower.includes(t))) {
    return compressDirscanOutput(output, command)
  }
  
  // 5. nikto 扫描结果
  if (cmdLower.includes('nikto') || output.toLowerCase().includes('nikto')) {
    return compressNiktoOutput(output, command)
  }
  
  // 6. nuclei 扫描结果
  if (cmdLower.includes('nuclei') || output.toLowerCase().includes('nuclei')) {
    return compressNucleiOutput(output, command)
  }
  
  // 7. hydra 暴力破解
  if (cmdLower.includes('hydra')) {
    return compressHydraOutput(output, command)
  }
  
  // 8. wpscan 输出
  if (cmdLower.includes('wpscan') || output.toLowerCase().includes('wpscan')) {
    return compressWpscanOutput(output, command)
  }
  
  // 9. 通用压缩
  return genericCompress(output, command)
}

// 压缩 HTML/HTTP 响应
function compressHTMLOutput(output, command = '') {
  const metadata = []
  
  // 提取 title
  const titleMatch = output.match(/<title[^>]*>(.*?)<\/title>/i)
  if (titleMatch) metadata.push(`Title: ${titleMatch[1].trim()}`)
    
  // 提取 Generator
  const genMatch = output.match(/<meta[^>]*name=["']generator["'][^>]*content=["'](.*?)["']/i)
  if (genMatch) metadata.push(`Generator: ${genMatch[1].trim()}`)
    
  // 提取 Server
  const serverMatch = output.match(/Server:\s*(.+)/i)
  if (serverMatch) metadata.push(`Server: ${serverMatch[1].trim()}`)
    
  // 构建压缩结果
  let result = '[Web 响应元数据提取]\n'
  result += `原始: ${output.length} 字符 | 压缩后: 仅保留元数据\n\n`
  if (metadata.length > 0) {
    result += '【关键信息】\n'
    result += metadata.join('\n') + '\n\n'
  }
    
  // 添加少量 HTML 片段
  const lines = output.split('\n').filter(l => l.trim())
  if (lines.length > 20) {
    result += '【HTML 片段（前 10 行）】\n'
    result += lines.slice(0, 10).join('\n') + '\n...\n'
    result += '【HTML 片段（后 10 行）】\n'
    result += lines.slice(-10).join('\n')
  }
    
  return result.substring(0, 1500)
}

// 压缩 nmap 输出
function compressNmapOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const openPorts = lines.filter(l => (l.includes('/tcp') || l.includes('/udp')) && l.includes('open'))
  const osInfo = lines.filter(l => l.includes('OS:') || l.includes('Aggressive OS'))
  
  let result = '[nmap 扫描结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 发现: ${openPorts.length} 个开放端口\n\n`
    
  if (openPorts.length > 0) {
    result += `【开放端口】(${openPorts.length} 个)\n`
    result += openPorts.slice(0, 20).join('\n')
    if (openPorts.length > 20) result += `\n... 还有 ${openPorts.length - 20} 个端口`
    result += '\n\n'
  }
    
  if (osInfo.length > 0) {
    result += '【操作系统识别】\n'
    result += osInfo.slice(0, 5).join('\n') + '\n\n'
  }
    
  return result.substring(0, 1500)
}

// 压缩 sqlmap 输出
function compressSqlmapOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const injectionInfo = lines.filter(l => ['injection', 'parameter', 'Type:', 'Title:', 'Payload:'].some(k => l.includes(k)))
  const dbInfo = lines.filter(l => ['Database:', 'DBMS:', 'web server', 'web application'].some(k => l.includes(k)))
    
  let result = '[sqlmap 扫描结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 压缩后: 仅保留关键信息\n\n`
    
  if (injectionInfo.length > 0) {
    result += '【注入点信息】\n'
    result += injectionInfo.slice(0, 10).join('\n') + '\n\n'
  }
    
  if (dbInfo.length > 0) {
    result += '【数据库信息】\n'
    result += dbInfo.slice(0, 10).join('\n') + '\n\n'
  }
    
  return result.substring(0, 1500)
}

// 压缩目录爆破输出
function compressDirscanOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const foundPaths = lines.filter(l => l.includes('Status:') || l.includes('(Status:'))
  const statusCodes = {}
    
  foundPaths.forEach(p => {
    const match = p.match(/Status:\s*(\d+)/i)
    if (match) {
      const code = match[1]
      statusCodes[code] = (statusCodes[code] || 0) + 1
    }
  })
    
  let result = '[目录爆破结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 发现: ${foundPaths.length} 个路径\n\n`
    
  if (Object.keys(statusCodes).length > 0) {
    result += '【状态码统计】\n'
    Object.keys(statusCodes).sort().forEach(code => {
      result += `  ${code}: ${statusCodes[code]} 个路径\n`
    })
    result += '\n'
  }
    
  if (foundPaths.length > 0) {
    result += `【发现的路径】(${foundPaths.length} 个)\n`
    const important = foundPaths.filter(p => ['200', '204', '301', '302', '403'].some(c => p.includes(c)))
    const other = foundPaths.filter(p => !important.includes(p))
        
    result += '（状态码 200/301/302/403 等）\n'
    result += important.slice(0, 20).join('\n')
    if (important.length > 20) result += `\n... 还有 ${important.length - 20} 个重要路径`
    result += '\n\n'
        
    if (other.length > 0) {
      result += '（其他状态码）\n'
      result += other.slice(0, 10).join('\n')
      if (other.length > 10) result += `\n... 还有 ${other.length - 10} 个其他路径`
    }
  }
    
  return result.substring(0, 1500)
}

// 压缩 nikto 输出
function compressNiktoOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const vulnerabilities = lines.filter(l => l.startsWith('+') && ['VULNERABLE', 'CVE-', 'exploit', 'password', 'admin', 'login'].some(k => l.includes(k)))
  const infoItems = lines.filter(l => l.startsWith('+') && !vulnerabilities.includes(l))
    
  let result = '[nikto 扫描结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 压缩后: 仅保留关键信息\n\n`
    
  if (vulnerabilities.length > 0) {
    result += `【漏洞发现】(${vulnerabilities.length} 个)\n`
    result += vulnerabilities.slice(0, 15).join('\n')
    if (vulnerabilities.length > 15) result += `\n... 还有 ${vulnerabilities.length - 15} 个漏洞`
    result += '\n\n'
  }
    
  if (infoItems.length > 0) {
    result += `【其他发现】(${infoItems.length} 个)\n`
    result += infoItems.slice(0, 15).join('\n')
    if (infoItems.length > 15) result += `\n... 还有 ${infoItems.length - 15} 个发现`
  }
    
  return result.substring(0, 1500)
}

// 压缩 nuclei 输出
function compressNucleiOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const vulnerabilities = []
  const stats = {}
    
  lines.forEach(line => {
    if (line.startsWith('{')) {
      try {
        const data = JSON.parse(line)
        if (data.info && data.info.name) {
          const name = data.info.name
          const severity = data.info.severity || 'unknown'
          vulnerabilities.push(`[${severity.toUpperCase()}] ${name}`)
          stats[severity] = (stats[severity] || 0) + 1
        }
      } catch {}
    } else {
      if (['[critical]', '[high]', '[medium]', '[low]'].some(k => line.includes(k))) {
        vulnerabilities.push(line)
      }
    }
  })
    
  let result = '[nuclei 扫描结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 发现: ${vulnerabilities.length} 个漏洞\n\n`
    
  if (Object.keys(stats).length > 0) {
    result += '【严重级别统计】\n'
    ['critical', 'high', 'medium', 'low', 'info'].forEach(severity => {
      if (stats[severity]) result += `  ${severity.toUpperCase()}: ${stats[severity]} 个\n`
    })
    result += '\n'
  }
    
  if (vulnerabilities.length > 0) {
    result += `【漏洞列表】(${vulnerabilities.length} 个)\n`
    result += vulnerabilities.slice(0, 20).join('\n')
    if (vulnerabilities.length > 20) result += `\n... 还有 ${vulnerabilities.length - 20} 个漏洞`
  }
    
  return result.substring(0, 1500)
}

// 压缩 hydra 输出
function compressHydraOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const successCredentials = lines.filter(l => l.includes('login:') && l.includes('host:'))
  const attemptsInfo = lines.filter(l => l.toLowerCase().includes('attempts') || l.toLowerCase().includes('finished'))
    
  let result = '[hydra 暴力破解结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符\n\n`
    
  if (successCredentials.length > 0) {
    result += `【✅ 成功凭据】(${successCredentials.length} 个)\n`
    result += successCredentials.join('\n') + '\n\n'
  }
    
  if (attemptsInfo.length > 0) {
    result += '【尝试统计】\n'
    result += attemptsInfo.join('\n') + '\n\n'
  }
    
  if (successCredentials.length === 0) {
    result += '[INFO] 未发现有效凭据\n'
  }
    
  return result.substring(0, 1500)
}

// 压缩 wpscan 输出
function compressWpscanOutput(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const vulnerabilities = lines.filter(l => ['Vulnerability', 'CVE-', 'exploit', 'vuln'].some(k => l.includes(k)))
  const plugins = lines.filter(l => l.includes('Plugin(s):') || l.includes('[P]'))
  const themes = lines.filter(l => l.includes('Theme(s):') || l.includes('[T]'))
    
  let result = '[wpscan 扫描结果摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 压缩后: 仅保留关键信息\n\n`
    
  if (vulnerabilities.length > 0) {
    result += `【漏洞发现】(${vulnerabilities.length} 个)\n`
    result += vulnerabilities.slice(0, 15).join('\n')
    if (vulnerabilities.length > 15) result += `\n... 还有 ${vulnerabilities.length - 15} 个漏洞`
    result += '\n\n'
  }
    
  if (plugins.length > 0) {
    result += `【插件】(${plugins.length} 个)\n`
    result += plugins.slice(0, 10).join('\n')
    if (plugins.length > 10) result += `\n... 还有 ${plugins.length - 10} 个插件`
    result += '\n\n'
  }
    
  if (themes.length > 0) {
    result += `【主题】(${themes.length} 个)\n`
    result += themes.slice(0, 5).join('\n') + '\n\n'
  }
    
  return result.substring(0, 1500)
}

// 通用压缩
function genericCompress(output, command = '') {
  const lines = output.split('\n').map(l => l.trim()).filter(l => l)
  const keywords = ['open', 'closed', 'filtered', 'CVE-', 'vulnerability', 'found', 'password', 'login', 'error', 'port', 'version', 'VULNERABLE', 'exploit', 'payload', 'success', 'cracked', 'valid']
  
  const keyLines = lines.filter(line => keywords.some(k => line.includes(k))).slice(0, 30)
  const head = lines.slice(0, 10)
  const tail = lines.slice(-10)
    
  const merged = [...new Set([...head, ...keyLines, ...tail])].slice(0, 50)
    
  let result = '[工具输出压缩摘要]\n'
  result += `命令: ${command}\n`
  result += `原始: ${output.length} 字符 | 压缩后: ${merged.join('\n').length} 字符\n\n`
  result += merged.join('\n')
    
  return result.substring(0, 1500)
}

// ============ 日志 ============
function clearLogs() { logs.value = [] }

/** 格式化日志时间：精确到分钟即可，不显示秒 */
function formatLogTime(timestamp) {
  if (!timestamp) return ''
  // 如果是数字时间戳（毫秒）
  if (typeof timestamp === 'number') {
    const d = new Date(timestamp)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    return `${hh}:${mm}`
  }
  // 如果是字符串，尝试解析
  if (typeof timestamp === 'string') {
    // 尝试解析为日期
    const d = new Date(timestamp)
    if (!isNaN(d.getTime())) {
      const hh = String(d.getHours()).padStart(2, '0')
      const mm = String(d.getMinutes()).padStart(2, '0')
      return `${hh}:${mm}`
    }
    // 如果已经是 HH:MM:SS 格式，去掉秒
    const match = timestamp.match(/(\d{1,2}:\d{2}):\d{2}/)
    if (match) return match[1]
    // 如果已经是 HH:MM 格式，直接返回
    if (/^\d{1,2}:\d{2}$/.test(timestamp)) return timestamp
    return timestamp
  }
  return ''
}

// ============ 终端 ============
const terminalLines = ref([])
const cmdInput = ref('')
const cmdInputRef = ref(null)
const terminalRef = ref(null)
const cmdHistory = ref([])
const cmdHistoryIndex = ref(-1)

const STORAGE_TERMINAL_KEY = `terminal_history_${sessionId}`

function loadTerminalHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_TERMINAL_KEY)
    if (raw) terminalLines.value = JSON.parse(raw)
  } catch { terminalLines.value = [] }
}

function saveTerminalHistory() {
  const maxLines = 100
  if (terminalLines.value.length > maxLines) {
    terminalLines.value = terminalLines.value.slice(-maxLines)
  }
  try {
    localStorage.setItem(STORAGE_TERMINAL_KEY, JSON.stringify(terminalLines.value))
  } catch { /* ignore */ }
}

function addTerminalLine(text, type = 'output') {
  terminalLines.value.push({ text, type, time: Date.now() })
  saveTerminalHistory()
  nextTick(() => {
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight
    }
  })
}

function clearTerminal() {
  terminalLines.value = []
  saveTerminalHistory()
}

async function executeManualCommand() {
  const cmd = cmdInput.value.trim()
  if (!cmd) return

  // 记录历史
  if (cmdHistory.value[cmdHistory.value.length - 1] !== cmd) {
    cmdHistory.value.push(cmd)
  }
  cmdHistoryIndex.value = cmdHistory.value.length

  addTerminalLine(cmd, 'cmd')
  cmdInput.value = ''

  try {
    const res = await request.post('/api/tools/execute', {
      name: 'execute_kali_command',
      arguments: { command: cmd }
    })
    if (res.output) addTerminalLine(res.output, 'output')
    if (res.error) addTerminalLine(`❌ ${res.error}`, 'error')
    if (res.blocked) addTerminalLine(`🚫 命令被拦截: ${res.blocked_reason}`, 'error')
    addTerminalLine(`⏱ 耗时: ${res.duration?.toFixed(2) || 0}s  返回码: ${res.return_code ?? 'N/A'}`)
  } catch (e) {
    addTerminalLine(`❌ 执行失败: ${e.message || e}`, 'error')
  }
}

function prevCmdHistory() {
  if (cmdHistory.value.length === 0) return
  cmdHistoryIndex.value = Math.max(0, cmdHistoryIndex.value - 1)
  cmdInput.value = cmdHistory.value[cmdHistoryIndex.value] || ''
}

function nextCmdHistory() {
  if (cmdHistoryIndex.value >= cmdHistory.value.length - 1) {
    cmdHistoryIndex.value = cmdHistory.value.length
    cmdInput.value = ''
    return
  }
  cmdHistoryIndex.value = Math.min(cmdHistory.value.length - 1, cmdHistoryIndex.value + 1)
  cmdInput.value = cmdHistory.value[cmdHistoryIndex.value] || ''
}

// ============ AI 对话 ============
const chatInput = ref('')
const chatMessages = ref([])
const chatLoading = ref(false)
const executingTool = ref(false)
const stopRequested = ref(false)
let abortController = null
const chatMessagesRef = ref(null)
const pendingDecision = ref(null)  // 工具失败后的决策弹窗数据
const isAutoRunning = ref(false)  // 防止 autoChatLoop 重入

const CHAT_STORAGE_KEY = `chat_history_${sessionId}`
const LOGS_STORAGE_KEY = `session_logs_${sessionId}`

function loadChatHistory() {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) chatMessages.value = parsed
    }
  } catch { chatMessages.value = [] }
}

function saveChatHistory() {
  try {
    // ★ 只保存非空数据，避免清除后自动覆盖历史
    if (chatMessages.value.length > 0) {
      localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatMessages.value))
    }
  } catch { /* ignore */ }
}

function scrollChatToBottom() {
  nextTick(() => {
    if (chatMessagesRef.value) {
      const el = chatMessagesRef.value
      el.scrollTop = el.scrollHeight
    }
  })
}

// ============ 日志持久化（刷新恢复）============
function saveLogs() {
  try {
    if (logs.value.length > 0) {
      localStorage.setItem(LOGS_STORAGE_KEY, JSON.stringify(logs.value.slice(-200)))
    }
  } catch { /* ignore */ }
}

function loadLogs() {
  try {
    const raw = localStorage.getItem(LOGS_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) logs.value = parsed
    }
  } catch { logs.value = [] }
}

function clearChat() {
  chatMessages.value = []
  // ★ 手动清除：主动写入空数组到 localStorage
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify([]))
  } catch { /* ignore */ }
  ElMessage.success('对话已清空')
}

async function sendChatMessage() {
  const content = chatInput.value.trim()
  if (!content) return
  if (content.length > 5000) {
    ElMessage.warning('消息过长（上限 5000 字符）')
    return
  }

  chatInput.value = ''

  // ★ /run 命令：跳过 AI，直接执行
  if (content.startsWith('/run ')) {
    const cmd = content.slice(5).trim()
    if (!cmd) { ElMessage.warning('请输入要执行的命令'); return }
    chatMessages.value.push({ role: 'user', content: `/run ${cmd}` })
    saveChatHistory()
    scrollChatToBottom()
    // 直接调用执行接口
    chatMessages.value.push({ role: 'assistant', content: `⚡ 正在执行: ${cmd}` })
    try {
      const res = await request.post('/api/tools/execute', {
        name: 'execute_kali_command',
        arguments: { command: cmd }
      }, { timeout: 300000 })
      const last = chatMessages.value[chatMessages.value.length - 1]
      const output = (res?.output || res?.result || '').substring(0, 1500)
      const err = (res?.error || '').substring(0, 500)
      last.content = output
        ? `✅ 执行完成 (${(res?.duration || 0).toFixed(1)}s)\n\n\`\`\`\n${output}\n\`\`\``
        : `❌ 执行失败: ${err || '无输出'}`
      if (res?.return_code !== undefined) last.content += `\n\n返回码: ${res.return_code}`
    } catch (e) {
      const last = chatMessages.value[chatMessages.value.length - 1]
      last.content = `❌ 执行异常: ${e?.message || e}`
    }
    saveChatHistory()
    scrollChatToBottom()
    return
  }

  // ★ AutoPilot 运行时：自动暂停 + 处理用户消息
  if (isAutoRunning.value) {
    // 先暂停 AutoPilot
    try { await request.post(`/api/sessions/${sessionId}/pause`) } catch {}
    isAutoRunning.value = false
    chatLoading.value = false
    executingTool.value = false
    if (abortController) { abortController.abort(); abortController = null }
    ElMessage.success('AutoPilot 已暂停，现在可以自由对话')

    chatMessages.value.push({ role: 'user', content })
    saveChatHistory()
    scrollChatToBottom()

    // 正常调用 AI 回复
    stopRequested.value = false
    abortController = new AbortController()
    chatLoading.value = true
    try {
      const msgs = buildMessagesPayload()
      await streamLLMOnce(msgs)
    } catch { /* ignore */ }
    finally {
      chatLoading.value = false
      abortController = null
      saveChatHistory()
      scrollChatToBottom()
    }
    return
  }

  // 非 AutoPilot 模式：正常对话
  stopRequested.value = false
  abortController = new AbortController()

  chatMessages.value.push({ role: 'user', content })
  saveChatHistory()
  scrollChatToBottom()

  chatLoading.value = true
  try {
    await autoChatLoop()
  } catch (e) {
    if (e?.name !== 'CanceledError' && e?.name !== 'AbortError') {
      ElMessage.error(`对话异常: ${e.message || e}`)
    }
  } finally {
    chatLoading.value = false
    executingTool.value = false
    stopRequested.value = false
    abortController = null
    saveChatHistory()
    scrollChatToBottom()
  }
}

/**
 * SSE 流式调用 LLM：逐块更新消息内容，让用户看到实时输出
 */
async function streamLLMOnce(msgs) {
  const aiMsg = { role: 'assistant', content: '', tool_calls: undefined, _streamDone: false }
  const msgIndex = chatMessages.value.length
  chatMessages.value.push(aiMsg)
  saveChatHistory()
  scrollChatToBottom()
  
  // 启动伪流式
  startPseudoStreaming(msgIndex, '')

  let toolCalls = []
  let fullContent = ''
  let timeoutId = null

  try {
    const res = await fetch('/api/llm/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
      body: JSON.stringify({ messages: msgs }),
      signal: abortController?.signal,
    })
    // ★ 添加 120 秒超时，防止大模型推理卡住导致前端永久等待
    timeoutId = setTimeout(() => abortController?.abort(), 120000)
    if (!res.ok) {
      clearTimeout(timeoutId)
      const errData = await res.json().catch(() => ({}))
      throw new Error(errData?.detail || `LLM 请求失败 (${res.status})`)
    }
    const reader = res.body.getReader()
    // 获取响应后清除超时，后续流式读取靠 done 事件自然结束
    clearTimeout(timeoutId)
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let doneReceived = false

    while (!doneReceived) {
      // ★ 停止按钮立即生效：SSE 读取循环中检查停止标志
      if (stopRequested.value) {
        reader.cancel()
        break
      }
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // 用 \n\n 作为 SSE 事件分隔符
      const rawEvents = buffer.split('\n\n')
      // 最后一段可能是不完整的事件，保留到下一轮
      buffer = rawEvents.pop() || ''

      for (const rawEvent of rawEvents) {
        const eventBlock = rawEvent.trim()
        if (!eventBlock) continue

        // SSE 事件可能包含多行（id: / event: / data: / retry:）
        let dataLine = ''
        for (const line of eventBlock.split('\n')) {
          if (line.startsWith('data: ')) {
            dataLine = line.slice(6)
          }
        }
        if (!dataLine) continue

        // OpenAI 兼容：data: [DONE] 表示流结束
        if (dataLine === '[DONE]') {
          doneReceived = true
          break
        }

        let event
        try { event = JSON.parse(dataLine) } catch { continue }

        if (event.type === 'content') {
          // ★ 关键：必须取 event.text，不要用整个event对象赋值给content
          const text = event.text || ''
          fullContent += text
          aiMsg.content = fullContent
          // ★ 伪流式：追加到流式缓冲区
          appendStreamContent(msgIndex, text)
          scrollChatToBottom()
        } else if (event.type === 'tool_calls') {
          toolCalls = event.calls || []
          console.log('[流式] 收到工具调用:', event.calls)
        } else if (event.type === 'error') {
          throw new Error(event.message)
        } else if (event.type === 'done') {
          doneReceived = true
          break
        }
      }

      // 让出事件循环，确保 UI 更新（关键！）
      await new Promise(resolve => setTimeout(resolve, 0))
    }
  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId)
    // ★ 失败时移除 aiMsg，防止残消息
    const idx = chatMessages.value.indexOf(aiMsg)
    if (idx !== -1) {
      chatMessages.value.splice(idx, 1)
    }
    if (e?.name === 'AbortError') {
      // 已取消，不需要额外消息
    } else {
      chatMessages.value.push({ role: 'assistant', content: `❌ ${e.message || 'LLM 调用失败'}` })
    }
    saveChatHistory()
    throw e
  }

  aiMsg.content = fullContent
  // ★ 伪流式结束
  finishPseudoStreaming(msgIndex)
  if (toolCalls.length) aiMsg.tool_calls = toolCalls
  if (aiMsg.content && aiMsg.content.includes('```state')) {
    aiMsg.content = aiMsg.content.replace(/```state[\s\S]*?```/g, '').trim()
  }
  saveChatHistory()
  return { toolCalls }
}

async function autoChatLoop() {
  // ★ 防重入保护：防止工具重复执行
  if (isAutoRunning.value) {
    console.warn('[autoChatLoop] 已有循环在运行，跳过')
    return
  }

  isAutoRunning.value = true
  let loopCount = 0
  const MAX_LOOPS = 10

  try {
    while (loopCount < MAX_LOOPS) {
      loopCount++

      if (stopRequested.value) {
        chatMessages.value.push({ role: 'assistant', content: '⏹️ 已手动停止' })
        saveChatHistory()
        return
      }

      // ---- 1. 流式调用 LLM ----
      chatLoading.value = true
      const msgsPayload = buildMessagesPayload()

      let toolCalls = []
      try {
        const result = await streamLLMOnce(msgsPayload)
        toolCalls = result.toolCalls
      } catch (e) {
        if (stopRequested.value || e?.name === 'AbortError') {
          saveChatHistory()
          return
        }
        chatLoading.value = false
        saveChatHistory()
        return
      }
      chatLoading.value = false

      // ---- 2. 如果没有 tool_calls，结束循环 ----
      if (!toolCalls || toolCalls.length === 0) {
        scrollChatToBottom()
        return
      }

      // ---- 3. 执行每个 tool_call ----
      executingTool.value = true
      for (const tc of toolCalls) {
        // ==== 检查停止信号 ====
        if (stopRequested.value) break

        const toolName = tc?.function?.name || ''
        let toolArgs = {}

        // 解析 arguments
        if (typeof tc?.function?.['arguments'] === 'string') {
          try {
            toolArgs = JSON.parse(tc.function.arguments)
          } catch {
            toolArgs = { command: tc.function.arguments }
          }
        } else if (tc?.function?.arguments_json) {
          toolArgs = tc.function.arguments_json
        } else if (tc?.function?.['arguments'] && typeof tc.function.arguments === 'object') {
          toolArgs = tc.function.arguments
        }

        const commandToExec = toolArgs?.command || ''

        // 同步输出到终端区域
        if (commandToExec) {
          addTerminalLine(`[AI 执行] ${commandToExec}`, 'cmd')
        }

        let execResult

        try {
          execResult = await request.post('/api/tools/execute',
            { name: toolName, arguments: toolArgs },
            { signal: abortController?.signal, timeout: 660000 }
          )
        } catch (e) {
          if (stopRequested.value || e?.name === 'CanceledError' || e?.name === 'AbortError') {
            break
          }
          const errMsg = e?.response?.data?.detail || e?.message || '工具执行失败'
          execResult = { success: false, output: '', error: errMsg }
        }

        // ==== 检查停止信号 ====
        if (stopRequested.value) break

        // 工具执行结果输出到终端
        if (commandToExec) {
          const outStr = execResult?.output ? String(execResult.output) : ''
          const errStr = execResult?.error ? String(execResult.error) : ''
          if (outStr) addTerminalLine(outStr, 'output')
          if (errStr) addTerminalLine(`❌ ${errStr}`, 'error')
          if (execResult?.blocked) addTerminalLine(`🚫 拦截: ${execResult.blocked_reason}`, 'error')
          const rc = execResult?.return_code ?? '?'
          addTerminalLine(`⏱ ${(execResult?.duration || 0).toFixed(2)}s  返回码: ${rc}`)
        }

        // ★ 将执行结果以 tool_result 附加到最新的 AI 消息上
        const lastMsg = chatMessages.value[chatMessages.value.length - 1]
        if (lastMsg) {
          let outputStr = execResult?.output
            ? (typeof execResult.output === 'string'
                ? execResult.output
                : JSON.stringify(execResult.output, null, 2))
            : ''
          const errStr = execResult?.error
            ? `
[错误] ${typeof execResult.error === 'string' ? execResult.error : JSON.stringify(execResult.error)}`
            : ''
          const rcStr = execResult?.return_code !== undefined && execResult?.return_code !== null
            ? `
[返回码] ${execResult.return_code}`
            : ''
          const fullResult = outputStr + errStr + rcStr
          if (fullResult.length > 2000) {
            outputStr = compressToolOutput(fullResult)
          }
          const newPart = outputStr.substring(0, 1500)
          if (lastMsg.tool_result) {
            lastMsg.tool_result += '\n---\n' + newPart
            if (lastMsg.tool_result.length > 3000) lastMsg.tool_result = lastMsg.tool_result.substring(0, 3000)
          } else {
            lastMsg.tool_result = newPart
          }
        }

        // ★ 检查是否需要用户决策（工具失败或被拦截）
        if ((execResult?.requires_user_decision && execResult?.next_steps?.length) || execResult?.blocked) {
          let reason = execResult?.retry_reason || '命令执行失败'
          let steps = execResult?.next_steps || []
          if (execResult?.blocked) {
            reason = `命令被安全拦截: ${execResult.blocked_reason || ''}`
            steps = [
              { label: '继续执行（忽略拦截）', action: 'force_execute', command: commandToExec },
              { label: '修改命令', action: 'modify', command: commandToExec },
              { label: '跳过此步骤', action: 'skip' },
            ]
          }
          pendingDecision.value = {
            command: commandToExec,
            reason: reason,
            next_steps: steps,
            is_manual: execResult?.manual || false,
          }
          break  // 暂停自动循环，等待用户决策
        }

        saveChatHistory()
        scrollChatToBottom()
      }
      executingTool.value = false

      // 如果有待用户决策，暂停自动循环
      if (pendingDecision.value) break
    }

    // 超过最大循环次数
    if (!stopRequested.value && !pendingDecision.value) {
      chatMessages.value.push({
        role: 'assistant',
        content: '⚠️ 工具调用已到达最大循环次数，请检查是否需要继续。',
      })
      saveChatHistory()
    }
  } finally {
    isAutoRunning.value = false
    chatLoading.value = false
    executingTool.value = false
  }
}

function stopExecution() {
  stopRequested.value = true
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  // ★ 通知后端强制终止正在运行的进程
  request.post('/api/tools/stop').catch(() => {})
  // ★ 同时取消 AutoPilot 后台任务
  const sid = sessionId.value
  if (sid) {
    request.post(`/api/sessions/${sid}/abort`).then(() => {
      ElMessage.warning('AutoPilot 已停止')
    }).catch(() => {})
  }
}

// ============ ★ 优化1：实时日志统一时间线 ============
/** 日志 category → 显示配置 */
const CATEGORY_CONFIG = {
  system:      { label: '系统', type: 'info',     cls: 'cat-system' },
  phase:       { label: '阶段', type: 'info',     cls: 'cat-phase' },
  tool_start:  { label: '执行', type: 'info',     cls: 'cat-tool-start' },
  tool_output: { label: '输出', type: 'info',     cls: 'cat-tool-output' },
  tool_end:    { label: '完成', type: 'success',  cls: 'cat-tool-end' },
  ai_thought:  { label: '思考', type: 'info',     cls: 'cat-ai-thought' },
  ai_analysis: { label: '分析', type: 'info',     cls: 'cat-ai-analysis' },
  ai_plan:     { label: '计划', type: 'info',     cls: 'cat-ai-plan' },
  finding:     { label: '发现', type: 'warning',  cls: 'cat-finding' },
  error:       { label: '错误', type: 'danger',  cls: 'cat-error' },
  warning:     { label: '警告', type: 'warning', cls: 'cat-warning' },
  success:     { label: '成功', type: 'success', cls: 'cat-success' },
  confirm:     { label: '确认', type: 'warning', cls: 'cat-confirm' },
}

const timelineLogs = computed(() => {
  const result = []
  for (const log of logs.value) {
    const category = log.category || 'system'
    const cfg = CATEGORY_CONFIG[category] || CATEGORY_CONFIG['system']
    result.push({
      time: log.timestamp,
      category,
      label: cfg.label,
      type: cfg.type,
      cls: cfg.cls,
      source: log.source || '',
      message: log.message || '',
      detail: log.detail || null,
    })
  }
  // 合并终端输出
  for (const line of terminalOutputLines.value) {
    result.push({
      time: line.time,
      category: 'tool_output',
      label: '输出',
      type: 'info',
      cls: 'cat-tool-output',
      source: 'Terminal',
      message: line.text || '',
      detail: null,
    })
  }
  return result
})

/** 左栏实时日志：只显示操作流水 */
const logTimeline = computed(() => {
  const LOG_CATEGORIES = ['tool_start', 'tool_end', 'tool_output', 'error', 'warning', 'success', 'system', 'phase', 'confirm']
  return timelineLogs.value.filter(l => LOG_CATEGORIES.includes(l.category))
})

/** 伪流式状态管理：key=消息索引 */
const streamingStates = reactive({})
const STREAM_INTERVAL_MS = 40  // 40ms 显示一个字符

function startPseudoStreaming(msgIndex, initialContent = '') {
  if (streamingStates[msgIndex]) {
    // 已有流式状态，只追加内容
    return
  }
  const state = reactive({
    target: initialContent || '',
    displayed: '',
    cursor: 0,
    timer: null,
    done: false,
  })
  streamingStates[msgIndex] = state
  
  if (initialContent) {
    _runStreamTimer(msgIndex, state)
  }
}

function appendStreamContent(msgIndex, text) {
  const state = streamingStates[msgIndex]
  if (!state) return
  state.target += text
  if (!state.timer && !state.done) {
    _runStreamTimer(msgIndex, state)
  }
}

function finishPseudoStreaming(msgIndex) {
  const state = streamingStates[msgIndex]
  if (!state) return
  state.done = true
  if (state.timer) {
    clearInterval(state.timer)
    state.timer = null
  }
  state.displayed = state.target
  state.cursor = state.target.length
  // 标记该消息的流式显示完成
  const msg = chatMessages.value[msgIndex]
  if (msg) msg._streamDone = true
}

function _runStreamTimer(msgIndex, state) {
  if (state.timer) return
  state.timer = setInterval(() => {
    if (state.cursor < state.target.length) {
      state.displayed = state.target.slice(0, state.cursor + 1)
      state.cursor++
      // 触发滚动
      scrollChatToBottom()
    } else {
      // 目标已全部显示
      clearInterval(state.timer)
      state.timer = null
      if (state.done) {
        const msg = chatMessages.value[msgIndex]
        if (msg) msg._streamDone = true
      }
    }
  }, STREAM_INTERVAL_MS)
}

/** 获取消息的显示文本（伪流式优先） */
function getDisplayText(msgIndex, msg) {
  const state = streamingStates[msgIndex]
  if (state && state.displayed) return state.displayed
  if (msg._streamDone) return msg.content || ''
  return msg.content || ''
}

/** 检查消息是否正在流式显示中 */
function isStreaming(msgIndex) {
  const state = streamingStates[msgIndex]
  return state && !state.done && (state.displayed.length > 0 || state.target.length > 0)
}

// ============ ★ 优化3：AI 结构化分析结果解析 ============

const AI_SECTION_KEYWORDS = [
  { key: 'summary', patterns: ['分析结果', '总结', '概述', 'Summary', '分析', '扫描总结', '执行总结'], label: '📊 分析总结', icon: '📊' },
  { key: 'phase', patterns: ['当前阶段', '当前状态', '阶段目标', 'Phase', '执行阶段'], label: '🎯 当前阶段', icon: '🎯' },
  { key: 'found', patterns: ['已发现', '发现', 'Found', 'Findings', 'Discovered', '扫描发现', '漏洞发现', '发现信息'], label: '📌 已发现', icon: '📌' },
  { key: 'vuln', patterns: ['风险点', '风险', '漏洞', '漏洞信息', 'Risks', 'Vulnerabilities', '安全风险', '脆弱性'], label: '⚠️ 漏洞风险', icon: '⚠️' },
  { key: 'next', patterns: ['下一步计划', '下一步', '建议', 'Next Steps', 'Recommendations', '后续', '后续操作', '建议操作'], label: '➡️ 下一步计划', icon: '➡️' },
  { key: 'info', patterns: ['基本信息', '信息收集', 'Information', '系统信息', '目标信息'], label: 'ℹ️ 信息', icon: 'ℹ️' },
]

/** 解析 AI 回复是否为结构化格式 */
/** 渲染 Markdown 到 HTML（支持表格/代码块/列表/标题/加粗等） */
function renderMarkdown(text) {
  if (!text) return ''
  // 1. 转义 HTML 特殊字符
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // 2. 代码块（必须优先处理，避免内部内容被后续规则误伤）
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const langClass = lang ? ` class="lang-${lang}"` : ''
    return `<pre><code${langClass}>${code.trim()}</code></pre>`
  })

  // 3. 表格
  html = html.replace(/^\|(.+)\|\s*$/gm, (line) => {
    // 跳过表头分隔行 (|---|)
    if (/^\|[\s:-]+\|$/.test(line.trim())) return line
    const cells = line.split('|').filter(c => c.trim()).map(c => c.trim())
    return '| ' + cells.join(' | ') + ' |'
  })
  // 将表格行转为 HTML
  html = html.replace(/(\|[^\n]+\|\s*\n)+/g, (tableBlock) => {
    const rows = tableBlock.trim().split('\n').filter(r => r.trim())
    let inHeader = true
    let result = '<table>'
    for (const row of rows) {
      const cells = row.split('|').filter(c => c.trim()).map(c => c.trim())
      // 表头分隔行
      if (/^[\s:-]+$/.test(cells.join(''))) { inHeader = false; continue }
      result += '<tr>'
      for (const cell of cells) {
        result += inHeader ? `<th>${cell}</th>` : `<td>${cell}</td>`
      }
      result += '</tr>'
    }
    result += '</table>'
    return result
  })

  // 4. 标题（### → h3, ## → h2, # → h1）
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>')

  // 5. 水平分割线
  html = html.replace(/^---+$/gm, '<hr>')

  // 6. 列表（- / * 无序列表）
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')

  // 7. 数字列表
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
  html = html.replace(/(?:<ul>)?(<li>.*<\/li>\n?)+(?:<\/ul>)?/g, (m) => {
    if (m.startsWith('<ul>')) return m
    return '<ol>' + m + '</ol>'
  })

  // 8. 加粗 + 斜体
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // 9. 行内代码
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')

  // 10. 换行
  html = html.replace(/\n/g, '<br>')

  return html
}

function parseAiSections(content) {
  if (!content) return null
  
  const lines = content.split('\n')
  const sections = []
  let currentSection = null
  
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      if (currentSection) currentSection.lines.push('')
      continue
    }
    
    // 检查是否为章节标题
    let matched = false
    for (const kw of AI_SECTION_KEYWORDS) {
      for (const pattern of kw.patterns) {
        if (trimmed.startsWith(pattern) || trimmed.startsWith(`**${pattern}`) || trimmed.startsWith(`# ${pattern}`)) {
          if (currentSection && currentSection.lines.length > 0) {
            sections.push(currentSection)
          }
          currentSection = { key: kw.key, label: kw.icon + ' ' + kw.label, lines: [] }
          matched = true
          break
        }
      }
      if (matched) break
    }
    if (!matched) {
      if (currentSection) {
        currentSection.lines.push(trimmed)
      } else {
        // 没有匹配任何section，判为普通文本
        return null
      }
    }
  }
  
  if (currentSection && currentSection.lines.length > 0) {
    sections.push(currentSection)
  }
  
  return sections.length >= 2 ? sections : null
}

/** 把 chatMessages 转为 /api/llm/chat 需要的格式 */
function buildMessagesPayload() {
  const payload = []
  for (const msg of chatMessages.value) {
    if (msg.role === 'assistant') {
      // ★ 跳过不完整的 assistant 消息（既没有 content 也没有 tool_calls）
      const hasContent = msg.content && msg.content.trim()
      const hasToolCalls = msg.tool_calls && msg.tool_calls.length > 0
      if (!hasContent && !hasToolCalls) {
        continue  // 残消息，跳过
      }
      const m = { role: 'assistant' }
      if (hasContent) {
        m.content = msg.content
      } else {
        m.content = null
      }
      // ★ 只有同时有 tool_result 才发 tool_calls，否则缺 tool 响应会导致 400
      if (hasToolCalls && msg.tool_result) {
        m.tool_calls = msg.tool_calls.map(tc => {
          const fn = tc.function || {}
          let args = fn.arguments
          // arguments 必须是字符串（JSON 格式），如果是对象则序列化
          if (typeof args === 'object' && args !== null) {
            args = JSON.stringify(args)
          }
          // 兼容：有些来源会把参数存在 arguments_json
          if ((!args || args === '{}') && fn.arguments_json) {
            args = typeof fn.arguments_json === 'string'
              ? fn.arguments_json
              : JSON.stringify(fn.arguments_json)
          }
          return {
            id: tc.id || '',
            type: tc.type || 'function',
            function: {
              name: fn.name || '',
              arguments: args || '{}'
            }
          }
        })
      }
      payload.push(m)
    }
    if (msg.tool_result) {
      // ★ 工具结果：tool_calls 有几个就生成几条 tool 响应，每条匹配各自 tool_call_id
      const toolCalls = msg.tool_calls || []
      const tr = typeof msg.tool_result === 'string'
        ? msg.tool_result
        : JSON.stringify(msg.tool_result, null, 2)
      const truncatedContent = tr.substring(0, 1500)
      for (const tc of toolCalls) {
        payload.push({
          role: 'tool',
          tool_call_id: tc.id || '',
          content: truncatedContent,
        })
      }
      // 即使 tool_calls 为空也保底创建一条
      if (toolCalls.length === 0) {
        const callId = msg.tool_calls?.[0]?.id || ''
        const fnName = msg.tool_calls?.[0]?.function?.name || 'execute_kali_command'
        payload.push({
          role: 'tool',
          tool_call_id: callId,
          name: fnName,
          content: truncatedContent,
        })
      }
    }
    if (msg.role === 'user') {
      payload.push({ role: 'user', content: msg.content })
    }
  }
  // ★ 确保最后一条消息不是 assistant（某些 provider 如 OpenRouter 要求末尾是 user/tool）
  if (payload.length > 0 && payload[payload.length - 1].role === 'assistant') {
    payload.push({ role: 'user', content: '请继续分析。' })
  }
  return payload
}

const terminalCommandLines = computed(() => terminalLines.value.filter(l => l.type === 'cmd'))
const terminalOutputLines = computed(() => terminalLines.value.filter(l => l.type !== 'cmd'))

// ============ 生命周期 ============
const logContainerRef = ref(null)

// WebSocket 连接状态监控
function setupWsMonitor() {
  const ws = getGlobalWebSocket()
  if (!ws || !ws.ws) return
  // 监听原生 WebSocket 状态
  const nativeWs = ws.ws
  wsConnected.value = nativeWs.readyState === 1
  nativeWs.onopen = () => {
    wsConnected.value = true
    wsReconnecting.value = false
    wsReconnectAttempts = 0
  }
  nativeWs.onclose = () => {
    wsConnected.value = false
    wsReconnecting.value = true
    ElMessage.warning('WebSocket 连接已断开，正在尝试重连...')
  }
  nativeWs.onerror = () => {
    wsConnected.value = false
  }
}

onMounted(() => {
  console.log('[SessionDetail] onMounted 触发, sessionId:', sessionId)

  // ★ 调试：捕获所有 WebSocket 消息
  const _debugHandler = registerMessageHandler('ALL_MESSAGES_DEBUG', (data) => {})
  // 改为监听所有消息
  const _ws = getGlobalWebSocket()
  if (_ws && _ws.messages) {
    console.log('[SessionDebug] WS 已连接，历史消息数:', _ws.messages.value.length)
  }

  fetchSession()
  loadTerminalHistory()
  loadChatHistory()
  loadLogs()  // ★ 恢复实时日志
  // ★ 仅在从 localStorage 恢复日志后，才合并后端历史日志（防止新会话乱入旧日志）
  if (logs.value.length > 0) {
    refreshLogs().then(() => console.log('[SessionDebug] refreshLogs 完成, logs数:', logs.value.length)).catch(e => console.error('[SessionDebug] refreshLogs 失败:', e))
  }
  scrollChatToBottom()
  // ★ 加载自定义拖拽高度（替代原生 CSS resize）
  loadBoxHeight()
  // 监控 WebSocket 连接状态
  setupWsMonitor()
  // ★ 新增：注册状态查询响应处理器
  setupStatusHandler()
  // ★ 新增：注册 AutoPilot 进展推送处理器（autopilot_progress）
  unregisterAutoPilotHandler = registerMessageHandler('autopilot_log', (data) => {
    console.log('[AutoPilot] 进展:', data)
    // 追加到实时日志
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: data.source || 'AutoPilot',
      level: data.level || 'info',
      category: data.category || 'system',
      message: data.message || '',
      detail: data.detail || null,
      phase: data.phase || '',
      step: data.step || 0,
    })
    // 限制日志长度
    if (logs.value.length > 600) {
      logs.value = logs.value.slice(-400)
    }
    // 检测到中止消息时，自动刷新会话状态
    if (data.message?.includes('被取消') || data.message?.includes('aborted') || data.state === 'ABORTED') {
      fetchSession()
    }
    // 更新阶段进度（兼容旧字段）
    if (data.phase) {
      autoPilotPhase.value = data.phase
    }
    if (data.state) {
      autoPilotState.value = data.state
    }
    // ★ AI 状态/计划/想法推送到对话窗口（短消息，辅助用户了解进度）
    if (data.category === 'ai_thought' || data.category === 'ai_plan') {
      chatMessages.value.push({
        role: 'assistant',
        content: data.message || '',
        timestamp: (data.timestamp || Date.now()),
      })
      saveChatHistory()
      scrollChatToBottom()
    }
    // 通知：finding/success/error 弹窗
    if (data.category === 'finding' && data.level === 'warning') {
      ElMessage.warning({ message: `⚠️ 发现：${data.message}`, duration: 5000 })
    }
    if (data.category === 'success') {
      ElMessage.success({ message: `✅ ${data.message}`, duration: 5000 })
    }
    if (data.category === 'error') {
      ElMessage.error({ message: `❌ ${data.message}`, duration: 8000 })
    }
  })

  // ★ 新增：注册 AutoPilot 状态变更处理器（autopilot_state_change，更新进度条）
  unregisterStateChangeHandler = registerMessageHandler('autopilot_state_change', (data) => {
    console.log('[AutoPilot] 状态变更:', data)
    autoPilotPhase.value = data.phase || autoPilotPhase.value
    autoPilotPhaseName.value = data.phase_name || ''
    autoPilotStep.value = data.step || 0
    autoPilotProgress.value = data.progress || 0
    autoPilotState.value = data.state || autoPilotState.value
    autoPilotDescription.value = data.description || ''
  })
  // ★ 新增：注册 AutoPilot 命令确认请求处理器
  unregisterCmdConfirmHandler = registerMessageHandler('autopilot_command_confirm_request', (data) => {
    console.log('[AutoPilot] 收到命令确认请求:', data)
    // 重置对话框状态
    showModifyInput.value = false
    skipInputVisible.value = false
    skipUserInput.value = ''
    commandModifyInput.value = ''
    // 设置命令数据
    commandDialogData.value = {
      command: data.command || '',
      reason: data.reason || 'AutoPilot 自动执行',
      target: data.target || '',
      state: data.state || '',
      session_id: data.session_id || sessionId.value,
    }
    commandDialogVisible.value = true
  })
  // ★ 新：AI准备执行命令 → 立即显示到日志
  unregisterCmdPendingHandler = registerMessageHandler('autopilot_command_pending', (data) => {
    console.log('[AutoPilot] 命令待执行:', data)
    pendingCommand.value = data.command || ''
    autoPilotStep.value += 1  // ★ 步数+1
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'AutoPilot',
      level: 'info',
      message: `⏳ AI 准备执行: ${data.command}`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：用户修改了命令
  unregisterCmdModifiedHandler = registerMessageHandler('autopilot_command_modified', (data) => {
    console.log('[AutoPilot] 命令已修改:', data)
    pendingCommand.value = data.modified_command || ''
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'AutoPilot',
      level: 'info',
      message: `✏️ 用户修改为: ${data.modified_command}`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：用户跳过了命令
  unregisterCmdSkippedHandler = registerMessageHandler('autopilot_command_skipped', (data) => {
    console.log('[AutoPilot] 命令已跳过:', data)
    pendingCommand.value = ''
    let skipMsg = `⏭️ 用户跳过: ${data.command}`
    if (data.user_input) {
      skipMsg += ` (用户指示: ${data.user_input})`
    }
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'AutoPilot',
      level: 'info',
      message: skipMsg,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新增：注册 AutoPilot 确认请求处理器
  unregisterConfirmHandler = registerMessageHandler('autopilot_confirm_request', (data) => {
    console.log('[AutoPilot] 收到确认请求:', data)
    // 用响应式变量触发对话框（Vue 官方推荐方式，比 ElMessageBox 可靠）
    confirmDialogMessage.value = data.message || `AutoPilot 即将执行【${data.phase || '下一'}】阶段，是否继续？`
    confirmDialogSessionId.value = data.session_id || sessionId.value
    confirmDialogVisible.value = true
  })
  // ★ 新增：AutoPilot AI 分析结果 → 推送到 AI 对话窗口
  unregisterAiResponseHandler = registerMessageHandler('autopilot_ai_response', (data) => {
    console.log('[AutoPilot] AI 分析结果:', data)
    if (data.content) {
      chatMessages.value.push({
        role: 'assistant',
        content: data.content,
        timestamp: data.timestamp || Date.now(),
      })
      saveChatHistory()
      scrollChatToBottom()
    }
  })
  // ★ 新增：AutoPilot 命令执行结果 → 推送到实时日志区域
  unregisterExecResultHandler = registerMessageHandler('autopilot_exec_result', (data) => {
    console.log('[AutoPilot] 命令执行结果:', data)
    if (data.command) {
      const ts = data.timestamp || Date.now()
      // 执行命令
      logs.value.push({
        timestamp: ts,
        source: 'AutoPilot',
        level: 'info',
        message: `🤖 AI正在执行: ${data.command}`,
      })
      // 执行结果
      if (data.result) {
        const lines = data.result.split('\n')
        for (const line of lines) {
          if (line.trim()) {
            logs.value.push({
              timestamp: ts,
              source: 'Tool',
              level: 'info',
              message: line,
            })
          }
        }
      }
      if (logs.value.length > 600) {
        logs.value = logs.value.slice(-300)
      }
    }
  })
  // ★ 新：手动工具执行开始 → 追加到日志
  unregisterToolStartHandler = registerMessageHandler('tool_exec_started', (data) => {
    console.log('[工具执行] 开始:', data)
    toolCount.value++
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'Terminal',
      level: 'info',
      message: `🔧 执行: ${data.command || ''}`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：手动工具被拦截 → 追加到日志
  unregisterToolBlockedHandler = registerMessageHandler('tool_exec_blocked', (data) => {
    console.log('[工具执行] 被拦截:', data)
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'Security',
      level: 'ERROR',
      message: `🚫 命令被安全拦截: ${data.command || ''} (${data.reason || '无原因'})`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：手动工具执行结果 → 追加到日志
  unregisterToolResultHandler = registerMessageHandler('tool_exec_result', (data) => {
    console.log('[工具执行] 结果:', data)
    toolCount.value++
    const ts = data.timestamp || Date.now()
    logs.value.push({
      timestamp: ts,
      source: 'Terminal',
      level: data.success ? 'info' : 'ERROR',
      message: data.success
        ? `✅ 执行成功 (${(data.duration || 0).toFixed(1)}s)\n${(data.output || '').slice(0, 500)}`
        : `❌ 执行失败: ${data.error || ''}`,
    })
    if (logs.value.length > 600) logs.value = logs.value.slice(-300)
  })
  // ★ 新：AutoPilot 通知（失败、超时、交互式工具等）
  unregisterNotificationHandler = registerMessageHandler('autopilot_notification', (data) => {
    console.log('[AutoPilot] 通知:', data)
    const level = data.level || 'info'
    const typeMap = { error: 'error', warning: 'warning', info: 'success' }
    // 同时弹通知 + 写日志
    ElMessage({
      type: typeMap[level] || 'info',
      title: data.title || 'AutoPilot 通知',
      message: data.message || '',
      duration: 8000,
    })
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'AutoPilot',
      level: level === 'error' ? 'ERROR' : 'info',
      message: `${level === 'error' ? '❌' : level === 'warning' ? '⚠️' : 'ℹ️'} ${data.title}: ${data.message}`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：手动命令插入确认
  unregisterManualCmdHandler = registerMessageHandler('autopilot_manual_command', (data) => {
    console.log('[AutoPilot] 手动命令已入队:', data)
    logs.value.push({
      timestamp: (data.timestamp || Date.now()),
      source: 'AutoPilot',
      level: 'info',
      message: `💬 手动命令已入队: ${data.command || ''}`,
    })
    if (logs.value.length > 500) logs.value = logs.value.slice(-300)
  })
  // ★ 新：工具选择请求
  unregisterToolChoiceHandler = registerMessageHandler('autopilot_tool_choice', (data) => {
    console.log('[AutoPilot] 工具选择:', data)
    ElMessageBox.confirm(
      data.reason || `AI 选择了 ${data.chosen_tool}，${data.suggested_tool} 更快，是否更换？`,
      '工具选择',
      {
        confirmButtonText: `用 ${data.suggested_tool}`,
        cancelButtonText: `保持 ${data.chosen_tool}`,
        type: 'info',
      }
    ).then(() => {
      const ws = getGlobalWebSocket()
      if (ws && ws.send) ws.send({ type: 'autopilot_tool_choice_response', session_id: data.session_id || sessionId.value, use_alternative: true })
    }).catch(() => {
      const ws = getGlobalWebSocket()
      if (ws && ws.send) ws.send({ type: 'autopilot_tool_choice_response', session_id: data.session_id || sessionId.value, use_alternative: false })
    })
  })
  // ★ 报告已生成 → 自动打开新标签
  registerMessageHandler('report_generated', (data) => {
    console.log('[报告] 已生成:', data)
    // 从消息中提取报告路径
    const msg = data.message || data.detail || ''
    const htmlMatch = msg.match(/report_[a-z0-9]+\.html/)
    if (htmlMatch) {
      const reportName = htmlMatch[0]
      setTimeout(() => {
        window.open(`/api/reports/download/${reportName}`, '_blank')
      }, 1000)
    }
  })
})

onUnmounted(() => {
  // ★ 离开时保存聊天记录和日志，确保回来时有数据
  saveChatHistory()
  saveLogs()
  
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  // ★ 新增：取消注册 AutoPilot 进展消息处理器
  if (unregisterAutoPilotHandler) {
    unregisterAutoPilotHandler()
    unregisterAutoPilotHandler = null
  }
  // ★ 新增：取消注册确认请求处理器
  if (unregisterConfirmHandler) {
    unregisterConfirmHandler()
    unregisterConfirmHandler = null
  }
  // ★ 新增：取消注册 AI 分析结果处理器
  if (unregisterAiResponseHandler) {
    unregisterAiResponseHandler()
    unregisterAiResponseHandler = null
  }
  // ★ 新增：取消注册命令执行结果处理器
  if (unregisterExecResultHandler) {
    unregisterExecResultHandler()
    unregisterExecResultHandler = null
  }
  // ★ 新增：取消注册命令确认处理器
  if (unregisterCmdConfirmHandler) {
    unregisterCmdConfirmHandler()
    unregisterCmdConfirmHandler = null
  }
  if (unregisterCmdPendingHandler) {
    unregisterCmdPendingHandler()
    unregisterCmdPendingHandler = null
  }
  if (unregisterCmdModifiedHandler) {
    unregisterCmdModifiedHandler()
    unregisterCmdModifiedHandler = null
  }
  if (unregisterCmdSkippedHandler) {
    unregisterCmdSkippedHandler()
    unregisterCmdSkippedHandler = null
  }
  // ★ 已移除 ResizeObserver 清理，改用自定义拖拽
  // ★ 新增：取消注册工具执行消息处理器
  if (unregisterToolStartHandler) {
    unregisterToolStartHandler()
    unregisterToolStartHandler = null
  }
  if (unregisterToolBlockedHandler) {
    unregisterToolBlockedHandler()
    unregisterToolBlockedHandler = null
  }
  if (unregisterToolResultHandler) {
    unregisterToolResultHandler()
    unregisterToolResultHandler = null
  }
  if (unregisterNotificationHandler) {
    unregisterNotificationHandler()
    unregisterNotificationHandler = null
  }
  // ★ 新增：取消注册状态查询响应处理器
  if (unregisterStatusHandler) {
    unregisterStatusHandler()
    unregisterStatusHandler = null
  }
  // ★ 新增：取消注册 AutoPilot 状态变更处理器
  if (unregisterStateChangeHandler) {
    unregisterStateChangeHandler()
    unregisterStateChangeHandler = null
  }
  if (unregisterManualCmdHandler) {
    unregisterManualCmdHandler()
    unregisterManualCmdHandler = null
  }
  if (unregisterToolChoiceHandler) {
    unregisterToolChoiceHandler()
    unregisterToolChoiceHandler = null
  }
})

function isDebugLog(msg) {
  // 过滤掉调试日志行
  const ignorePatterns = [
    'ai_output:',
    'audit:',
    '[LLM][请求体]',
    '[LLM][',
    'tool_call:',
    'role_switch:',
    'command_blocked:',
    'httpx:',
    'httpcore:',
    'openai.',
    'Connection pool',
    'Retrying',
    'Request to',
    'urllib3',
  ]
  const lower = (msg || '').toLowerCase()
  for (const p of ignorePatterns) {
    if (lower.includes(p.toLowerCase())) return true
  }
  return false
}

async function refreshLogs() {
  try {
    const res = await request.get(`/api/system/logs?lines=100&log_type=all`)
    const data = (res && res.data !== undefined) ? res.data : res
    if (data?.logs) {
      // ★ 合并新日志到已有日志（不清除已有记录），按时间排序去重
      const newLogs = data.logs.filter(l => !isDebugLog(l.message || ''))
      const existingIds = new Set(logs.value.map(l => l.timestamp + '_' + l.source + '_' + l.message))
      const merged = [...logs.value]
      for (const nl of newLogs) {
        // 只合并有 sessionId 匹配的日志，或 source 不是 AutoPilot 的系统日志
        const k = nl.timestamp + '_' + (nl.source || '') + '_' + nl.message
        if (!existingIds.has(k)) {
          merged.push(nl)
          existingIds.add(k)
        }
      }
      // 按时间排序
      merged.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))
      logs.value = merged.slice(-600)
    }
  } catch { /* ignore */ }
}

// ============ 工具失败决策 ============
function executeUserDecision(step) {
  if (!step) {
    pendingDecision.value = null
    return
  }

  pendingDecision.value = null   // 先关弹窗

  // ★ 去终端执行：提示用户在终端手动执行，等待结果
  if (step.action === 'manual_execution') {
    addTerminalLine(`[用户选择] 手动执行: ${step.command}`, 'cmd')
    chatMessages.value.push({
      role: 'user',
      content: `请在终端执行以下命令，执行后把结果粘贴回来继续：\n\`\`\`bash\n${step.command}\n\`\`\``,
    })
    saveChatHistory()
    scrollChatToBottom()
    ElMessage.success('命令已展示，请在 Kali 终端执行后粘贴结果')
    // 不调用 autoChatLoop，等用户粘贴结果后自动继续
    return
  }

  // ★ 跳过：告诉 AI 命令被跳过了，让它继续下一步
  if (step.action === 'skip') {
    addTerminalLine('[用户选择] 跳过此步骤', 'info')
    // 插入系统消息让 AI 知道命令被跳过，避免重复重试
    chatMessages.value.push({
      role: 'user',
      content: '[系统消息: 用户跳过了该工具执行结果，请基于当前已知信息继续下一步。不要重复执行已被跳过的命令。]',
    })
    saveChatHistory()
    autoChatLoop()
    return
  }

  // ★ 强制执行（绕过安全拦截）
  if (step.action === 'force_execute') {
    addTerminalLine(`[用户选择] 强制执行: ${step.command}`, 'cmd')
    const lastMsg = chatMessages.value[chatMessages.value.length - 1]
    request.post('/api/tools/execute', {
      name: 'execute_kali_command',
      arguments: { command: step.command, user_confirmed: true }
    }, { timeout: 120000 }).then(res => {
      if (res?.output) addTerminalLine(String(res.output), 'output')
      if (res?.error) addTerminalLine(`❌ ${String(res.error)}`, 'error')
      if (lastMsg) {
        lastMsg.tool_result = ((res?.output || '') + (res?.error || '')).substring(0, 1500)
      }
      saveChatHistory()
      autoChatLoop()
    }).catch(e => {
      addTerminalLine(`❌ 执行失败: ${e.message || e}`, 'error')
    })
    return
  }

  // ★ 修改命令
  if (step.action === 'modify') {
    addTerminalLine(`[用户选择] 修改命令: ${step.command}`, 'cmd')
    // 用 ElMessageBox.prompt 让用户输入修改后的命令
    ElMessageBox.prompt('请修改命令', '修改命令', {
      inputValue: step.command,
      inputType: 'textarea',
    }).then(({ value }) => {
      if (!value || !value.trim()) return
      addTerminalLine(`修改为: ${value}`, 'cmd')
      const lastMsg = chatMessages.value[chatMessages.value.length - 1]
      request.post('/api/tools/execute', {
        name: 'execute_kali_command',
        arguments: { command: value.trim() }
      }, { timeout: 120000 }).then(res => {
        if (res?.output) addTerminalLine(String(res.output), 'output')
        if (res?.error) addTerminalLine(`❌ ${String(res.error)}`, 'error')
        if (lastMsg) {
          lastMsg.tool_result = ((res?.output || '') + (res?.error || '')).substring(0, 1500)
        }
        saveChatHistory()
        autoChatLoop()
      }).catch(e => {
        addTerminalLine(`❌ 执行失败: ${e.message || e}`, 'error')
      })
    }).catch(() => { /* 取消 */ })
    return
  }

  // ★ 没有命令的其它步骤：提示
  if (!step.command) {
    ElMessage.warning(`"${step.label}" 没有关联命令，无法执行`)
    return
  }

  // ★ 正常执行
  addTerminalLine(`[用户选择] ${step.label}: ${step.command}`, 'cmd')
  // 复用 executeTool 逻辑
  const lastMsg = chatMessages.value[chatMessages.value.length - 1]
  let execResult
  request.post('/api/tools/execute',
    { name: 'execute_kali_command', arguments: { command: step.command } },
    { timeout: 120000 },
  ).then(res => {
    execResult = res
    if (execResult?.output) addTerminalLine(String(execResult.output), 'output')
    if (execResult?.error) addTerminalLine(`❌ ${String(execResult.error)}`, 'error')
    if (lastMsg) {
      const out = execResult?.output ? String(execResult.output) : ''
      const err = execResult?.error ? String(execResult.error) : ''
      lastMsg.tool_result = (out + err).substring(0, 1500)
    }
    saveChatHistory()
    // 继续执行自动循环
    autoChatLoop()
  }).catch(e => {
    addTerminalLine(`❌ 执行失败: ${e.message || e}`, 'error')
  })
}

// 监听日志自动滚动（用户手动上滚时不打断）
watch(logs.value, () => {
  saveLogs()  // ★ 自动保存日志到 localStorage
  nextTick(() => {
    if (logContainerRef.value) {
      const el = logContainerRef.value
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom > 30) return
      el.scrollTop = el.scrollHeight
    }
  })
})

// 监听终端输出，同步滚动日志区（用户手动上滚时不打断）
watch(terminalLines, () => {
  nextTick(() => {
    if (logContainerRef.value) {
      const el = logContainerRef.value
      // column 布局：scrollTop = scrollHeight 显示最新消息（在底部）
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom > 30) return  // 用户在查看历史，不打断
      el.scrollTop = el.scrollHeight
    }
  })
}, { deep: true })
</script>

<style scoped>
.session-detail {
  /* ★ 删除原生 resize，改用自定义拖拽条，免去滚动冲突 */
  overflow: auto !important;
  min-height: 400px;
  max-height: calc(100vh * 5);
  flex: none !important;
  /* ★ 修复：标准 Vue CSS v-bind 语法 — boxHeight 是数字，拼接 "px" 单位 */
  height: v-bind('boxHeight + "px"') !important;
  box-sizing: border-box;
  position: relative;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  color: #c9d1d9;
}

/* ★ 新增：底部蓝色半透明拖拽条 — 替代原生 CSS resize */
.box-resize-bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 12px;
  cursor: ns-resize;
  background: rgba(64, 158, 255, 0.4);
  z-index: 50;
  flex-shrink: 0;
  border-top: 2px solid rgba(64, 158, 255, 0.8);
}
.box-resize-bar:hover {
  background: rgba(64, 158, 255, 0.8);
}

/* ★ 已删除原生 ::-webkit-resizer — 不再依赖浏览器 resize 手柄 */
.session-detail::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.session-detail::-webkit-scrollbar-thumb {
  background: #30363d;
  border-radius: 3px;
}
.session-detail::-webkit-scrollbar-track {
  background: transparent;
}

/* 顶部栏 */
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}
.ws-status {
  margin-right: auto;
}
.page-title {
  margin: 0;
  font-size: 16px;
  flex: 1;
}
.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* 双栏容器 */
.dual-panel {
  flex: 1;
  display: flex;
  min-height: 0;
  position: relative;
}

/* 左右面板 - 继承外层高度，不抢占滚动区域 */
.left-panel,
.right-panel {
  overflow: auto;
  display: flex;
  flex-direction: column;
  max-height: 100%;
}
.left-panel {
  border-right: 1px solid #30363d;
  min-width: 300px;
}
.right-panel {
  flex: 1;
  min-width: 300px;
}

/* 拖拽手柄 */
.resize-handle {
  width: 4px;
  cursor: col-resize;
  background: #21262d;
  position: absolute;
  left: calc(v-bind(leftWidth) - 2px);
  top: 0;
  bottom: 0;
  z-index: 10;
  transition: background 0.15s;
}
.resize-handle:hover {
  background: #58a6ff;
}

/* 面板头部 */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  font-size: 13px;
  flex-shrink: 0;
  color: #8b949e;
}
.panel-header .el-icon {
  margin-right: 4px;
}

/* 面板间分割线 + 垂直拖拽手柄 */
.panel-divider {
  height: 2px;
  background: #30363d;
  flex-shrink: 0;
}
.resize-handle-vert {
  height: 6px;
  margin: -2px 0;
  background: #21262d;
  cursor: row-resize;
  transition: background 0.15s;
  z-index: 5;
  position: relative;
}
.resize-handle-vert:hover {
  background: #58a6ff;
}

/* ===== 日志区域 ===== */
.log-container {
  flex: 1;
  overflow-y: auto;
  background: #161b22;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.6;
  display: flex;
  flex-direction: column;
}
.log-item {
  padding: 6px 8px;
  margin-bottom: 4px;
  border-radius: 4px;
  border-left: 3px solid transparent;
  word-wrap: break-word;
  word-break: break-all;
}
.log-item.INFO { 
  color: #58a6ff; 
  border-left-color: #58a6ff;
  background: rgba(88, 166, 255, 0.05);
}
.log-item.ERROR { 
  color: #f85149; 
  border-left-color: #f85149;
  background: rgba(248, 81, 73, 0.05);
}
.log-item.WARNING { 
  color: #d29922; 
  border-left-color: #d29922;
  background: rgba(210, 153, 34, 0.05);
}
.log-item.DEBUG { 
  color: #8b949e; 
  border-left-color: #484f58;
  background: rgba(72, 79, 88, 0.05);
}
.log-time { 
  color: #484f58; 
  margin-right: 8px; 
  font-size: 12px;
  flex-shrink: 0;
}
.log-source { 
  color: #3fb950; 
  margin-right: 8px; 
  font-weight: 500;
  flex-shrink: 0;
}
.log-message {
  color: #c9d1d9;
  white-space: pre-wrap;
  word-wrap: break-word;
  word-break: break-all;
  display: block;
  margin-top: 4px;
}
.log-empty {
  text-align: center;
  color: #484f58;
  padding: 40px 0;
}

/* ===== 终端区域 ===== */
.terminal-output {
  flex: 1;
  overflow-y: auto;
  background: #0d1117;
  padding: 8px;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  font-size: 13px;
}
.term-line {
  padding: 1px 0;
  line-height: 1.5;
}
.term-line.cmd .term-prompt {
  color: #3fb950;
}
.term-line.output .term-output {
  color: #c9d1d9;
  white-space: pre-wrap;
}
.term-prompt { color: #3fb950; }
.term-output { color: #c9d1d9; white-space: pre-wrap; }
.term-input-line {
  display: flex;
  align-items: center;
  gap: 0;
}
.term-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: #c9d1d9;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  font-size: 13px;
  caret-color: #58a6ff;
}

/* ===== AI 对话区域 ===== */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: #0d1117;
  display: flex;
  flex-direction: column;
}
.chat-msg {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 100%;
}
/* AI 在左 */
.chat-msg.assistant {
  justify-content: flex-start;
  flex-direction: row;
}
.chat-msg.assistant .msg-avatar { order: 1; }
.chat-msg.assistant .msg-content { order: 2; }
.chat-msg.assistant .msg-bubble {
  background: #21262d;
  border: 1px solid #30363d;
  text-align: left;
}
/* 用户在右，头像在右边 */
.chat-msg.user {
  justify-content: flex-end;
  flex-direction: row;
}
.chat-msg.user .msg-content { order: 1; }
.chat-msg.user .msg-avatar { order: 2; }
.chat-msg.user .msg-bubble {
  background: #1f6feb;
  border: 1px solid #1f6feb;
  text-align: left;
}
.msg-avatar { flex-shrink: 0; }
.msg-content { max-width: 80%; }
.msg-name { font-size: 12px; color: #8b949e; margin-bottom: 4px; }
.msg-bubble {
  padding: 10px 14px;
  border-radius: 8px;
  background: #21262d;
  border: 1px solid #30363d;
  font-size: 14px;
  line-height: 1.6;
  word-wrap: break-word;
}
.msg-text { white-space: pre-wrap; }
/* Markdown 渲染样式 */
.msg-text h1, .msg-text h2, .msg-text h3 {
  margin: 12px 0 6px;
  color: #e6edf3;
  font-weight: 600;
}
.msg-text h1 { font-size: 18px; }
.msg-text h2 { font-size: 16px; }
.msg-text h3 { font-size: 14px; }
.msg-text table {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
  width: 100%;
}
.msg-text th, .msg-text td {
  border: 1px solid #30363d;
  padding: 6px 10px;
  text-align: left;
}
.msg-text th {
  background: #161b22;
  color: #c9d1d9;
  font-weight: 600;
}
.msg-text td { color: #c9d1d9; }
.msg-text pre {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  margin: 8px 0;
}
.msg-text code {
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  font-size: 13px;
}
.msg-text pre code {
  background: none;
  padding: 0;
  color: #e6edf3;
}
.msg-text :not(pre) > code {
  background: #1c2128;
  color: #f0e6d2;
  padding: 2px 6px;
  border-radius: 4px;
}
.msg-text ul, .msg-text ol {
  margin: 6px 0;
  padding-left: 24px;
}
.msg-text li { margin: 4px 0; }
.msg-text hr {
  border: none;
  border-top: 1px solid #30363d;
  margin: 12px 0;
}
.msg-text strong { color: #e6edf3; }
.msg-text em { color: #8b949e; }
.msg-tool-calls { margin-top: 8px; }
.tool-call-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.tool-call-args {
  font-size: 11px;
  color: #8b949e;
  word-break: break-all;
}
.msg-tool-result { margin-top: 8px; }
.tool-result-header {
  font-size: 12px;
  color: #3fb950;
  margin-bottom: 4px;
}
.tool-result-output {
  background: rgba(63,185,80,0.08);
  border: 1px solid #3fb950;
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: #3fb950;
}
.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
.chat-status {
  display: flex;
  align-items: center;
  gap: 8px;
}
.loading-tag .el-icon.is-loading { margin-right: 4px; }

/* 输入框 */
.chat-input-area {
  padding: 12px;
  border-top: 1px solid #30363d;
  background: #161b22;
}
.chat-input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.char-count {
  font-size: 12px;
  color: #484f58;
}

/* 工具失败决策面板 */
.decision-overlay {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.65);
  border-top: 2px solid #d29922;
  padding: 12px 16px;
  backdrop-filter: blur(4px);
  pointer-events: auto;
}
.decision-panel {
  max-width: 600px;
  margin: 0 auto;
}
.decision-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: bold;
  color: #d29922;
  margin-bottom: 6px;
}
.decision-reason {
  font-size: 12px;
  color: #8b949e;
  margin-bottom: 10px;
  line-height: 1.5;
}
.decision-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.decision-step-btn {
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 8px 12px;
  cursor: pointer;
  transition: all 0.2s;
  flex: 1 1 200px;
  min-width: 140px;
}
.decision-step-btn:hover {
  border-color: #58a6ff;
  background: #1c2128;
}
.decision-step-btn.step-primary {
  border-color: #238636;
}
.decision-step-btn.step-primary:hover {
  background: #1a3a1d;
}
.decision-step-btn.step-skip {
  border-color: #484f58;
  opacity: 0.7;
}
.decision-step-btn.step-skip:hover {
  opacity: 1;
}
.step-label {
  display: block;
  font-size: 13px;
  color: #c9d1d9;
  margin-bottom: 4px;
}
.step-command {
  display: block;
  font-size: 11px;
  color: #58a6ff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.decision-manual .decision-header {
  color: #58a6ff;
}
.decision-manual-hint {
  font-size: 12px;
  color: #8b949e;
  margin-bottom: 10px;
  padding: 8px 12px;
  background: #161b22;
  border-radius: 6px;
  border: 1px solid #30363d;
  line-height: 1.6;
}
.decision-manual-hint ol {
  margin: 4px 0 0 16px;
  padding: 0;
}
.decision-manual-hint li {
  margin-bottom: 2px;
}

/* ===== 阶段进度条 ===== */
.phase-progress-bar {
  padding: 6px 12px;
  background: #1c2128;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 3px solid transparent;
  transition: border-color 0.3s ease, background 0.3s ease;
}
.phase-progress-bar.active {
  border-left-color: #58a6ff;
  background: linear-gradient(90deg, rgba(88,166,255,0.08), #1c2128);
}
.phase-progress-bar.paused {
  border-left-color: #e6a23c;
  background: linear-gradient(90deg, rgba(230,162,60,0.06), #1c2128);
}
.phase-progress-bar.completed {
  border-left-color: #3fb950;
  background: linear-gradient(90deg, rgba(63,185,80,0.06), #1c2128);
}
.phase-state {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #8b949e;
}
.phase-progress-bar.active .phase-state { color: #58a6ff; }
.phase-progress-bar.paused .phase-state { color: #e6a23c; }
.phase-progress-bar.completed .phase-state { color: #3fb950; }
.phase-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.phase-name {
  color: #d29922;
  font-weight: 600;
}
.phase-step {
  color: #58a6ff;
  font-size: 11px;
  margin-left: auto;
}
.phase-progress-track {
  height: 4px;
  background: #30363d;
  border-radius: 2px;
  overflow: hidden;
}
.phase-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #d29922, #58a6ff);
  border-radius: 2px;
  transition: width 0.3s ease;
}

/* ★ 工具计数样式与闪烁动画 */
.phase-tool-count {
  margin-left: auto;
  font-size: 12px;
  color: #39c5cf;
  display: flex;
  align-items: center;
  gap: 3px;
  background: rgba(57, 197, 207, 0.1);
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid rgba(57, 197, 207, 0.3);
  transition: all 0.3s ease;
}
.tool-count-flash {
  animation: toolCountPulse 0.4s ease;
}
@keyframes toolCountPulse {
  0% { transform: scale(1); }
  50% { transform: scale(1.15); background: rgba(57, 197, 207, 0.25); }
  100% { transform: scale(1); }
}
.phase-state {
  color: #8b949e;
  font-size: 11px;
}
.pending-command {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #c9d1d9;
}
.pending-label {
  color: #8b949e;
  flex-shrink: 0;
}
.pending-cmd {
  color: #58a6ff;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

/* ===== 日志分类折叠面板 ===== */
.log-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
  background: #161b22;
}
/* ===== 实时日志统一时间线 ===== */
.tl-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.5;
  border-bottom: 1px solid rgba(48,54,61,0.4);
  transition: background 0.1s;
}
.tl-item:hover { background: rgba(255,255,255,0.03); }
.tl-time {
  color: #484f58;
  font-size: 11px;
  flex-shrink: 0;
  min-width: 55px;
  padding-top: 1px;
}
.tl-tag {
  flex-shrink: 0;
  min-width: 36px;
  text-align: center;
  font-size: 11px;
}
.tl-message {
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
  flex: 1;
  min-width: 0;
}
.tl-truncated {
  max-height: 7.5em;
  overflow: hidden;
  position: relative;
}
.tl-truncated::after {
  content: '...';
  position: absolute;
  bottom: 0;
  right: 0;
  background: #161b22;
  padding: 0 4px;
  color: #484f58;
  font-size: 11px;
}
/* 日志类型颜色 */
.tl-item.type-output { border-left: 2px solid #3fb950; color: #3fb950; }
.tl-item.type-output .tl-message { color: #3fb950; }
.tl-item.type-command { border-left: 2px solid #58a6ff; }
.tl-item.type-error { border-left: 2px solid #f85149; }
.tl-item.type-ai { border-left: 2px solid #d29922; }
.tl-item.type-system { border-left: 2px solid #8b949e; }
.tl-item.type-blocked { border-left: 2px solid #f85149; background: rgba(248,81,73,0.06); }

/* ===== AI 结构化渲染 ===== */
.ai-section-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  margin-bottom: 8px;
  overflow: hidden;
}
.ai-section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  border-bottom: 1px solid #30363d;
}
.ai-section-header.phase { color: #58a6ff; background: rgba(88,166,255,0.08); }
.ai-section-header.found { color: #3fb950; background: rgba(63,185,80,0.08); }
.ai-section-header.risk { color: #f85149; background: rgba(248,81,73,0.08); }
.ai-section-header.next { color: #d29922; background: rgba(210,153,34,0.08); }
.ai-section-body {
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  color: #c9d1d9;
}
/* 普通 AI 文本保留原样式 */
.msg-streaming-cursor::after {
  content: '▊';
  animation: blink 1s step-end infinite;
  color: #58a6ff;
}
@keyframes blink {
  50% { opacity: 0; }
}
/* 结构化标签样式 */
.section-tag-phase { color: #58a6ff; }
.section-tag-found { color: #3fb950; }
.section-tag-risk { color: #f85149; }
.section-tag-next { color: #d29922; }
/* ★ 日志 category 着色 */
.cat-system     { color: #58a6ff; }
.cat-phase      { color: #58a6ff; font-weight: 600; }
.cat-tool-start { color: #39c5cf; }
.cat-tool-output { color: #8b949e; }
.cat-tool-end   { color: #3fb950; }
.cat-ai-thought { color: #bc8cff; font-style: italic; }
.cat-ai-analysis { color: #bc8cff; }
.cat-ai-plan    { color: #58a6ff; }
.cat-finding    { color: #d29922; font-weight: 600; }
.cat-error      { color: #f85149; font-weight: 600; }
.cat-warning    { color: #e3b341; }
.cat-success    { color: #3fb950; font-weight: 600; }
.cat-confirm    { color: #e3b341; font-weight: 600; }

/* 凭证展示 */
.credential-panel {
  margin: 20px;
  padding: 16px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
}
.credential-header {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
  font-size: 14px;
  color: #c9d1d9;
}
.credential-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.credential-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: #0d1117;
  border-radius: 6px;
  flex-wrap: wrap;
}
.credential-value {
  font-size: 13px;
  color: #f0e6d2;
  background: #1c2128;
  padding: 2px 8px;
  border-radius: 4px;
  word-break: break-all;
  max-width: 400px;
}
.credential-source {
  font-size: 11px;
  color: #8b949e;
  margin-left: auto;
}
</style>
