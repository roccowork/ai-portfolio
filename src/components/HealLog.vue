<script setup lang="ts">
// 最近自愈记录：告警来了运维 Agent 自己查原因、自己处理（关键操作由人确认），这里展示真实发生过的记录
import { onMounted, ref } from 'vue';
import { getJSON } from '../lib/ai';

interface Item {
  id: number;
  time: string;
  alert: string;
  diagnosis: string;
  action: string;
  mode: string;
  status: string;
  result: string;
  ok: boolean;
}

const items = ref<Item[]>([]);
const loaded = ref(false);
const error = ref('');

onMounted(async () => {
  try {
    items.value = (await getJSON<{ items: Item[] }>('/api/ai/heal/recent')).items;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loaded.value = true;
  }
});
</script>

<template>
  <div>
    <p v-if="!loaded" class="text-sm text-slate-500">加载中……</p>
    <p v-else-if="error" class="text-sm text-red-600">{{ error }}</p>
    <p v-else-if="!items.length" class="rounded-xl bg-slate-50 p-5 text-sm text-slate-600">
      最近没有需要处理的故障，服务器一切正常。有告警时，记录会出现在这里。
    </p>
    <ol v-else class="space-y-4">
      <li
        v-for="it in items"
        :key="it.id"
        class="rounded-xl bg-white p-5 shadow-sm ring-1"
        :class="it.ok ? 'ring-emerald-200' : 'ring-slate-200'"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="font-semibold text-slate-900">
            {{ it.alert }}
            <span class="ml-2 text-sm font-normal text-slate-400">{{ it.time }}（新加坡时间）</span>
          </p>
          <span class="text-sm" :class="it.ok ? 'text-emerald-700' : 'text-slate-600'">{{
            it.status
          }}</span>
        </div>
        <p class="mt-3 text-sm leading-relaxed text-slate-700">{{ it.diagnosis }}</p>
        <p v-if="it.action" class="mt-2 text-sm text-slate-600">
          处理：{{ it.action }}<span class="text-slate-400">（{{ it.mode }}）</span>
        </p>
        <p v-if="it.result" class="mt-1 text-sm text-slate-500">结果：{{ it.result }}</p>
      </li>
    </ol>
  </div>
</template>
