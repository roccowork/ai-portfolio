// 总控台的数据结构（后端 ai/app/console.py 的 build() 返回的就是这些）

export interface ConsoleNode {
  id: string;
  col: string;
  kind?: string;
  name: string;
  role?: string;
  desc: string;
  link?: string;
  status: string;
  lines: string[];
  detail?: string; // 点开后看的详情（/api/ai/console/detail 的 section）
}
export interface ConsoleEdge {
  from: string;
  to: string;
  label: string;
  state: 'flow' | 'idle' | 'bad';
}
export interface ConsoleAlert {
  level: 'bad' | 'warn';
  source: string;
  title: string;
  detail: string;
  since: string;
  heal?: string;
}
export interface ConsoleResource {
  id: string;
  group: string;
  name: string;
  status: string;
  lines: string[];
  detail?: string; // 点开后看的详情：/api/ai/console/detail 的 section（p: 开头的在私人助理那边）
}
export interface ConsoleData {
  generated: string;
  columns: { key: string; name: string }[];
  nodes: ConsoleNode[];
  edges: ConsoleEdge[];
  alerts: ConsoleAlert[];
  resource_groups: { key: string; name: string }[];
  resources: ConsoleResource[];
  pages: { key: string; name: string; path: string }[]; // 私人助理的网页，做成标签页（iframe）
  summary: {
    staff: number;
    staff_ok: number;
    problems: string[];
    flows: number;
    alerts: number;
    alerts_bad: number;
    resources: number;
    resources_bad: number;
    cost_today: number;
  };
}

// 状态 → [中文, 样式]
export const STATUS: Record<string, [string, string]> = {
  ok: ['正常', 'ok'],
  warn: ['需关注', 'warn'],
  bad: ['异常', 'bad'],
  idle: ['空闲', 'idle'],
  manual: ['手动', 'manual'],
  paused: ['暂停', 'idle'],
  unknown: ['查不到', 'idle'],
};
