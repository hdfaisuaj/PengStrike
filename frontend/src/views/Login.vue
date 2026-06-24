<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-header">
        <div class="login-logo">
          <el-icon :size="48" color="#409eff"><Lock /></el-icon>
        </div>
        <h1 class="login-title">PengStrike</h1>
        <p class="login-subtitle">AI辅助渗透平台</p>
      </div>
      <p>正在进入系统...</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Lock } from '@element-plus/icons-vue'

const router = useRouter()
onMounted(() => {
  router.replace({ name: 'Dashboard' })
})
</script>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
}
.login-card {
  width: 420px;
  padding: 40px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  text-align: center;
}
.login-header {
  margin-bottom: 24px;
}
.login-logo {
  margin-bottom: 16px;
}
.login-title {
  font-size: 28px;
  font-weight: 700;
  color: #1d1e1f;
  margin: 0;
}
.login-subtitle {
  font-size: 14px;
  color: #909399;
  margin: 8px 0 0;
}
</style>
