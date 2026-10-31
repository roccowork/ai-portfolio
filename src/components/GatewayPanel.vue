<script setup lang="ts">
// 模型网关的实时面板：今天花了多少、额度还剩多少、各功能用了多少、最近 14 天的费用。
// 数据来自 /api/ai/stats（读费用账本，不调用模型），每 30 秒刷新
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { getJSON } from '../lib/ai';

interface Stats {
  today: { calls: number; cost: number };
  total: { calls: number; cost: number };
  budget: { daily: number; left: number; public: number; public_left: number };
  enabled: boolean;
  models?: string[];
  quota?: Record<string, number>;
  public_features?: string[];
  daily?: { day: string; calls: number; cost: number }[];
  today_by_feature?: Record<string, { calls: number; cost: number }>;
}

const ZH: Record<string, string> = {
  ask: '作品集问答',
  index: '建索引',
  chatbi: 'ChatBI',
  opsagent: '运维 Agent',
  dutybot: '值班台',
  autoheal: '告警自愈',
  opsdocs: '运维手册检索',
  eval: '效果评测',
  extract: '单据识别',
  triage: '工单分派',
  minutes: '会议纪要',
  content: '内容生成',
  digest: '消息简报',
  agent: '办公 Agent',
};
const name = (f: string) => ZH[f] ?? '其他';

const s = ref<Stats | null>(null);
const err = ref('');
let timer: ReturnType<typeof setInterval> | undefined;

async function load() {
  try {
    s.value = await getJSON<Stats>('/api/ai/stats');
    err.value = '';
  } catch (e) {
    err.value = (e as Error).message;
  }
}
onMounted(() => {
  load();
  timer = setInterval(load, 30_000);
});
onUnmounted(() => clearInterval(timer));

const yuan = (v: number) => `¥${v.toFixed(v < 1 ? 3 : 2)}`;
const used = (total: number, left: number) => (total ? ((total - left) / total) * 100 : 0);

/** 今天各功能：合并成中文名后按费用排序 */
const features = computed(() => {
  const m = new Map<string, { calls: number; cost: number; pub: boolean }>();
  for (const [f, v] of Object.entries(s.value?.today_by_feature ?? {})) {
    const k = name(f);
    const o = m.get(k) ?? { calls: 0, cost: 0, pub: false };
    o.calls += v.calls;
    o.cost += v.cost;
    o.pub ||= (s.value?.public_features ?? []).includes(f);
    m.set(k, o);
  }
  return [...m.entries()].map(([k, v]) => ({ k, ...v })).sort((a, b) => b.cost - a.cost);
});
const maxCost = computed(() => Math.max(0.001, ...features.value.map((f) => f.cost)));
const maxDay = computed(() => Math.max(0.001, ...(s.value?.daily ?? []).map((d) => d.cost)));
const quotas = computed(() =>
  Object.entries(s.value?.quota ?? {})
    .filter(([k]) => k !== 'total' && k !== 'lead')
    .map(([k, v]) => ({ k: name(k), v })),
);
</script>

<template>
  <div class="rounded-3xl bg-slate-950 p-5 text-slate-200 ring-1 ring-white/10 sm:p-8">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2 text-sm">
        <span class="relative flex h-2.5 w-2.5">
          <span
            class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60"
          ></span>
          <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400"></span>
        </span>
        实时 · 每 30 秒刷新 · 读账本，不调用模型
      </div>
      <div v-if="s" class="font-mono text-xs text-slate-400">
        模型：{{ (s.models ?? []).join(' → ') }}
      </div>
    </div>

    <p v-if="err && !s" class="mt-6 text-sm text-rose-300">加载失败：{{ err }}</p>
    <p v-else-if="!s" class="mt-6 text-sm text-slate-400">加载中……</p>

    <template v-else>
      <div class="mt-6 grid gap-3 sm:grid-cols-4">
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">今天调用</div>
          <div class="mt-1 font-mono text-2xl text-white">{{ s.today.calls }}</div>
          <div class="text-xs text-slate-500">累计 {{ s.total.calls }} 次</div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">今天费用</div>
          <div class="mt-1 font-mono text-2xl text-white">{{ yuan(s.today.cost) }}</div>
          <div class="text-xs text-slate-500">累计 {{ yuan(s.total.cost) }}</div>
        </div>
        <div
          v-for="b in [
            { t: '全站每日上限', total: s.budget.daily, left: s.budget.left },
            { t: '访客每日上限', total: s.budget.public, left: s.budget.public_left },
          ]"
          :key="b.t"
          class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10"
        >
          <div class="text-xs text-slate-400">{{ b.t }}</div>
          <div class="mt-1 font-mono text-2xl text-white">
            {{ yuan(b.left) }}<span class="text-sm text-slate-500"> / {{ yuan(b.total) }}</span>
          </div>
          <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
            <div
              class="h-full rounded-full transition-all duration-700"
              :class="used(b.total, b.left) > 80 ? 'bg-rose-400' : 'bg-emerald-400'"
              :style="{ width: `${used(b.total, b.left)}%` }"
            ></div>
          </div>
          <div class="mt-1 text-xs text-slate-500">剩余，用完当天暂停、次日恢复</div>
        </div>
      </div>

      <div class="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <p class="text-sm font-semibold text-white">今天各功能的费用</p>
          <p v-if="!features.length" class="mt-3 text-sm text-slate-500">今天还没有调用。</p>
          <ul v-else class="mt-3 space-y-2">
            <li v-for="f in features" :key="f.k" class="text-sm">
              <div class="flex justify-between gap-2">
                <span
                  >{{ f.k }}
                  <span
                    v-if="f.pub"
                    class="ml-1 rounded bg-amber-400/15 px-1.5 text-xs text-amber-300"
                    >访客</span
                  ></span
                >
                <span class="font-mono text-slate-400">{{ f.calls }} 次 · {{ yuan(f.cost) }}</span>
              </div>
              <div class="mt-1 h-1.5 rounded-full bg-white/5">
                <div
                  class="h-full rounded-full bg-sky-400 transition-all duration-700"
                  :style="{ width: `${(f.cost / maxCost) * 100}%` }"
                ></div>
              </div>
            </li>
          </ul>
        </div>
        <div>
          <p class="text-sm font-semibold text-white">最近 {{ s.daily?.length ?? 0 }} 天的费用</p>
          <div class="mt-3 flex h-36 items-end gap-1.5">
            <div
              v-for="d in s.daily ?? []"
              :key="d.day"
              class="group relative flex-1 rounded-t bg-emerald-400/70 hover:bg-emerald-300"
              :style="{ height: `${Math.max(2, (d.cost / maxDay) * 100)}%` }"
            >
              <span
                class="pointer-events-none absolute bottom-full left-1/2 mb-1 hidden -translate-x-1/2 rounded bg-slate-800 px-2 py-1 font-mono text-xs whitespace-nowrap text-white group-hover:block"
                >{{ d.day.slice(5) }} · {{ d.calls }} 次 · {{ yuan(d.cost) }}</span
              >
            </div>
          </div>
          <div class="mt-1 flex justify-between font-mono text-xs text-slate-500">
            <span>{{ s.daily?.[0]?.day.slice(5) }}</span
            ><span>{{ s.daily?.at(-1)?.day.slice(5) }}</span>
          </div>
          <p class="mt-4 text-sm font-semibold text-white">每个访客（按 IP）每天的次数</p>
          <div class="mt-2 flex flex-wrap gap-2 text-xs">
            <span class="rounded-full bg-white/10 px-2.5 py-1">合计 {{ s.quota?.total }} 次</span>
            <span v-for="q in quotas" :key="q.k" class="rounded-full bg-white/5 px-2.5 py-1"
              >{{ q.k }} {{ q.v }}</span
            >
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
