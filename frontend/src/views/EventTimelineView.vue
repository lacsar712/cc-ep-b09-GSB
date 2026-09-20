<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap">
      <div>
        <h1 style="margin-bottom: 4px">事件时间线</h1>
        <p class="muted" style="margin-top: 0">按 version 展示 event_store 原始事件</p>
      </div>
      <div style="display: flex; gap: 8px">
        <n-button @click="$router.push(`/runs/${id}`)">返回详情</n-button>
        <n-button @click="$router.push(`/runs/${id}/lineage`)">血缘</n-button>
      </div>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap">
        <div class="muted" style="font-size: 13px">按事件类型筛选（多选，与服务端返回一致）</div>
        <n-button size="tiny" quaternary :disabled="!selected.length" @click="clearFilter">
          清空并恢复全量
        </n-button>
      </div>
      <n-checkbox-group
        v-model:value="selected"
        :disabled="loading"
        style="margin-top: 10px"
        @update:value="onFilterChange"
      >
        <n-space>
          <n-checkbox v-for="opt in typeOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </n-checkbox>
        </n-space>
      </n-checkbox-group>
      <div class="muted" style="margin-top: 8px; font-size: 12px">
        {{ selected.length ? `已选 ${selected.length} 类，由服务端过滤后返回` : '未选择类型，展示全量事件' }}
      </div>
    </div>

    <div class="card">
      <n-spin :show="loading">
        <n-timeline v-if="events.length">
          <n-timeline-item
            v-for="ev in events"
            :key="ev.id"
            :type="itemType(ev.event_type)"
            :title="`v${ev.version} · ${ev.event_type}`"
            :time="formatTime(ev.occurred_at)"
          >
            <div class="muted" style="margin-bottom: 6px">actor: {{ ev.actor }}</div>
            <pre class="mono" style="white-space: pre-wrap; margin: 0; font-size: 12px">{{
              JSON.stringify(ev.payload_json, null, 2)
            }}</pre>
          </n-timeline-item>
        </n-timeline>
        <n-empty
          v-else-if="!loading"
          description="该筛选条件下暂无事件"
          style="padding: 32px 0"
        />
      </n-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { getEvents } from '../api/client'

const route = useRoute()
const message = useMessage()
const events = ref([])
const loading = ref(true)
const selected = ref([])
const id = computed(() => route.params.id)
const storageKey = computed(() => `ep_event_filter_${id.value}`)

// value 必须与后端 event_store.event_type 完全一致，筛选由服务端完成。
const typeOptions = [
  { label: '启动 RunStarted', value: 'RunStarted' },
  { label: '记度量 MetricRecorded', value: 'MetricRecorded' },
  { label: '挂附件 ArtifactAttached', value: 'ArtifactAttached' },
  { label: '完成 RunCompleted', value: 'RunCompleted' },
  { label: '中止 RunAborted', value: 'RunAborted' },
]
const validValues = typeOptions.map((o) => o.value)

function formatTime(v) {
  return new Date(v).toLocaleString()
}

function itemType(t) {
  if (t === 'RunCompleted') return 'success'
  if (t === 'RunAborted') return 'warning'
  if (t === 'RunStarted') return 'info'
  return 'default'
}

function loadStoredSelection() {
  try {
    const raw = localStorage.getItem(storageKey.value)
    const parsed = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return []
    return parsed.filter((v) => validValues.includes(v))
  } catch {
    return []
  }
}

async function fetchEvents() {
  loading.value = true
  try {
    // 空数组 → 不带 event_type 参数 → 服务端返回该 run 的全量事件
    events.value = await getEvents(id.value, selected.value)
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onFilterChange(value) {
  selected.value = value
  localStorage.setItem(storageKey.value, JSON.stringify(value))
  fetchEvents()
}

function clearFilter() {
  selected.value = []
  localStorage.removeItem(storageKey.value)
  fetchEvents()
}

onMounted(async () => {
  // 刷新后仍恢复上次勾选，再向服务端请求对应筛选结果
  selected.value = loadStoredSelection()
  await fetchEvents()
})
</script>
