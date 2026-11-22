<script setup lang="ts">
// 总控台「数据流」标签页：所有项目和资源一张图，连线表示数据从哪儿到哪儿，最近真有数据走过的线有流动的光点。
// 原来是总控台首页，2026-11-22 改版后单独放一页，首页改成概览。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { STATUS, type ConsoleData, type ConsoleEdge } from './types';

const props = defineProps<{ data: ConsoleData }>();
const hover = ref<string | null>(null);
const board = ref<HTMLElement | null>(null);
const paths = ref<{ d: string; e: ConsoleEdge; mid: [number, number] }[]>([]);
const size = ref({ w: 0, h: 0 });
const wide = ref(true);
let ro: ResizeObserver | undefined;

const byCol = computed(() =>
  props.data.columns.map((c) => ({
    ...c,
    nodes: props.data.nodes.filter((n) => n.col === c.key),
  })),
);
const names = computed(() => Object.fromEntries(props.data.nodes.map((n) => [n.id, n.name])));

function related(id: string) {
  return props.data.edges.filter((e) => e.from === id || e.to === id);
}
function active(e: ConsoleEdge) {
  return !hover.value || e.from === hover.value || e.to === hover.value;
}

// 根据卡片的实际位置画连线：左边列连到右边列用横向曲线，同一列用竖向曲线
function layout() {
  const el = board.value;
  wide.value = window.innerWidth >= 1024;
  if (!el || !wide.value) return;
  const box = el.getBoundingClientRect();
  size.value = { w: box.width, h: box.height };
  const rect = (id: string) => {
    const c = el.querySelector(`[data-node="${id}"]`);
    return c ? c.getBoundingClientRect() : null;
  };
  const out: typeof paths.value = [];
  for (const e of props.data.edges) {
    const a = rect(e.from);
    const b = rect(e.to);
    if (!a || !b) continue;
    let x1: number, y1: number, x2: number, y2: number, d: string;
    if (Math.abs(a.left - b.left) < 10) {
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

watch(
  () => props.data,
  async () => {
    await nextTick();
    layout();
  },
);
onMounted(async () => {
  await nextTick();
  layout();
  ro = new ResizeObserver(() => layout());
  if (board.value) ro.observe(board.value);
});
onBeforeUnmount(() => ro?.disconnect());
</script>

<template>
  <div>
    <div ref="board" class="relative grid gap-6 lg:grid-cols-4 lg:gap-14">
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
        <div
          v-for="n in col.nodes"
          :key="n.id"
          :data-node="n.id"
          class="node"
          :class="[`st-${STATUS[n.status]?.[1] || 'idle'}`, { hot: hover === n.id }]"
          @mouseenter="hover = n.id"
          @mouseleave="hover = null"
        >
          <div class="flex items-center gap-2">
            <b class="truncate">{{ n.name }}</b>
            <span class="badge">{{ STATUS[n.status]?.[0] || n.status }}</span>
          </div>
          <p v-if="n.role" class="text-xs text-amber-200/80">{{ n.role }}</p>
          <ul v-if="!wide" class="mt-2 space-y-0.5 text-[11px] text-slate-500">
            <li v-for="(e, i) in related(n.id)" :key="i">
              {{ e.from === n.id ? `→ ${names[e.to]}` : `← ${names[e.from]}` }}（{{ e.label }}）{{
                e.state === 'flow' ? ' ⚡' : e.state === 'bad' ? ' ⚠️' : ''
              }}
            </li>
          </ul>
        </div>
      </section>
    </div>
    <p class="mt-8 text-xs text-slate-400">
      金色流动的线：最近真有数据走过（发布、备份、调用模型、告警……）；红色虚线：一头异常。鼠标移到卡片上只看它的连线。各项的详细状态在「资源中心」「数字员工」里看。
    </p>
  </div>
</template>

<style scoped>
.node {
  border-radius: 12px;
  border: 1px solid rgb(30 41 59);
  background: rgb(14 21 36 / 0.95);
  padding: 10px 12px;
  transition:
    border-color 0.2s,
    transform 0.2s;
}
.node.hot {
  transform: translateY(-2px);
  border-color: rgb(251 191 36 / 0.7);
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
</style>
