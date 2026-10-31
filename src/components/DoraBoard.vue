<script setup lang="ts">
// DORA 四个指标：用本站自己的真实发布和故障记录算（/api/ai/dora，缓存 1 小时）。算法见 ai/app/dora.py
import { onMounted, ref } from 'vue';
import { getJSON } from '../lib/ai';

interface Dora {
  days: number;
  deploys: number;
  incidents: number;
  restored: number;
  deploys_per_day: number;
  lead_hours: number | null;
  failure_pct: number | null;
  restore_hours: number | null;
  levels: Record<string, string | null>;
  ts: string;
}

const d = ref<Dora | null>(null);
const err = ref('');
onMounted(async () => {
  try {
    d.value = await getJSON<Dora>('/api/ai/dora');
  } catch (e) {
    err.value = (e as Error).message;
  }
});

const dur = (h: number | null) =>
  h == null
    ? '—'
    : h < 1
      ? `${Math.round(h * 60)} 分钟`
      : h < 48
        ? `${h.toFixed(1)} 小时`
        : `${(h / 24).toFixed(1)} 天`;
const LEVEL_CLASS: Record<string, string> = {
  精英: 'bg-emerald-400/15 text-emerald-300 ring-emerald-400/40',
  高: 'bg-sky-400/15 text-sky-300 ring-sky-400/40',
  中: 'bg-amber-400/15 text-amber-300 ring-amber-400/40',
  低: 'bg-rose-400/15 text-rose-300 ring-rose-400/40',
};
const tiles = (x: Dora) => [
  {
    k: 'deploys_per_day',
    name: '部署频率',
    value: `${x.deploys_per_day} 次/天`,
    what: '多久发布一次。精英：按需发布，每天多次',
    how: `${x.deploys} 次发布：CI 写回镜像标签的提交，一条算一次`,
  },
  {
    k: 'lead_hours',
    name: '提交到上线',
    value: dur(x.lead_hours),
    what: '代码提交后多久到生产。精英：一天以内',
    how: '提交时间 → 集群里新版本开始上线（ReplicaSet 创建时间），取中位数',
  },
  {
    k: 'failure_pct',
    name: '变更失败率',
    value: x.failure_pct == null ? '—' : `${x.failure_pct}%`,
    what: '发布后出问题的比例。精英：5% 以内',
    how: '发布后 1 小时内站点出告警，或之后被 revert 的发布',
  },
  {
    k: 'restore_hours',
    name: '故障恢复',
    value: dur(x.restore_hours),
    what: '出了故障多久恢复。精英：1 小时以内',
    how:
      x.restored > 0
        ? `${x.restored} 次故障，从告警开始到恢复的中位数`
        : '还没有带恢复时间的故障记录',
  },
];
</script>

<template>
  <div class="rounded-3xl bg-slate-900 p-5 text-slate-200 ring-1 ring-white/10 sm:p-8">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <p class="text-sm font-semibold text-white">DORA 四个指标：交付有多快、有多稳</p>
      <p v-if="d" class="text-xs text-slate-400">最近 {{ d.days }} 天的真实数据 · 每小时更新</p>
    </div>
    <p v-if="err" class="mt-4 text-sm text-slate-400">暂时查不到：{{ err }}</p>
    <p v-else-if="!d" class="mt-4 text-sm text-slate-400">计算中……</p>
    <template v-else>
      <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div
          v-for="t in tiles(d)"
          :key="t.k"
          class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs text-slate-400">{{ t.name }}</span>
            <span
              v-if="d.levels[t.k]"
              class="rounded-full px-2 py-0.5 text-xs ring-1"
              :class="LEVEL_CLASS[d.levels[t.k]!]"
              >{{ d.levels[t.k] }}</span
            >
          </div>
          <div class="mt-1 font-mono text-2xl text-white">{{ t.value }}</div>
          <p class="mt-2 text-xs leading-relaxed text-slate-400">{{ t.what }}</p>
          <p class="mt-1 text-xs leading-relaxed text-slate-500">{{ t.how }}</p>
        </div>
      </div>
      <p class="mt-4 text-xs leading-relaxed text-slate-500">
        分档参照 DORA 年度报告（精英 / 高 / 中 / 低）。这个仓库 2026-10-29
        才拆出来，数据天数还少，个人项目发布频繁、没有发布审批，分档只作参考。
        <a
          class="text-amber-300 hover:underline"
          href="https://github.com/roccowork/ai-portfolio/blob/main/ai/app/dora.py"
          target="_blank"
          rel="noopener"
          >算法 ↗</a
        >
      </p>
    </template>
  </div>
</template>
