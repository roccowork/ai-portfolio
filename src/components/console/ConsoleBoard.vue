<script setup lang="ts">
// 总控台：我所有的项目和资源都在这一个地方看（查看类内容只在这里，Telegram 只推送和操作）。
// 标签页：概览、告警中心、资源中心、数字员工，加私人助理的网页（「数据流」关系图 2026-11-22 删了：信息和数字员工重复，用不上）。数据来自 /api/ai/console，30 秒刷新一次；
// 点开资源、员工的详情走 /api/ai/console/detail（原来值班台机器人按钮的内容）。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import TgHtml from './TgHtml.vue';
import {
  canFullscreen,
  inBrowser,
  initAuth,
  logout,
  post,
  saveKey,
  startTab,
  toggleFullscreen,
} from './api';
import { beep, setSound, soundOn } from './sound';
import { STATUS, type ConsoleData, type ConsoleNode, type ConsoleResource } from './types';

const BASE_TABS = [
  { key: 'home', name: '概览' },
  { key: 'alerts', name: '告警中心' },
  { key: 'resources', name: '资源中心' },
  { key: 'staff', name: '数字员工' },
];
type Tab = string;

const data = ref<ConsoleData | null>(null);
const error = ref('');
const needLogin = ref(false);
const sound = ref(soundOn());
let seenAlerts: Set<string> | null = null; // 第一次加载不响，之后新出现的告警才响
const browser = ref(false);
const keyInput = ref('');
const keyWrong = ref(false);
const tab = ref<Tab>('home');
const panel = ref<{ title: string; html: string; loading: boolean; error: string } | null>(null);
let timer: ReturnType<typeof setInterval> | undefined;

// 私人助理的网页（吃药打卡等，名字由私人助理给）排在数字员工后面
const TABS = computed(() => [
  ...BASE_TABS,
  ...(data.value?.pages || []).map((p) => ({ key: p.key, name: p.name })),
]);
const page = computed(() => (data.value?.pages || []).find((p) => p.key === tab.value));
// 暂停的项目排在最后、变灰
const staff = computed(() =>
  (data.value?.nodes || [])
    .filter((n) => n.kind === 'staff')
    .sort((a, b) => Number(a.status === 'paused') - Number(b.status === 'paused')),
);
const groups = computed(() => {
  const d = data.value;
  if (!d) return [];
  return d.resource_groups
    .map((g) => ({ ...g, items: d.resources.filter((r) => r.group === g.key) }))
    .filter((g) => g.items.length);
});
// 概览「需要看一眼」：告警在前，再是状态不对的员工
const attention = computed(() => {
  const d = data.value;
  if (!d) return [];
  const out = d.alerts.slice(0, 6).map((a) => ({
    level: a.level,
    text: `${a.title}${a.detail ? '：' + a.detail : ''}`,
    tab: 'alerts' as Tab,
  }));
  for (const n of staff.value)
    if (n.status === 'bad' || n.status === 'warn')
      out.push({
        level: n.status,
        text: `数字员工「${n.name}」${STATUS[n.status][0]}`,
        tab: 'staff',
      });
  return out;
});

const fullscreen = ref(false);

function setTab(t: Tab) {
  tab.value = t;
  const q = new URLSearchParams(location.search);
  q.set('t', t);
  history.replaceState(null, '', `${location.pathname}?${q.toString()}${location.hash}`);
}

function fmt(iso: string) {
  // 固定 UTC+8（和后端、值班台一致）。不用设备时区：电脑可能设成别的时区（2026-11-22 浏览器里显示成了美国时间）；公开仓库不写所在地
  return new Date(iso).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

async function openDetail(item: ConsoleResource | ConsoleNode) {
  if (!item.detail) return;
  panel.value = { title: item.name, html: '', loading: true, error: '' };
  try {
    const r = await post<{ html: string }>('/api/ai/console/detail', { section: item.detail });
    if (panel.value) panel.value = { ...panel.value, html: r.html, loading: false };
  } catch (e) {
    if ((e as Error).message === 'login') {
      panel.value = null;
      return showLogin(true);
    }
    if (panel.value) panel.value = { ...panel.value, loading: false, error: (e as Error).message };
  }
}

async function load() {
  try {
    data.value = await post<ConsoleData>('/api/ai/console');
    error.value = '';
    const now = data.value.alerts.map((a) => `${a.source}|${a.title}`);
    const fresh = seenAlerts ? data.value.alerts.filter((_, i) => !seenAlerts!.has(now[i])) : [];
    seenAlerts = new Set(now);
    if (sound.value && fresh.length) beep(fresh.some((a) => a.level === 'bad'));
    if (!TABS.value.some((t) => t.key === tab.value)) tab.value = 'home';
  } catch (e) {
    if ((e as Error).message === 'login') return showLogin(true);
    error.value = (e as Error).message;
  }
}

// 浏览器打开：输私人密钥（和需求雷达上报同一个，后端比对）
function showLogin(wrong = false) {
  clearInterval(timer);
  keyWrong.value = wrong; // 后端打回来的：密钥不对或换过了
  data.value = null;
  needLogin.value = true;
}

function submitKey() {
  if (!keyInput.value.trim()) return;
  saveKey(keyInput.value);
  keyInput.value = '';
  needLogin.value = false;
  start();
}

function toggleSound() {
  sound.value = !sound.value;
  setSound(sound.value);
}

function signOut() {
  logout();
  showLogin();
}

function start() {
  load();
  clearInterval(timer);
  timer = setInterval(load, 30000);
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') panel.value = null;
}

onMounted(() => {
  const err = initAuth();
  const t = startTab();
  if (/^[a-z][a-z-]{0,25}$/.test(t)) tab.value = t; // 私人标签页要等数据回来才知道，先记下
  fullscreen.value = canFullscreen();
  browser.value = inBrowser();
  window.addEventListener('keydown', onKey);
  if (err === 'login') return showLogin();
  if (err) {
    error.value = err;
    return;
  }
  start();
});
onBeforeUnmount(() => {
  clearInterval(timer);
  window.removeEventListener('keydown', onKey);
});
</script>

<template>
  <div class="mx-auto max-w-[1400px] px-4 pt-5 pb-16 sm:px-6">
    <header class="flex flex-wrap items-center gap-3">
      <img src="/favicon.svg?v=4" alt="" width="36" height="36" class="size-9" />
      <div class="min-w-0">
        <h1 class="text-lg font-semibold tracking-wide">向晓 AI · 总控台</h1>
        <p class="text-xs text-slate-400">所有告警、资源、数字员工都在这里看（UTC+8）</p>
      </div>
      <span v-if="data" class="ml-auto text-xs text-slate-500"
        >更新于 {{ fmt(data.generated) }}</span
      >
      <button
        v-if="fullscreen"
        class="rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300"
        @click="toggleFullscreen"
      >
        ⛶ 全屏
      </button>
      <button
        v-if="data"
        class="rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300"
        :title="sound ? '出现新告警时响铃（点一下关掉）' : '点一下打开：出现新告警时响铃'"
        @click="toggleSound"
      >
        {{ sound ? '🔔 声音开' : '🔕 声音关' }}
      </button>
      <button
        v-if="browser && data"
        class="rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300"
        @click="signOut"
      >
        退出
      </button>
    </header>

    <nav class="tabs mt-4" role="tablist">
      <button
        v-for="t in TABS"
        :key="t.key"
        role="tab"
        :aria-selected="tab === t.key"
        :class="{ on: tab === t.key }"
        @click="setTab(t.key)"
      >
        {{ t.name }}
        <span v-if="t.key === 'alerts' && data?.summary.alerts" class="count">{{
          data.summary.alerts
        }}</span>
      </button>
    </nav>

    <p v-if="error" class="mt-6 rounded-xl bg-red-500/10 p-4 text-red-300 ring-1 ring-red-500/30">
      {{ error }}
    </p>
    <form
      v-if="needLogin"
      class="mx-auto mt-10 flex max-w-sm flex-col gap-3 text-center"
      @submit.prevent="submitKey"
    >
      <p class="text-slate-300">输入私人密钥（存在这台电脑的浏览器里，下次不用再输）</p>
      <input
        v-model="keyInput"
        type="password"
        autocomplete="current-password"
        class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
        placeholder="密钥"
      />
      <p v-if="keyWrong" class="text-xs text-red-300">密钥不对，或者已经换过了</p>
      <button class="rounded-lg bg-amber-500 px-3 py-2 font-medium text-slate-900">
        打开总控台
      </button>
      <p class="text-xs text-slate-500">也可以在值班台机器人里点「总控台」直接打开</p>
    </form>
    <p v-else-if="!data && !error" class="mt-10 text-slate-400">加载中……</p>

    <template v-if="data">
      <!-- 概览 -->
      <section v-if="tab === 'home'" class="mt-5 space-y-5">
        <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <button
            class="tile"
            :class="data.summary.alerts_bad ? 'bad' : data.summary.alerts ? 'warn' : 'ok'"
            @click="setTab('alerts')"
          >
            <span>告警</span><b>{{ data.summary.alerts }}</b
            ><small>{{
              data.summary.alerts_bad ? `${data.summary.alerts_bad} 条异常` : '没有异常'
            }}</small>
          </button>
          <button
            class="tile"
            :class="data.summary.resources_bad ? 'warn' : 'ok'"
            @click="setTab('resources')"
          >
            <span>资源</span><b>{{ data.summary.resources }}</b
            ><small>{{
              data.summary.resources_bad ? `${data.summary.resources_bad} 个要看` : '全部正常'
            }}</small>
          </button>
          <button
            class="tile"
            :class="data.summary.staff_ok < data.summary.staff ? 'warn' : 'ok'"
            @click="setTab('staff')"
          >
            <span>数字员工在岗</span><b>{{ data.summary.staff_ok }}/{{ data.summary.staff }}</b
            ><small>{{
              data.summary.staff_ok < data.summary.staff ? '有员工需关注' : '全部正常'
            }}</small>
          </button>
          <button class="tile ok" @click="setTab('resources')">
            <span>网站 AI 今天花费</span><b>¥{{ data.summary.cost_today.toFixed(2) }}</b
            ><small>模型网关账本</small>
          </button>
        </div>
        <div class="box">
          <h2 class="h2">需要看一眼</h2>
          <p v-if="!attention.length" class="text-sm text-emerald-300">一切正常 ✅</p>
          <ul v-else class="divide-y divide-slate-800">
            <li v-for="(a, i) in attention" :key="i">
              <button class="row" @click="setTab(a.tab)">
                <i class="dot" :class="a.level"></i>
                <span class="flex-1 text-left">{{ a.text }}</span>
                <span class="text-slate-500">›</span>
              </button>
            </li>
          </ul>
        </div>
      </section>

      <!-- 告警中心 -->
      <section v-if="tab === 'alerts'" class="mt-5 space-y-4">
        <div class="box">
          <div class="flex items-center gap-3">
            <h2 class="h2 mb-0">所有告警（{{ data.alerts.length }}）</h2>
            <button
              class="ml-auto text-xs text-amber-300 hover:underline"
              @click="openDetail({ name: '值班员自愈记录', detail: 'heal' } as ConsoleNode)"
            >
              值班员自愈记录 ›
            </button>
          </div>
          <p v-if="!data.alerts.length" class="mt-3 text-sm text-emerald-300">没有告警 ✅</p>
          <table v-else class="alerts mt-3">
            <thead>
              <tr>
                <th>级别</th>
                <th>告警</th>
                <th>来源</th>
                <th>开始</th>
                <th>值班员处理</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(a, i) in data.alerts" :key="i">
                <td>
                  <span class="lv" :class="a.level">{{ a.level === 'bad' ? '异常' : '注意' }}</span>
                </td>
                <td>
                  <b>{{ a.title }}</b>
                  <p v-if="a.detail" class="text-xs text-slate-400">{{ a.detail }}</p>
                </td>
                <td class="text-slate-300">{{ a.source }}</td>
                <td class="text-slate-400">{{ a.since }}</td>
                <td class="text-slate-300">{{ a.heal }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="box">
          <h2 class="h2">最近 24 小时已消失（{{ data.recovered?.length || 0 }}）</h2>
          <p v-if="!data.recovered?.length" class="text-sm text-slate-400">没有</p>
          <table v-else class="alerts mt-3">
            <thead>
              <tr>
                <th>级别</th>
                <th>告警</th>
                <th>开始</th>
                <th>恢复</th>
                <th>持续</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(a, i) in data.recovered" :key="i">
                <td>
                  <span class="lv" :class="a.level">{{
                    a.level === 'bad' ? '异常' : a.level === 'warn' ? '注意' : '一闪而过'
                  }}</span>
                </td>
                <td>
                  <b>{{ a.title }}</b>
                  <p class="text-xs text-slate-400">
                    {{ a.detail }}{{ a.detail ? ' · ' : '' }}{{ a.pushed ? '已推送' : '没推送' }}
                  </p>
                </td>
                <td class="text-slate-400">{{ a.start }}</td>
                <td class="text-slate-400">{{ a.end }}</td>
                <td class="text-slate-300">{{ a.minutes }} 分钟</td>
              </tr>
            </tbody>
          </table>
          <p class="mt-2 text-xs text-slate-500">
            只含 Prometheus 告警（来自它自己存的历史）；拨测、Argo
            CD、备份、私人助理的问题恢复后不在这里列
          </p>
        </div>
        <p class="text-xs text-slate-500">
          来源：Prometheus 告警、外部拨测、Argo
          CD、备份、私人助理（VPS、余额等）。告警一响，值班台机器人会推到
          Telegram，需要确认的操作在推送消息里点按钮。
        </p>
      </section>

      <!-- 资源中心 -->
      <section v-if="tab === 'resources'" class="mt-5 space-y-6">
        <div v-for="g in groups" :key="g.key">
          <h2 class="h2">{{ g.name }}</h2>
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <component
              :is="r.detail ? 'button' : 'div'"
              v-for="r in g.items"
              :key="r.id"
              class="card"
              :class="[`st-${STATUS[r.status]?.[1] || 'idle'}`, { click: r.detail }]"
              @click="openDetail(r)"
            >
              <div class="flex items-center gap-2">
                <b class="truncate">{{ r.name }}</b>
                <span class="badge">{{ STATUS[r.status]?.[0] || r.status }}</span>
                <span v-if="r.detail" class="ml-auto text-slate-500">详情 ›</span>
              </div>
              <ul v-if="r.lines.length" class="mt-1.5 space-y-0.5 text-xs text-slate-300">
                <li v-for="(l, i) in r.lines" :key="i">{{ l }}</li>
              </ul>
            </component>
          </div>
        </div>
      </section>

      <!-- 数字员工 -->
      <section v-if="tab === 'staff'" class="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="n in staff"
          :key="n.id"
          class="card"
          :class="[
            `st-${STATUS[n.status]?.[1] || 'idle'}`,
            { 'opacity-50': n.status === 'paused' },
          ]"
        >
          <div class="flex items-start gap-3">
            <span class="avatar">{{ n.name.slice(0, 1) }}</span>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <b class="truncate">{{ n.name }}</b>
                <span class="badge">{{ STATUS[n.status]?.[0] || n.status }}</span>
              </div>
              <p v-if="n.role" class="text-xs text-amber-200/80">{{ n.role }}</p>
            </div>
          </div>
          <p class="mt-2 text-xs leading-relaxed text-slate-400">{{ n.desc }}</p>
          <ul v-if="n.lines.length" class="mt-2 space-y-0.5 text-xs text-slate-200">
            <li v-for="(l, i) in n.lines" :key="i">· {{ l }}</li>
          </ul>
          <div v-if="n.detail || n.link" class="mt-2 flex gap-4 text-xs">
            <button v-if="n.detail" class="text-amber-300 hover:underline" @click="openDetail(n)">
              工作记录 ›
            </button>
            <a
              v-if="n.link"
              :href="n.link"
              target="_blank"
              rel="noopener"
              class="text-slate-400 hover:underline"
              >打开工具 ↗</a
            >
          </div>
        </div>
      </section>

      <!-- 私人网页（页面在私人助理那边，同一个域名，用总控台的 Telegram 身份） -->
      <section v-if="page" class="mt-4">
        <iframe :key="page.key" :src="page.path" :title="page.name" class="frame"></iframe>
      </section>
    </template>

    <!-- 详情：手机上全屏，电脑上右侧抽屉 -->
    <div v-if="panel" class="mask" @click.self="panel = null">
      <aside class="drawer" role="dialog" :aria-label="panel.title">
        <div class="flex items-center gap-3 border-b border-slate-800 pb-3">
          <b>{{ panel.title }}</b>
          <button
            class="ml-auto px-2 text-xl text-slate-400"
            aria-label="关闭"
            @click="panel = null"
          >
            ×
          </button>
        </div>
        <p v-if="panel.loading" class="mt-4 text-slate-400">加载中……</p>
        <p v-if="panel.error" class="mt-4 text-red-300">{{ panel.error }}</p>
        <TgHtml v-if="panel.html" :html="panel.html" class="mt-4" />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  flex-wrap: wrap; /* 小程序窗口窄：换行，不横向滚动（滚动条看不出来，后面的标签像没了） */
  gap: 4px;
  border-bottom: 1px solid rgb(30 41 59);
}
.tabs button {
  flex-shrink: 0;
  outline: none;
  padding: 8px 14px;
  font-size: 14px;
  color: rgb(148 163 184);
  border-bottom: 2px solid transparent;
}
.tabs button.on {
  color: #fde68a;
  border-color: #fbbf24;
}
.count {
  margin-left: 4px;
  border-radius: 999px;
  background: rgb(251 191 36 / 0.2);
  padding: 0 6px;
  font-size: 11px;
  color: #fbbf24;
}
.h2 {
  margin-bottom: 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.2em;
  color: #fbbf24;
}
.box {
  border-radius: 14px;
  border: 1px solid rgb(30 41 59);
  background: rgb(14 21 36 / 0.92);
  padding: 14px 16px;
}
.tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-radius: 14px;
  border: 1px solid rgb(30 41 59);
  background: rgb(14 21 36 / 0.92);
  padding: 12px 14px;
  text-align: left;
}
.tile span {
  font-size: 12px;
  color: rgb(148 163 184);
}
.tile b {
  font-size: 26px;
  line-height: 1.2;
}
.tile small {
  font-size: 12px;
  color: rgb(148 163 184);
}
.tile.ok b {
  color: #34d399;
}
.tile.warn b {
  color: #fbbf24;
}
.tile.bad b {
  color: #f87171;
}
.row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  font-size: 14px;
}
.dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #fbbf24;
}
.dot.bad {
  background: #f87171;
}
.alerts {
  width: 100%;
  font-size: 13px;
  border-collapse: collapse;
}
.alerts th {
  padding: 6px 8px;
  text-align: left;
  font-weight: 500;
  font-size: 12px;
  color: rgb(100 116 139);
  border-bottom: 1px solid rgb(30 41 59);
}
.alerts td {
  padding: 8px;
  vertical-align: top;
  border-bottom: 1px solid rgb(30 41 59 / 0.6);
}
.alerts td:empty::after {
  content: '—';
  color: rgb(71 85 105);
}
/* 手机上表格改成一条一张卡片，空的栏不显示 */
@media (max-width: 767px) {
  .alerts thead {
    display: none;
  }
  .alerts tr {
    display: block;
    padding: 8px 0;
    border-bottom: 1px solid rgb(30 41 59);
  }
  .alerts td {
    display: block;
    padding: 2px 0;
    border: 0;
  }
  .alerts td:empty {
    display: none;
  }
  .alerts td:nth-child(n + 3):not(:empty)::before {
    color: rgb(100 116 139);
    margin-right: 6px;
  }
  .alerts td:nth-child(3)::before {
    content: '来源';
  }
  .alerts td:nth-child(4)::before {
    content: '开始';
  }
  .alerts td:nth-child(5)::before {
    content: '处理';
  }
}
.lv {
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
  white-space: nowrap;
  background: rgb(251 191 36 / 0.15);
  color: #fbbf24;
}
.lv.bad {
  background: rgb(248 113 113 / 0.15);
  color: #f87171;
}
.lv.flash {
  background: rgb(148 163 184 / 0.15);
  color: #94a3b8;
}
.card {
  display: block;
  width: 100%;
  border-radius: 14px;
  border: 1px solid rgb(30 41 59);
  background: rgb(14 21 36 / 0.92);
  padding: 12px 14px;
  text-align: left;
  transition: border-color 0.2s;
}
.card.click:hover {
  border-color: rgb(251 191 36 / 0.6);
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
.st-bad .avatar {
  background: #f87171;
}
.st-idle .avatar {
  background: #475569;
}
.frame {
  display: block;
  width: 100%;
  height: calc(100vh - 130px);
  min-height: 480px;
  border: 1px solid rgb(30 41 59);
  border-radius: 14px;
  background: #fff;
}
.mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  justify-content: flex-end;
  background: rgb(2 6 23 / 0.6);
}
.drawer {
  width: 100%;
  max-width: 720px;
  height: 100%;
  overflow-y: auto;
  background: #0b1220;
  border-left: 1px solid rgb(30 41 59);
  padding: 16px 18px 40px;
}
</style>
