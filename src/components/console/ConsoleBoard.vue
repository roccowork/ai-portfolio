<script setup lang="ts">
// 总控台：所有项目和资源一张图。每个自动化项目是一名「数字员工」，资源是普通卡片；
// 连线表示数据从哪儿到哪儿，最近真有数据走过的线有流动的光点。数据来自 /api/ai/console（要私人密钥），30 秒刷新一次。
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';

interface Node {
  id: string;
  col: string;
  kind?: string;
  name: string;
  role?: string;
  desc: string;
  link?: string;
  status: string;
  lines: string[];
}
interface Edge {
  from: string;
  to: string;
  label: string;
  state: 'flow' | 'idle' | 'bad';
}
interface Data {
  generated: string;
  columns: { key: string; name: string }[];
  nodes: Node[];
  edges: Edge[];
  summary: { staff: number; staff_ok: number; problems: string[]; flows: number; alerts: number };
}

const STATUS: Record<string, [string, string]> = {
  ok: ['在岗', 'ok'],
  warn: ['需关注', 'warn'],
  bad: ['异常', 'bad'],
  idle: ['空闲', 'idle'],
  manual: ['手动', 'manual'],
  unknown: ['查不到', 'idle'],
};

const data = ref<Data | null>(null);
const error = ref('');
const hover = ref<string | null>(null);
const board = ref<HTMLElement | null>(null);
const paths = ref<{ d: string; e: Edge; mid: [number, number] }[]>([]);
const size = ref({ w: 0, h: 0 });
const wide = ref(true);
let key = '';
let timer: ReturnType<typeof setInterval> | undefined;
let ro: ResizeObserver | undefined;

const byCol = computed(() => {
  const d = data.value;
  if (!d) return [];
  return d.columns.map((c) => ({ ...c, nodes: d.nodes.filter((n) => n.col === c.key) }));
});
const names = computed(() =>
  Object.fromEntries((data.value?.nodes || []).map((n) => [n.id, n.name])),
);

function linkOf(n: Node) {
  return n.link ? n.link.replace('{k}', encodeURIComponent(key)) : '';
}
function related(id: string) {
  return (data.value?.edges || []).filter((e) => e.from === id || e.to === id);
}
function active(e: Edge) {
  return !hover.value || e.from === hover.value || e.to === hover.value;
}
function fmt(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', {
    timeZone: 'Asia/Singapore',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

// 根据卡片的实际位置画连线：左边列连到右边列用横向曲线，同一列用竖向曲线
function layout() {
  const el = board.value;
  wide.value = window.innerWidth >= 1024;
  if (!el || !data.value || !wide.value) return;
  const box = el.getBoundingClientRect();
  size.value = { w: box.width, h: box.height };
  const rect = (id: string) => {
    const c = el.querySelector(`[data-node="${id}"]`);
    return c ? c.getBoundingClientRect() : null;
  };
  const out: typeof paths.value = [];
  for (const e of data.value.edges) {
    const a = rect(e.from);
    const b = rect(e.to);
    if (!a || !b) continue;
    let x1: number, y1: number, x2: number, y2: number, d: string;
    if (Math.abs(a.left - b.left) < 10) {
      // 同一列：从下面连到上面（或反过来），向右鼓出去一点
      const down = a.top < b.top;
      x1 = a.right - box.left;
      y1 = (down ? a.bottom - 18 : a.top + 18) - box.top;
      x2 = b.right - box.left;
      y2 = (down ? b.top + 18 : b.bottom - 18) - box.top;
      d = `M${x1},${y1} C${x1 + 40},${y1} ${x2 + 40},${y2} ${x2},${y2}`;
    } else {
      const ltr = a.left < b.left;
      x1 = (ltr ? a.right : a.left) - box.left;
      y1 = a.top + a.height / 2 - box.top;
      x2 = (ltr ? b.left : b.right) - box.left;
      y2 = b.top + b.height / 2 - box.top;
      const dx = Math.max(40, Math.abs(x2 - x1) / 2) * (ltr ? 1 : -1);
      d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
    }
    out.push({ d, e, mid: [(x1 + x2) / 2, (y1 + y2) / 2] });
  }
  paths.value = out;
}

async function load() {
  try {
    const r = await fetch('/api/ai/console', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ k: key }),
    });
    if (!r.ok) throw new Error(r.status === 404 ? '密钥不对' : `服务暂时不可用（${r.status}）`);
    data.value = (await r.json()) as Data;
    error.value = '';
    await nextTick();
    layout();
  } catch (e) {
    error.value = (e as Error).message;
  }
}

onMounted(() => {
  key = decodeURIComponent((location.hash.match(/k=([^&]+)/) || [])[1] || '');
  if (!key) {
    error.value = '链接里缺少私人密钥（#k=…）';
    return;
  }
  load();
  timer = setInterval(load, 30000);
  ro = new ResizeObserver(() => layout());
  if (board.value) ro.observe(board.value);
  window.addEventListener('resize', layout);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  ro?.disconnect();
  window.removeEventListener('resize', layout);
});
</script>

<template>
  <div class="mx-auto max-w-[1400px] px-4 pt-6 pb-16 sm:px-6">
    <header class="flex flex-wrap items-center gap-4">
      <img src="/favicon.svg?v=4" alt="" width="40" height="40" class="size-10" />
      <div>
        <h1 class="text-xl font-semibold tracking-wide">向晓 AI · 总控台</h1>
        <p class="text-sm text-slate-400">
          我所有的项目和资源：数字员工在不在岗、数据往哪儿流（新加坡时间）
        </p>
      </div>
      <div v-if="data" class="ml-auto flex flex-wrap gap-2 text-sm">
        <span class="chip"
          >👥 数字员工 {{ data.summary.staff_ok }}/{{ data.summary.staff }} 在岗</span
        >
        <span class="chip">⚡ {{ data.summary.flows }} 条数据流动中</span>
        <span class="chip" :class="data.summary.alerts ? 'text-amber-300' : ''"
          >🚨 {{ data.summary.alerts }} 条告警</span
        >
        <span class="chip text-slate-400">更新于 {{ fmt(data.generated) }}</span>
      </div>
    </header>

    <p v-if="error" class="mt-8 rounded-xl bg-red-500/10 p-4 text-red-300 ring-1 ring-red-500/30">
      {{ error }}
    </p>
    <p
      v-if="data && data.summary.problems.length"
      class="mt-5 rounded-xl bg-amber-400/10 px-4 py-3 text-sm text-amber-200 ring-1 ring-amber-400/30"
    >
      需要看一眼：{{ data.summary.problems.join('、') }}
    </p>
    <p v-if="!data && !error" class="mt-10 text-slate-400">加载中……</p>

    <div v-if="data" ref="board" class="relative mt-6 grid gap-6 lg:grid-cols-4 lg:gap-14">
      <svg
        v-if="wide"
        class="pointer-events-none absolute inset-0 z-0"
        :width="size.w"
        :height="size.h"
      >
        <g v-for="(p, i) in paths" :key="i" :class="{ dim: !active(p.e) }">
          <path :d="p.d" class="edge" :class="p.e.state" />
          <text
            v-if="hover && active(p.e)"
            :x="p.mid[0]"
            :y="p.mid[1] - 6"
            class="label"
            text-anchor="middle"
          >
            {{ p.e.label }}
          </text>
        </g>
      </svg>

      <section v-for="col in byCol" :key="col.key" class="relative z-10 flex flex-col gap-4">
        <h2 class="text-xs font-semibold tracking-[0.25em] text-amber-400">{{ col.name }}</h2>
        <component
          :is="linkOf(n) ? 'a' : 'div'"
          v-for="n in col.nodes"
          :key="n.id"
          :href="linkOf(n) || undefined"
          :target="linkOf(n) ? '_blank' : undefined"
          rel="noopener"
          :data-node="n.id"
          class="card"
          :class="[
            `st-${STATUS[n.status]?.[1] || 'idle'}`,
            { staff: n.kind === 'staff', hot: hover === n.id },
          ]"
          @mouseenter="hover = n.id"
          @mouseleave="hover = null"
        >
          <div class="flex items-start gap-3">
            <span v-if="n.kind === 'staff'" class="avatar">{{ n.name.slice(0, 1) }}</span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <b class="truncate">{{ n.name }}</b>
                <span class="badge">{{ STATUS[n.status]?.[0] || n.status }}</span>
              </div>
              <p v-if="n.role" class="text-xs text-amber-200/80">{{ n.role }}</p>
            </div>
            <span v-if="linkOf(n)" class="text-slate-500">↗</span>
          </div>
          <p class="mt-2 text-xs leading-relaxed text-slate-400">{{ n.desc }}</p>
          <ul v-if="n.lines.length" class="mt-2 space-y-0.5 text-xs text-slate-200">
            <li v-for="(l, i) in n.lines" :key="i">· {{ l }}</li>
          </ul>
          <!-- 窄屏不画连线，直接写出它连着谁 -->
          <ul v-if="!wide" class="mt-2 space-y-0.5 text-[11px] text-slate-500">
            <li v-for="(e, i) in related(n.id)" :key="i">
              {{ e.from === n.id ? `→ ${names[e.to]}` : `← ${names[e.from]}` }}（{{ e.label }}）{{
                e.state === 'flow' ? ' ⚡' : e.state === 'bad' ? ' ⚠️' : ''
              }}
            </li>
          </ul>
        </component>
      </section>
    </div>

    <footer v-if="data" class="mt-10 flex flex-wrap gap-5 text-xs text-slate-400">
      <span><i class="lg ok"></i>在岗 / 正常</span>
      <span><i class="lg warn"></i>需关注</span>
      <span><i class="lg bad"></i>异常</span>
      <span><i class="lg manual"></i>手动</span>
      <span>金色流动的线：最近真有数据走过（发布、备份、调用模型、告警……）</span>
      <span>鼠标移到卡片上：只看它的连线；点卡片：看详情</span>
    </footer>
  </div>
</template>

<style scoped>
.chip {
  border-radius: 999px;
  border: 1px solid rgb(51 65 85);
  background: rgb(15 23 42 / 0.7);
  padding: 4px 12px;
}
.card {
  display: block;
  border-radius: 14px;
  border: 1px solid rgb(30 41 59);
  background: rgb(14 21 36 / 0.92);
  padding: 12px 14px;
  transition:
    border-color 0.2s,
    transform 0.2s;
}
.card.hot {
  transform: translateY(-2px);
  border-color: rgb(251 191 36 / 0.7);
}
.card.staff {
  background: linear-gradient(180deg, rgb(20 28 46 / 0.95), rgb(14 21 36 / 0.95));
}
.avatar {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  font-weight: 700;
  color: #0b1220;
  background: #fbbf24;
}
.badge {
  flex-shrink: 0;
  border-radius: 999px;
  padding: 0 8px;
  font-size: 11px;
  line-height: 18px;
}
.st-ok .badge {
  background: rgb(52 211 153 / 0.15);
  color: #34d399;
}
.st-warn .badge {
  background: rgb(251 191 36 / 0.15);
  color: #fbbf24;
}
.st-bad .badge {
  background: rgb(248 113 113 / 0.15);
  color: #f87171;
}
.st-bad {
  border-color: rgb(248 113 113 / 0.5);
}
.st-manual .badge {
  background: rgb(96 165 250 / 0.15);
  color: #60a5fa;
}
.st-idle .badge {
  background: rgb(71 85 105 / 0.3);
  color: #94a3b8;
}
.st-ok .avatar {
  box-shadow: 0 0 0 3px rgb(52 211 153 / 0.35);
}
.st-bad .avatar {
  background: #f87171;
}
.st-warn .avatar {
  box-shadow: 0 0 0 3px rgb(251 191 36 / 0.45);
}
.st-idle .avatar {
  background: #475569;
}
.edge {
  fill: none;
  stroke-width: 1.6;
  transition: opacity 0.2s;
}
.edge.idle {
  stroke: rgb(71 85 105 / 0.55);
}
.edge.bad {
  stroke: rgb(248 113 113 / 0.75);
  stroke-dasharray: 5 5;
}
.edge.flow {
  stroke: #fbbf24;
  stroke-width: 2;
  stroke-dasharray: 6 10;
  animation: flow 1.2s linear infinite;
  filter: drop-shadow(0 0 3px rgb(251 191 36 / 0.6));
}
@keyframes flow {
  to {
    stroke-dashoffset: -32;
  }
}
.dim {
  opacity: 0.08;
}
.label {
  fill: #fde68a;
  font-size: 11px;
  paint-order: stroke;
  stroke: #070b14;
  stroke-width: 4px;
}
.lg {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}
.lg.ok {
  background: #34d399;
}
.lg.warn {
  background: #fbbf24;
}
.lg.bad {
  background: #f87171;
}
.lg.manual {
  background: #60a5fa;
}
</style>
