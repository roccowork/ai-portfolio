/**
 * 网站文案集中在这里：个人定位、代表作、其他作品、技术栈。
 * 改作品介绍时，同步改 ai/knowledge/ 下对应的文档（右下角的 AI 助手按那里的内容回答访客）。
 * 作品状态必须如实：真实环境在跑的写 production，用虚构数据的写 demo。
 */

export const BRAND = {
  name: 'Applied AI Lab',
  tagline: 'AI 应用开发作品集',
};

export const PROFILE = {
  role: 'AI 应用开发工程师',
  headline: '会 Python 开发、懂数据、能独立部署上线的 AI 应用工程师',
  intro:
    '11 年制造业 IT 背景，做 Agent、RAG、Text-to-SQL 这类能在企业里实际用起来的 AI 应用。这个网站本身就是作品：下面的演示都调用真实的大模型，跑在我自己搭建和运维的 K8s 集群上。',
  /** 留空时不显示 */
  github: 'https://github.com/roccowork/ai-portfolio',
  resume: '',
};

/** 顶部导航 */
export const NAV = [
  { name: '代表作', href: '/#projects' },
  { name: '值班大屏', href: '/ops-agent/' },
  { name: '技术栈', href: '/#skills' },
  { name: '关于我', href: '/about/' },
];

export type WorkStatus = 'production' | 'demo';

export const STATUS_LABEL: Record<WorkStatus, string> = {
  production: '真实环境运行中',
  demo: '演示数据',
};

export interface Work {
  id: string;
  name: string;
  /** 一两句话：做了什么 */
  summary: string;
  status: WorkStatus;
  /** 工程上的关键点，面试时可以展开讲 */
  highlights: string[];
  stack: string[];
  /** 在线演示：http 开头的在新窗口打开；chat 表示打开右下角的 AI 助手 */
  demo?: { href: string; label: string; hint?: string } | 'chat';
}

export const PROJECTS: Work[] = [
  {
    id: 'ops-agent',
    name: '运维 Agent：告警自动排查与自愈',
    summary:
      '给大模型一组只读工具，让它自己查 Prometheus 告警、K8s 的 Pod 和事件，给出结论和依据。告警触发时自动排查，从预先写好的操作清单里选处理办法：低风险的直接执行，关键操作推到 Telegram 等人确认，执行后检查有没有真的恢复。',
    status: 'production',
    highlights: [
      '函数调用 + 多轮工具循环，模型自己决定查什么，最多 8 轮；问怎么操作时用 RAG 查本站的运维手册，按手册回答并注明出处',
      '安全边界靠程序：只读的 K8s 账号、命名空间白名单、日志先脱敏再交给模型',
      '自愈只能从 3 种操作里选，参数由代码校验；同一个对象 6 小时内只自动处理一次，没好就交给人',
    ],
    stack: [
      'Python',
      'FastAPI',
      'Function Calling',
      'Prometheus',
      'Kubernetes API',
      'Telegram Bot',
    ],
    demo: {
      href: '/ops-agent/',
      label: '值班大屏：实时状态 + 在线提问',
      hint: '它会查本站真实运行的服务器，每一步实时显示',
    },
  },
  {
    id: 'rag',
    name: 'RAG 知识库问答：带评测的检索增强',
    summary:
      '两个知识库：作品集问答（右下角的 AI 助手）和运维手册问答（运维 Agent 的工具）。自己写代码实现切块、向量 + 关键词混合检索、引用出处和拒答，用 61 道题的评测集衡量效果，按评测结果一轮轮改进。',
    status: 'production',
    highlights: [
      '混合检索：向量对 rateLimited 这类专有名词不敏感，加上 BM25 关键词检索、用 RRF 融合两路结果，运维手册命中@1 从 70% 提到 85.7%',
      '评测分检索和生成：命中@1 / @3、MRR、要点命中、LLM 裁判、引用准确；还有 10 道资料里没有答案的题，专测会不会编',
      '按标题切块，代码块和表格不拆开；向量按内容哈希缓存，文档改了只重算变化的段落',
    ],
    stack: ['Python', 'Embedding', 'BM25', 'RRF', 'LLM 裁判', '评测集'],
    demo: {
      href: '/rag/',
      label: '看评测结果和改进过程',
      hint: '最近一次评测的真实分数，每道题的回答都能查',
    },
  },
  {
    id: 'chatbi',
    name: 'ChatBI：一句话查数据',
    summary:
      '用中文问本站自己的运行数据：AI 调用和费用、告警自愈、评测分数、访问量、发布记录。模型写 SQL，程序在只读、受限的连接上执行，再画图并用一两句话解读。',
    status: 'production',
    highlights: [
      '语义层：表结构、「费用」「成功率」等口径和「今天」「本周」对应的真实日期写进提示词，避免每次问出不一样的数',
      'SQL 安全靠程序：只读连接、只放行查询、最多 200 行、超过 2 秒中断；执行报错时把报错交给模型改一次',
      '数据权限在数据层：每 10 分钟从账本、自愈记录、Prometheus 生成只含统计表的快照，访客留言和联系方式不复制进来，SQL 怎么写都查不到',
    ],
    stack: ['Python', 'Text-to-SQL', 'SQLite', 'FastAPI', 'Vue'],
    demo: {
      href: '/chatbi/',
      label: '在线提问',
      hint: '本站真实运行数据，每 10 分钟更新',
    },
  },
  {
    id: 'gitops',
    name: 'K8s + GitOps 平台：本站的部署和监控',
    summary:
      '单节点 k3s 上按生产标准部署：推送到 main 后 CI 自动测试、构建镜像，Argo CD 从 Git 同步上线。监控、证书、Argo CD 自己也都由 Git 管理。',
    status: 'production',
    highlights: [
      'CI 不持有集群凭据，只把新镜像标签写回 Git；回滚就是 git revert',
      '就绪探针通过才接流量，新版本不健康时旧版本继续服务',
      'kube-prometheus-stack、Grafana、Uptime Kuma 监控，告警推 Telegram 并交给运维 Agent 排查；数据用 restic 每天加密备份到 R2',
    ],
    stack: [
      'k3s',
      'Argo CD',
      'GitHub Actions',
      'Kustomize',
      'Prometheus',
      'cert-manager',
      'restic',
    ],
    demo: {
      href: '/gitops/',
      label: '看部署流程和真实配置',
      hint: '从 git push 到上线、到一键回滚，每一步都链接到仓库里的配置',
    },
  },
];

export const OTHER_WORKS: Work[] = [
  {
    id: 'kb-agent',
    name: 'AI 客服 Agent（Dify）',
    summary:
      '虚构的净水器公司「清源净水」的客服：知识库回答产品问题，调用自定义工具查订单、预约上门，工具接口由本站的 AI 后端提供。',
    status: 'demo',
    highlights: [],
    stack: ['Dify', 'RAG', '自定义工具'],
    demo: {
      href: 'https://dify.liuxd.dev/chat/X2llFSUsp10ASkzl',
      label: '在线体验',
      hint: '可以查订单 QY1001～QY1005，无需注册',
    },
  },
  {
    id: 'gateway',
    name: '轻量模型网关',
    summary:
      '本站所有模型调用都经过它：按 IP 限次数、全站和访客分开的每日费用上限、失败重试、按 token 记账、Prometheus 指标。',
    status: 'production',
    highlights: [],
    stack: ['Python', 'httpx', 'SQLite', 'Prometheus'],
    demo: {
      href: '/gateway/',
      label: '实时用量和额度',
      hint: '今天花了多少、还剩多少，读账本不调用模型',
    },
  },
];

export const SKILLS = [
  {
    group: 'AI 应用',
    items: ['RAG', 'Function Calling / Agent', 'Text-to-SQL', 'Dify', '提示词设计'],
  },
  { group: '后端', items: ['Python', 'FastAPI', 'SQLite', 'pytest'] },
  {
    group: '部署与运维',
    items: ['Docker', 'k3s / Kubernetes', 'Argo CD', 'GitHub Actions', 'Prometheus / Grafana'],
  },
  { group: '前端', items: ['Astro', 'Vue', 'Tailwind CSS'] },
];
