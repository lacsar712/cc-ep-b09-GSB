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
        <div class="muted" style="font-weight: 600">按事件类型筛选（服务端过滤，支持多选）</div>
        <n-button size="small" quaternary :disabled="!isFiltering" @click="resetFilter">
          清空并恢复全量
        </n-button>
      </div>
      <n-checkbox-group v-model:value="selectedTypes" style="margin-top: 10px">
        <n-checkbox
          v-for="opt in TYPE_OPTIONS"
          :key="opt.value"
          :value="opt.value"
          style="margin-right: 20px"
        >
          {{ opt.label }}
        </n-checkbox>
      </n-checkbox-group>
      <div class="muted" style="margin-top: 8px; font-size: 12px">
        <template v-if="isFiltering">
          已选 {{ selectedTypes.length }} 类 · 仅展示服务端返回的事件，version 与全量中该类事件一致
        </template>
        <template v-else>未勾选时展示全量事件（勾选状态会保留，刷新后仍在）</template>
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
        <div v-else-if="!loading" class="muted" style="padding: 24px 0; text-align: center">
          <template v-if="isFiltering">
            所选类型下没有事件
            <div style="margin-top: 8px">
              <n-button size="small" @click="resetFilter">恢复全量</n-button>
            </div>
          </template>
          <template v-else>暂无事件</template>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { getEvents } from '../api/client'

const route = useRoute()
const message = useMessage()
const events = ref([])
const loading = ref(false)
const id = computed(() => route.params.id)

const TYPE_OPTIONS = [
  { label: '启动 RunStarted', value: 'RunStarted' },
  { label: '记度量 MetricRecorded', value: 'MetricRecorded' },
  { label: '挂附件 ArtifactAttached', value: 'ArtifactAttached' },
  { label: '完成 RunCompleted', value: 'RunCompleted' },
  { label: '中止 RunAborted', value: 'RunAborted' },
]
const VALID_TYPES = TYPE_OPTIONS.map((o) => o.value)

function storageKey(runId) {
  return `ep_event_filter_${runId}`
}

function readStored(runId) {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(runId)) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((t) => VALID_TYPES.includes(t))
  } catch {
    return []
  }
}

// Apply this run's saved filter on first paint; it survives refresh.
const selectedTypes = ref(readStored(id.value))
const isFiltering = computed(() => selectedTypes.value.length > 0)

// Drop responses that arrive after a newer selection so lists never go stale.
let requestSeq = 0
async function load() {
  const seq = ++requestSeq
  loading.value = true
  try {
    // Filtering is authoritative on the server: the rendered list is exactly
    // /events?event_type=..., so no client-side mixing of types is possible.
    const data = await getEvents(id.value, selectedTypes.value)
    if (seq !== requestSeq) return
    events.value = data
  } catch (e) {
    if (seq === requestSeq) {
      message.error(e.message || '加载失败')
      events.value = []
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function persist() {
  localStorage.setItem(storageKey(id.value), JSON.stringify(selectedTypes.value))
}

function resetFilter() {
  selectedTypes.value = []
}

// Re-select this run's saved types immediately when navigating between runs;
// flush:'sync' ensures the combined reload below already sees the new selection.
watch(
  id,
  (newId) => {
    selectedTypes.value = readStored(newId)
  },
  { flush: 'sync' },
)
// Reload from the server on run switch or any multi-selection change
// (clearing every box returns the full list).
watch([id, selectedTypes], load, { deep: true })
watch(selectedTypes, persist, { deep: true })

onMounted(load)

function formatTime(v) {
  return new Date(v).toLocaleString()
}

function itemType(t) {
  if (t === 'RunCompleted') return 'success'
  if (t === 'RunAborted') return 'warning'
  if (t === 'RunStarted') return 'info'
  return 'default'
}
</script>
