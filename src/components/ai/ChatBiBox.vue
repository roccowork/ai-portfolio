<script setup lang="ts">
// ChatBI：用中文问本站自己的运行数据。上面是数据目录（/api/ai/chatbi/catalog，不调用模型），
// 下面提问后：结论、图表、明细、AI 写的 SQL、执行过程，都来自 /api/ai/chatbi 的真实结果
import { computed, onMounted, ref } from 'vue';
import { getJSON, postJSON, type Meta } from '../../lib/ai';
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
interface Table {
  table: string;
  name: string;
  desc: string;
  rows: number;
  from: string | null;
  to: string | null;
}

// 示例问题按数据表分组；查的是本站自己的运行数据（每 10 分钟更新）
const EXAMPLES: Record<string, string[]> = {
  ai_usage: ['最近 7 天每天的 AI 调用次数', '哪个 AI 功能花钱最多？'],
  incidents: ['告警自愈一共处理了几次，成功率多少？'],
  eval_runs: ['运维知识库评测的命中率变化'],
  site_traffic: ['最近每天的访问量和 5xx 错误'],
  deploys: ['最近几次发布是什么时候？'],
};
const ICON: Record<string, string> = {
  ai_usage: '¥',
  incidents: '⚕',
  eval_runs: '✓',
  site_traffic: '↗',
  deploys: '⇪',
};

const tables = ref<Table[]>([]);
const question = ref('');
const loading = ref(false);
const error = ref('');
const res = ref<Result | null>(null);
const asked = ref('');

onMounted(async () => {
  try {
    tables.value = (await getJSON<{ tables: Table[] }>('/api/ai/chatbi/catalog')).tables;
  } catch {
    // 目录拿不到不影响提问
  }
});

async function ask(q?: string) {
  if (q) question.value = q;
  const text = question.value.trim();
  if (text.length < 2 || loading.value) return;
  loading.value = true;
  error.value = '';
  res.value = null;
  asked.value = text;
  try {
    res.value = await postJSON<Result>('/api/ai/chatbi', { question: text });
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

// ── 图表：x 列作标签，y 列作数值，最多 20 个点；纵轴包含 0，负数画在 0 线下方 ──
const W = 600;
const H = 220;
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
  const hi = Math.max(0, ...pts.map((p) => p.v));
  const lo = Math.min(0, ...pts.map((p) => p.v));
  const span = hi - lo || 1;
  const yOf = (v: number) => 10 + ((hi - v) / span) * (H - 20);
  const ticks = [hi, lo + span / 2, lo].filter((v, i, a) => a.indexOf(v) === i);
  return { type: r.plan.chart === 'line' ? 'line' : 'bar', pts, yOf, ticks, y: r.columns[yi] };
});
const fmt = (v: number) =>
  Math.abs(v) >= 10000
    ? `${(v / 10000).toFixed(1)}万`
    : Number.isInteger(v)
      ? String(v)
      : v.toFixed(2);
const xOf = (i: number) => (i * W) / Math.max((chart.value?.pts.length ?? 1) - 1, 1);
const linePath = computed(() =>
  (chart.value?.pts ?? [])
    .map((p, i) => `${i ? 'L' : 'M'}${xOf(i).toFixed(1)},${chart.value!.yOf(p.v).toFixed(1)}`)
    .join(' '),
);
const show = (v: unknown) => (v === null || v === undefined ? '—' : String(v));
const isNum = (v: unknown) => typeof v === 'number';

// ── SQL 高亮：先转义，再给关键字和字符串上色 ──
const KW =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LIMIT|AS|AND|OR|ON|JOIN|LEFT|IN|NOT|NULL|IS|DESC|ASC|COUNT|SUM|AVG|MIN|MAX|ROUND|CASE|WHEN|THEN|ELSE|END|WITH|DISTINCT|HAVING|BETWEEN|LIKE)\b/gi;
const sqlHtml = computed(() => {
  const sql = res.value?.plan.sql ?? '';
  const esc = sql.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return esc
    .replace(/('[^']*')/g, '<span class="text-amber-300">$1</span>')
    .replace(KW, (m) => `<span class="font-semibold text-sky-300">${m}</span>`);
});

const steps = computed(() => {
  const r = res.value;
  if (!r) return [];
  return [
    { t: '理解问题', d: '按语义层的口径和真实日期' },
    { t: '写 SQL', d: r.retried ? '第一次执行报错，AI 按报错改了一次' : '一次写对' },
    { t: '只读执行', d: `返回 ${r.rows.length} 行 · 最多 200 行、2 秒超时` },
    { t: '解读结果', d: '只按查询结果说，不推测原因' },
  ];
});
</script>

<template>
  <div>
    <!-- 数据目录 -->
    <div v-if="tables.length" class="grid grid-cols-2 gap-3 sm:grid-cols-5">
      <div
        v-for="t in tables"
        :key="t.table"
        class="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200"
      >
        <div class="flex items-center gap-2">
          <span
            class="grid size-7 place-items-center rounded-lg bg-sky-50 text-sm text-sky-700 ring-1 ring-sky-200"
            >{{ ICON[t.table] }}</span
          >
          <span class="text-sm font-semibold text-slate-900">{{ t.name }}</span>
        </div>
        <div class="mt-3 font-mono text-2xl font-bold text-slate-900 tabular-nums">
          {{ t.rows }}<span class="ml-1 text-xs font-normal text-slate-500">行</span>
        </div>
        <p class="mt-1 text-xs leading-relaxed text-slate-500">{{ t.desc }}</p>
        <p class="mt-2 font-mono text-[11px] text-slate-400">
          {{ t.table
          }}<template v-if="t.from"> · {{ t.from.slice(5) }}～{{ t.to?.slice(5) }}</template>
        </p>
      </div>
    </div>

    <!-- 提问 -->
    <div
      class="mt-6 rounded-2xl bg-gradient-to-br from-sky-500 via-indigo-500 to-violet-500 p-[1.5px] shadow-lg shadow-indigo-500/10"
    >
      <form
        class="flex flex-col gap-2 rounded-[15px] bg-white p-3 sm:flex-row"
        @submit.prevent="ask()"
      >
        <input
          v-model="question"
          maxlength="200"
          placeholder="用一句话问数据，例如：哪个 AI 功能花钱最多？"
          class="min-w-0 flex-1 rounded-xl px-3 py-3 text-lg outline-none placeholder:text-slate-400"
        />
        <button
          type="submit"
          :disabled="loading || question.trim().length < 2"
          class="rounded-xl bg-slate-900 px-6 py-3 font-semibold text-white transition hover:bg-slate-700 disabled:opacity-40"
        >
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </form>
    </div>
    <div class="mt-4 flex flex-wrap gap-2 text-sm">
      <template v-for="(qs, table) in EXAMPLES" :key="table">
        <button
          v-for="q in qs"
          :key="q"
          type="button"
          :disabled="loading"
          class="flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-slate-600 shadow-sm ring-1 ring-slate-200 transition hover:ring-indigo-300 disabled:opacity-50"
          @click="ask(q)"
        >
          <span class="text-xs text-sky-600">{{ ICON[table] }}</span
          >{{ q }}
        </button>
      </template>
    </div>

    <p
      v-if="error"
      class="mt-6 rounded-xl bg-amber-50 px-4 py-3 text-amber-800 ring-1 ring-amber-200"
    >
      {{ error }}
    </p>

    <!-- 查询中：骨架屏 -->
    <div
      v-if="loading"
      class="mt-6 space-y-3 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200"
    >
      <p class="text-sm text-slate-500">
        AI 正在理解「{{ asked }}」，写 SQL 并执行，一般 5～15 秒……
      </p>
      <div class="h-5 w-3/4 animate-pulse rounded bg-slate-100"></div>
      <div class="h-40 animate-pulse rounded-xl bg-slate-100"></div>
    </div>

    <!-- 结果 -->
    <div v-if="res" class="mt-6 space-y-4">
      <div class="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <p class="text-xs font-semibold tracking-widest text-indigo-600">结论</p>
        <p class="mt-2 border-l-4 border-indigo-400 pl-4 text-lg leading-relaxed text-slate-900">
          {{ res.answer }}
        </p>
        <ol class="mt-5 grid gap-2 sm:grid-cols-4">
          <li
            v-for="(s, i) in steps"
            :key="s.t"
            class="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200"
          >
            <span class="font-mono text-xs text-indigo-600">0{{ i + 1 }}</span>
            <p class="text-sm font-semibold text-slate-900">{{ s.t }}</p>
            <p class="text-xs text-slate-500">{{ s.d }}</p>
          </li>
        </ol>
      </div>

      <figure v-if="chart" class="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <figcaption class="text-sm font-semibold text-slate-900">
          {{ res.plan.title || chart.y }}
        </figcaption>
        <svg
          :viewBox="`-46 -10 ${W + 56} ${H + 50}`"
          class="mt-4 w-full"
          role="img"
          :aria-label="res.plan.title || chart.y"
        >
          <defs>
            <linearGradient id="bi-bar" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#6366f1" />
              <stop offset="100%" stop-color="#38bdf8" />
            </linearGradient>
            <linearGradient id="bi-area" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#6366f1" stop-opacity="0.25" />
              <stop offset="100%" stop-color="#6366f1" stop-opacity="0" />
            </linearGradient>
          </defs>
          <g v-for="t in chart.ticks" :key="t">
            <line
              x1="0"
              :x2="W"
              :y1="chart.yOf(t)"
              :y2="chart.yOf(t)"
              stroke="#e2e8f0"
              stroke-dasharray="3 4"
            />
            <text x="-8" :y="chart.yOf(t) + 4" text-anchor="end" class="fill-slate-400 text-[11px]">
              {{ fmt(t) }}
            </text>
          </g>
          <line x1="0" :y1="chart.yOf(0)" :x2="W" :y2="chart.yOf(0)" stroke="#94a3b8" />
          <template v-if="chart.type === 'bar'">
            <g
              v-for="(p, i) in chart.pts"
              :key="i"
              :transform="`translate(${(i * W) / chart.pts.length},0)`"
            >
              <rect
                :x="(W / chart.pts.length) * 0.15"
                :y="Math.min(chart.yOf(p.v), chart.yOf(0))"
                :width="(W / chart.pts.length) * 0.7"
                :height="Math.max(1, Math.abs(chart.yOf(p.v) - chart.yOf(0)))"
                rx="4"
                :fill="p.v < 0 ? '#f43f5e' : 'url(#bi-bar)'"
              >
                <title>{{ p.label }}：{{ fmt(p.v) }}</title>
              </rect>
              <text
                :x="W / chart.pts.length / 2"
                :y="p.v < 0 ? chart.yOf(p.v) + 13 : chart.yOf(p.v) - 5"
                text-anchor="middle"
                class="fill-slate-700 text-[11px] font-semibold"
              >
                {{ fmt(p.v) }}
              </text>
              <text
                :x="W / chart.pts.length / 2"
                :y="H + 18"
                text-anchor="middle"
                class="fill-slate-500 text-[11px]"
              >
                {{ p.label.length > 8 ? p.label.slice(0, 8) + '…' : p.label }}
              </text>
            </g>
          </template>
          <template v-else>
            <path
              :d="`${linePath} L${W},${chart.yOf(0)} L0,${chart.yOf(0)} Z`"
              fill="url(#bi-area)"
            />
            <path
              :d="linePath"
              fill="none"
              stroke="#6366f1"
              stroke-width="2.5"
              stroke-linejoin="round"
            />
            <g v-for="(p, i) in chart.pts" :key="i">
              <circle
                :cx="xOf(i)"
                :cy="chart.yOf(p.v)"
                r="4"
                fill="#fff"
                stroke="#6366f1"
                stroke-width="2"
              >
                <title>{{ p.label }}：{{ fmt(p.v) }}</title>
              </circle>
              <text
                v-if="chart.pts.length <= 12 || i % 2 === 0"
                :x="xOf(i)"
                :y="H + 18"
                text-anchor="middle"
                class="fill-slate-500 text-[11px]"
              >
                {{ p.label.length > 10 ? p.label.slice(5) : p.label }}
              </text>
            </g>
          </template>
        </svg>
      </figure>

      <div
        v-if="res.rows.length"
        class="overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-200"
      >
        <p class="border-b border-slate-200 px-5 py-3 text-sm font-semibold text-slate-900">
          明细 <span class="font-normal text-slate-500">· {{ res.rows.length }} 行</span>
        </p>
        <div class="max-h-72 overflow-auto">
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th v-for="c in res.columns" :key="c" class="px-5 py-2 font-medium">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, i) in res.rows"
                :key="i"
                class="border-t border-slate-100 odd:bg-white even:bg-slate-50/60"
              >
                <td
                  v-for="(v, j) in row"
                  :key="j"
                  class="px-5 py-2"
                  :class="isNum(v) ? 'text-right font-mono tabular-nums' : 'text-slate-700'"
                >
                  {{ show(v) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div
        v-if="res.plan.sql"
        class="overflow-hidden rounded-2xl bg-slate-950 shadow-sm ring-1 ring-slate-800"
      >
        <p
          class="flex items-center justify-between border-b border-white/10 px-5 py-3 text-xs text-slate-400"
        >
          <span>AI 写的 SQL（只读执行）</span>
          <span v-if="res.retried" class="text-amber-300">第一次执行报错，按报错改过一次</span>
        </p>
        <!-- eslint-disable vue/no-v-html -- 内容已先转义，只加了上色的 span -->
        <pre
          class="overflow-x-auto p-5 font-mono text-xs leading-relaxed whitespace-pre-wrap text-slate-200"
          v-html="sqlHtml"
        ></pre>
        <!-- eslint-enable vue/no-v-html -->
      </div>
      <AiMeta :meta="res.meta" />
    </div>
  </div>
</template>
