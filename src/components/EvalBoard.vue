<script setup lang="ts">
// RAG 评测看板：两套评测（作品集问答、运维知识库）最近一次的真实分数、最近几次的变化、没答好的题。
// 数据来自 /api/ai/eval/latest（值班台点「📏 RAG 评测」时真实调用模型跑出来的）
import { onMounted, ref } from 'vue';
import { getJSON } from '../lib/ai';

interface Summary {
  ts?: string;
  cases: number;
  answerable: number;
  unanswerable: number;
  hit_at_1: number | null;
  hit_at_3: number | null;
  mrr: number | null;
  keyword_acc: number | null;
  judge_correct: number | null;
  citation_acc: number | null;
  refusal_acc: number | null;
  cost_yuan: number;
  seconds: number;
  model: string;
  judge_model: string;
}
interface Row {
  id: string;
  question: string;
  answer: string;
  unanswerable: boolean;
  first_rank: number | null;
  keywords_ok: boolean;
  refused: boolean;
  verdict: string;
  reason: string;
}
interface Run {
  ts: string;
  summary: Summary;
  rows: Row[];
  history: Summary[];
}

const SETS = [
  { kind: 'rag', name: '作品集问答', desc: '右下角 AI 助手的资料：作品介绍、技术细节、常见问题' },
  {
    kind: 'ops-rag',
    name: '运维知识库',
    desc: '运维手册、架构说明、迁移手册，运维 Agent 用它回答「怎么做」',
  },
] as const;
const runs = ref<Record<string, Run | null>>({});
const loaded = ref(false);

onMounted(async () => {
  const out: Record<string, Run | null> = {};
  await Promise.all(
    SETS.map(async (s) => {
      try {
        out[s.kind] = await getJSON<Run>(`/api/ai/eval/latest?kind=${s.kind}`);
      } catch {
        out[s.kind] = null;
      }
    }),
  );
  runs.value = out;
  loaded.value = true;
});

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`);
const day = (iso: string | undefined) =>
  iso
    ? new Date(iso).toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
const metrics = (s: Summary) => [
  { k: '命中@1', v: pct(s.hit_at_1), tip: '应该找到的资料排在第 1' },
  { k: '命中@3', v: pct(s.hit_at_3), tip: '排在前 3' },
  { k: 'MRR', v: s.mrr == null ? '—' : s.mrr.toFixed(3), tip: '平均倒数排名' },
  { k: '要点命中', v: pct(s.keyword_acc), tip: '回答里有必须答到的要点' },
  { k: '裁判判对', v: pct(s.judge_correct), tip: '另一次模型调用按资料判对错' },
  { k: '拒答', v: pct(s.refusal_acc), tip: '资料里没有的题，老实说没有' },
];
const VERDICT: Record<string, string> = {
  correct: '裁判判对',
  partial: '部分对',
  wrong: '判错',
  error: '裁判出错',
};
/** 为什么算没答好：裁判判错、要点没答全、资料里没有却没说没有 */
const why = (r: Row) =>
  [
    VERDICT[r.verdict] ?? r.verdict,
    r.unanswerable
      ? r.refused
        ? '资料里没有'
        : '资料里没有却没说没有'
      : r.keywords_ok
        ? ''
        : '要点没答全',
  ]
    .filter(Boolean)
    .join(' · ');
const bad = (r: Run) =>
  r.rows.filter(
    (x) =>
      x.verdict !== 'correct' ||
      (!x.unanswerable && !x.keywords_ok) ||
      (x.unanswerable && !x.refused),
  );
</script>

<template>
  <p v-if="!loaded" class="text-sm text-slate-500">加载评测结果……</p>
  <div v-else class="space-y-8">
    <div
      v-for="s in SETS"
      :key="s.kind"
      class="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 sm:p-8"
    >
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h3 class="text-xl font-bold text-slate-900">{{ s.name }}</h3>
        <a
          :href="`/api/ai/eval/latest?kind=${s.kind}`"
          target="_blank"
          class="text-sm text-amber-700 hover:underline"
        >
          每道题的完整明细（JSON）↗
        </a>
      </div>
      <p class="mt-1 text-sm text-slate-500">{{ s.desc }}</p>

      <template v-if="runs[s.kind]">
        <p class="mt-4 text-xs text-slate-500">
          最近一次：{{ day(runs[s.kind]!.ts) }} · {{ runs[s.kind]!.summary.cases }} 道题（{{
            runs[s.kind]!.summary.unanswerable
          }}
          道资料里没有答案）· 回答 {{ runs[s.kind]!.summary.model }} · 裁判
          {{ runs[s.kind]!.summary.judge_model }} · ¥{{
            runs[s.kind]!.summary.cost_yuan.toFixed(3)
          }}
          · {{ runs[s.kind]!.summary.seconds }} 秒
        </p>
        <dl class="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-6">
          <div
            v-for="m in metrics(runs[s.kind]!.summary)"
            :key="m.k"
            class="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200"
            :title="m.tip"
          >
            <dt class="text-xs text-slate-500">{{ m.k }}</dt>
            <dd class="mt-1 font-mono text-lg font-bold text-slate-900">{{ m.v }}</dd>
          </div>
        </dl>

        <div v-if="runs[s.kind]!.history.length > 1" class="mt-6 overflow-x-auto">
          <p class="text-sm font-semibold text-slate-900">最近几次</p>
          <table class="mt-2 w-full text-left text-sm">
            <thead class="text-xs text-slate-500">
              <tr>
                <th class="py-1 pr-4 font-normal">时间</th>
                <th class="pr-4 font-normal">命中@1</th>
                <th class="pr-4 font-normal">要点</th>
                <th class="pr-4 font-normal">裁判判对</th>
                <th class="font-normal">拒答</th>
              </tr>
            </thead>
            <tbody class="font-mono text-slate-700">
              <tr
                v-for="h in runs[s.kind]!.history.slice(0, 6)"
                :key="h.ts"
                class="border-t border-slate-100"
              >
                <td class="py-1.5 pr-4 font-sans text-xs text-slate-500">{{ day(h.ts) }}</td>
                <td class="pr-4">{{ pct(h.hit_at_1) }}</td>
                <td class="pr-4">{{ pct(h.keyword_acc) }}</td>
                <td class="pr-4">{{ pct(h.judge_correct) }}</td>
                <td>{{ pct(h.refusal_acc) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-6">
          <p class="text-sm font-semibold text-slate-900">
            这一次没答好的题（{{ bad(runs[s.kind]!).length }} 道）
          </p>
          <p v-if="!bad(runs[s.kind]!).length" class="mt-2 text-sm text-emerald-700">全部答对。</p>
          <ul v-else class="mt-3 space-y-3">
            <li
              v-for="r in bad(runs[s.kind]!).slice(0, 5)"
              :key="r.id"
              class="rounded-xl bg-amber-50 p-4 text-sm ring-1 ring-amber-200"
            >
              <p class="font-semibold text-slate-900">
                {{ r.question }}
                <span class="ml-2 font-mono text-xs font-normal text-amber-800">{{ why(r) }}</span>
              </p>
              <p class="mt-1 text-slate-600">裁判：{{ r.reason }}</p>
            </li>
          </ul>
        </div>
      </template>
      <p v-else class="mt-4 text-sm text-slate-500">还没有评测结果。</p>
    </div>
  </div>
</template>
