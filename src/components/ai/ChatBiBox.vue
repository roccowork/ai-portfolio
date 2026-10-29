<script setup lang="ts">
import { computed, ref } from 'vue';
import { postJSON, type Meta } from '../../lib/ai';
import AiMeta from './AiMeta.vue';

interface Plan {
  sql?: string | null;
  chart?: string;
  x?: string;
  y?: string;
  title?: string;
}
interface Result {
  plan: Plan;
  columns: string[];
  rows: (string | number | null)[][];
  answer: string;
  retried: boolean;
  meta: Meta;
}

// 查的是本站自己的运行数据（每 10 分钟更新）
const EXAMPLES = [
  '最近 7 天每天的 AI 调用次数',
  '哪个 AI 功能花钱最多？',
  '告警自愈一共处理了几次，成功率多少？',
  '运维知识库评测的命中率变化',
  '最近每天的访问量和 5xx 错误',
  '最近几次发布是什么时候？',
];

const question = ref('');
const loading = ref(false);
const error = ref('');
const res = ref<Result | null>(null);

async function ask(q?: string) {
  if (q) question.value = q;
  const text = question.value.trim();
  if (text.length < 2 || loading.value) return;
  loading.value = true;
  error.value = '';
  res.value = null;
  try {
    res.value = await postJSON<Result>('/api/ai/chatbi', { question: text });
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

// 简单的柱状图 / 折线图：x 列作标签，y 列作数值，最多 20 个点
const chart = computed(() => {
  const r = res.value;
  if (!r || !r.rows.length || r.plan.chart === 'table') return null;
  const xi = Math.max(0, r.columns.indexOf(r.plan.x ?? ''));
  let yi = r.columns.indexOf(r.plan.y ?? '');
  if (yi < 0) yi = r.columns.findIndex((_, i) => i !== xi && typeof r.rows[0][i] === 'number');
  if (yi < 0) return null;
  const pts = r.rows
    .slice(0, 20)
    .map((row) => ({ label: String(row[xi]), v: Number(row[yi]) || 0 }));
  // 纵轴包含 0，负数（比如环比下降）画在 0 线下方
  const hi = Math.max(0, ...pts.map((p) => p.v));
  const lo = Math.min(0, ...pts.map((p) => p.v));
  const span = hi - lo || 1;
  const yOf = (v: number) => 10 + ((hi - v) / span) * (H - 20);
  return { type: r.plan.chart === 'line' ? 'line' : 'bar', pts, yOf, y: r.columns[yi] };
});

const W = 600;
const H = 220;
const fmt = (v: number) =>
  Math.abs(v) >= 10000
    ? `${(v / 10000).toFixed(1)}万`
    : Number.isInteger(v)
      ? String(v)
      : v.toFixed(2);
const linePath = computed(() => {
  const c = chart.value;
  if (!c) return '';
  const step = W / Math.max(c.pts.length - 1, 1);
  return c.pts
    .map((p, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${c.yOf(p.v).toFixed(1)}`)
    .join(' ');
});
const show = (v: unknown) => (v === null || v === undefined ? '—' : String(v));
</script>

<template>
  <div>
    <form class="flex flex-col gap-2 sm:flex-row" @submit.prevent="ask()">
      <input
        v-model="question"
        maxlength="200"
        placeholder="用一句话问数据，例如：哪个 AI 功能花钱最多？"
        class="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2"
      />
      <button
        type="submit"
        :disabled="loading || question.trim().length < 2"
        class="rounded-lg bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
      >
        {{ loading ? '查询中…' : '查询' }}
      </button>
    </form>
    <div class="mt-3 flex flex-wrap gap-2 text-sm">
      <button
        v-for="q in EXAMPLES"
        :key="q"
        type="button"
        class="rounded-full border border-slate-300 px-3 py-1 text-slate-600 hover:border-slate-500"
        @click="ask(q)"
      >
        {{ q }}
      </button>
    </div>

    <p v-if="error" class="mt-4 rounded-lg bg-amber-50 px-4 py-3 text-amber-800">
      {{ error }}
    </p>

    <div v-if="res" class="mt-5 rounded-xl border border-slate-200 bg-white px-5 py-4">
      <p class="leading-relaxed">{{ res.answer }}</p>

      <figure v-if="chart" class="mt-4">
        <figcaption class="mb-2 text-sm font-semibold text-slate-500">
          {{ res.plan.title || chart.y }}
        </figcaption>
        <svg
          :viewBox="`-10 -10 ${W + 20} ${H + 50}`"
          class="w-full text-sky-600"
          role="img"
          :aria-label="res.plan.title || chart.y"
        >
          <line x1="0" :y1="chart.yOf(0)" :x2="W" :y2="chart.yOf(0)" class="stroke-slate-300" />
          <template v-if="chart.type === 'bar'">
            <g
              v-for="(p, i) in chart.pts"
              :key="i"
              :transform="`translate(${(i * W) / chart.pts.length},0)`"
            >
              <rect
                :x="4"
                :y="Math.min(chart.yOf(p.v), chart.yOf(0))"
                :width="W / chart.pts.length - 8"
                :height="Math.abs(chart.yOf(p.v) - chart.yOf(0))"
                rx="3"
                :class="p.v < 0 ? 'fill-red-500' : 'fill-current'"
              />
              <text
                :x="W / chart.pts.length / 2"
                :y="p.v < 0 ? chart.yOf(p.v) + 13 : chart.yOf(p.v) - 4"
                text-anchor="middle"
                class="fill-slate-600 text-[11px]"
              >
                {{ fmt(p.v) }}
              </text>
              <text
                :x="W / chart.pts.length / 2"
                :y="H + 16"
                text-anchor="middle"
                class="fill-slate-500 text-[11px]"
              >
                {{ p.label.length > 8 ? p.label.slice(0, 8) + '…' : p.label }}
              </text>
            </g>
          </template>
          <template v-else>
            <path :d="linePath" fill="none" stroke="currentColor" stroke-width="2" />
            <g v-for="(p, i) in chart.pts" :key="i">
              <circle
                :cx="(i * W) / Math.max(chart.pts.length - 1, 1)"
                :cy="chart.yOf(p.v)"
                r="3"
                fill="currentColor"
              />
              <text
                v-if="chart.pts.length <= 12 || i % 2 === 0"
                :x="(i * W) / Math.max(chart.pts.length - 1, 1)"
                :y="H + 16"
                text-anchor="middle"
                class="fill-slate-500 text-[11px]"
              >
                {{ p.label }}
              </text>
            </g>
          </template>
        </svg>
      </figure>

      <div v-if="res.rows.length" class="mt-4 max-h-72 overflow-auto">
        <table class="w-full text-sm">
          <thead class="sticky top-0 bg-white text-left text-slate-500">
            <tr>
              <th v-for="c in res.columns" :key="c" class="py-1 pr-3">{{ c }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in res.rows" :key="i" class="border-t border-slate-200">
              <td v-for="(v, j) in row" :key="j" class="py-1 pr-3">{{ show(v) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-if="res.plan.sql" class="mt-4 text-sm">
        <summary class="cursor-pointer text-sky-700">
          查看 AI 写的 SQL{{ res.retried ? '（第一次执行出错，AI 根据报错改了一次）' : '' }}
        </summary>
        <pre
          class="mt-2 overflow-x-auto rounded-lg bg-slate-100 p-3 font-mono text-xs whitespace-pre-wrap"
          >{{ res.plan.sql }}</pre>
      </details>
      <AiMeta :meta="res.meta" />
    </div>
  </div>
</template>
