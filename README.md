# AI 应用开发作品集（ops-desk）

个人作品集网站，线上地址：<https://ops.liuxd.dev>。代表作有三个，都能在线体验，状态如实标注：

- **运维 Agent**（`/ops-agent/`）：用只读工具查本站真实的服务器，告警时自动排查，低风险操作自动执行、关键操作等人在 Telegram 确认
- **ChatBI**（`/chatbi/`）：一句话查数据（Text-to-SQL），程序保证只读和数据权限；演示数据是虚构公司的销售记录
- **K8s + GitOps 平台**（`/live/`）：本站自己的部署和监控，页面展示集群的真实数据

另有右下角的作品集问答助手（RAG，资料在 `ai/knowledge/`）。网站文案集中在 `src/lib/site.ts`。

这也是一个 K8s / CI/CD 实战项目：静态站点按生产标准部署在单节点 k3s 上，推送到 main 后自动测试、构建镜像，由 Argo CD 从 Git 同步上线；新版本不健康时旧版本继续服务，回滚就是 git revert。集群里的所有组件（包括监控、证书、Argo CD 自己）都由 Git 管理。

- 架构与技术选型：[docs/architecture.md](docs/architecture.md)
- 部署、回滚、从零重建、排障：[docs/runbook.md](docs/runbook.md)（改哪些配置、提交后怎么生效，见第 1 节「改哪些配置、怎么生效」）
- 规格与里程碑：[SPEC.md](SPEC.md)

## 本地开发

需要 Node 22.23.4+（CI 和镜像用 Node 24）和 pnpm（版本见 `package.json` 的 `packageManager`）。

```bash
pnpm install
pnpm dev            # http://localhost:4321
```

提交前可以跑一遍和 CI 相同的检查：

```bash
pnpm format:check && pnpm lint && pnpm check && pnpm test && pnpm build
```

## AI 后端（ai/）

FastAPI + 自写轻量网关，模型走阿里云百炼。本地测试不需要密钥：

```bash
cd ai && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

部署和启用步骤见 `docs/runbook.md` 第 12 节。

## 平台实况（/live）

`deploy/k8s/base/status/` 下的 CronJob 每 10 分钟查询 K8s API 和 Prometheus，写进 ConfigMap `ops-desk-status`，站点 Pod 挂载后由 nginx 以 `/status.json` 提供。字段定义在 `src/lib/status.ts`，排障见 [docs/runbook.md](docs/runbook.md)「平台实况」。

## 目录

```
src/                 站点源码（Astro）
src/lib/site.ts      网站文案：个人定位、代表作、其他作品、技术栈
ai/                  AI 后端（FastAPI）；ai/knowledge/ 是 AI 助手回答所依据的作品资料
public/sw.js         清理旧 Service Worker 用，2026-12 后可删除
deploy/docker/       Dockerfile 和 nginx 配置
deploy/k8s/          Kustomize 清单（base + overlays/prod；base/status 是平台实况的 CronJob）
deploy/server/       服务器初始化、k3s、Argo CD 的安装脚本
deploy/platform/     集群基础组件（Argo CD、cert-manager、kube-prometheus-stack 的 HelmChart），由 Argo CD 同步
deploy/argocd/       Argo CD 应用定义（全部组件都从 Git 同步）
.github/workflows/   CI/CD 流水线
docs/                架构说明和运维手册
```
