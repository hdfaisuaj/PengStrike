<template>
  <el-container class="main-container" :class="{ 'session-detail-mode': isSessionDetail }">
    <el-aside :width="isCollapse ? '64px' : '220px'" class="main-aside">
      <div class="logo-area">
        <img src="/vite.svg" alt="logo" class="logo" />
        <span v-show="!isCollapse" class="logo-text">PengStrike</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        :router="true"
        background-color="#1d1e1f"
        text-color="#bfcbd9"
        active-text-color="#409eff"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <template #title>仪表盘</template>
        </el-menu-item>
        <el-menu-item index="/sessions">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>会话列表</template>
        </el-menu-item>
        <el-menu-item index="/tools">
          <el-icon><Tools /></el-icon>
          <template #title>工具列表</template>
        </el-menu-item>
        <el-menu-item index="/roles">
          <el-icon><UserFilled /></el-icon>
          <template #title>角色管理</template>
        </el-menu-item>
        <el-menu-item index="/reports">
          <el-icon><Document /></el-icon>
          <template #title>报告管理</template>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>系统设置</template>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="main-header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-tag v-if="wsConnected" type="success" size="small" effect="dark">
            <el-icon style="margin-right: 4px"><Connection /></el-icon>已连接
          </el-tag>
          <el-tag v-else type="danger" size="small" effect="dark">
            <el-icon style="margin-right: 4px"><CircleClose /></el-icon>未连接
          </el-tag>
          <span class="user-dropdown">
            <el-avatar :size="32" icon="UserFilled" />
            <span class="username">{{ username }}</span>
          </span>
        </div>
      </el-header>
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  Odometer, ChatDotRound, Tools, UserFilled, Document, Setting,
  Fold, Expand, Connection, CircleClose
} from '@element-plus/icons-vue'
import { getUser } from '../utils/auth'
import { getGlobalWebSocket } from '../utils/websocket'

const route = useRoute()
const isCollapse = ref(false)
const { connected: wsConnected } = getGlobalWebSocket()

const user = getUser()
const username = ref(user?.username || '用户')

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/sessions')) return '/sessions'
  return path
})

const routeTitles = {
  '/dashboard': '仪表盘',
  '/sessions': '会话列表',
  '/tools': '工具列表',
  '/roles': '角色管理',
  '/reports': '报告管理',
  '/settings': '系统设置'
}

const currentTitle = computed(() => {
  const path = route.path
  for (const [prefix, title] of Object.entries(routeTitles)) {
    if (path.startsWith(prefix)) return title
  }
  if (path.startsWith('/sessions/')) return '会话详情'
  return ''
})

// 会话详情页需要突破 100vh 限制，动态切换样式
const isSessionDetail = computed(() => /^\/sessions\//.test(route.path))


</script>

<style scoped>
.main-container {
  height: 100vh;
}

.main-aside {
  background-color: #1d1e1f;
  overflow: hidden;
  transition: width 0.3s;
}

.logo-area {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.logo {
  width: 32px;
  height: 32px;
}

.logo-text {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}

.main-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
  height: 60px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: #606266;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-dropdown {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.username {
  font-size: 14px;
  color: #303133;
}

.main-content {
  background-color: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
}

/* 会话详情页：突破 100vh 限制 */
.session-detail-mode {
  height: auto;
  min-height: 100vh;
}
.session-detail-mode .main-header {
  position: sticky;
  top: 0;
  z-index: 100;
}
.session-detail-mode .main-content {
  overflow: visible;
  padding: 0;
  background: #0d1117;
}

.el-menu {
  border-right: none;
}
</style>