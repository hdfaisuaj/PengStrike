<template>
  <el-card class="status-card" :body-style="{ padding: '20px' }" shadow="hover">
    <div class="card-content">
      <div class="card-left">
        <div class="card-icon" :style="{ backgroundColor: iconBg }">
          <el-icon :size="24" :color="iconColor">
            <component :is="icon" />
          </el-icon>
        </div>
      </div>
      <div class="card-right">
        <div class="card-title">{{ title }}</div>
        <div class="card-value">{{ value }}</div>
        <div v-if="description" class="card-desc">{{ description }}</div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
const props = defineProps({
  title: { type: String, required: true },
  value: { type: [String, Number], default: '' },
  icon: { type: [String, Object], default: 'InfoFilled' },
  iconBg: { type: String, default: '#ecf5ff' },
  iconColor: { type: String, default: '#409eff' },
  description: { type: String, default: '' }
})
</script>

<style scoped>
.status-card {
  border-radius: 8px;
}

.card-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.card-icon {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-right {
  flex: 1;
}

.card-title {
  font-size: 13px;
  color: #909399;
  margin-bottom: 4px;
}

.card-value {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}

.card-desc {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}
</style>