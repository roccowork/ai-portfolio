# CLAUDE.md

## 先读这些

- `README.md`、`docs/architecture.md`、`docs/runbook.md`：项目结构、架构和运维手册
- `SPEC.md`：最初的规格说明，其中 0.1 节是服务器和域名的环境（IP 等细节不写进这个公开仓库）

## 这个仓库是公开的

- 2026-10-29 从私有仓库 `roccowork/ops-desk` 拆出：网站、AI 后端、K8s 和监控配置在这里；私人助理（Telegram 机器人）和职业规划文档留在私有仓库
- 不要提交：服务器 IP、SSH 端口、密钥、个人健康和行程信息、所在地。示例数据用虚构的
- 两个仓库由同一个 Argo CD 部署，`deploy/argocd/applications.yaml` 里的 `personal` 应用指向私有仓库

## 协作约定

- 用中文回复，简短、抓重点
- 用户在对话里同意的改动直接提交并推送到 main，不用开分支和 PR（推送即触发 CI 和 GitOps 上线）。推送前必须跑通：`pnpm format:check && pnpm lint && pnpm check && pnpm test && pnpm build`；动了 AI 后端再跑 `cd ai && python -m pytest -q`
- 改了 `ai/app/gateway.py`、`ledger.py`、`telegram.py`、`config.py` 的接口时注意：私有仓库的私人助理复用它们（固定在某个镜像版本上，升级前要在那边跑测试）
- 只改根目录 `*.md`、`docs/archive/` 不会触发 CI；`docs/runbook.md`、`architecture.md`、`migrate-server.md` 打包进 AI 镜像做运维知识库，改了会重新构建上线（见 `.github/workflows/ci.yml`）
- Markdown 会被 Prettier 检查（`.prettierrc.json`），新增或修改 md 后运行 `pnpm format`
- 案例和作品的状态必须如实标注，演示数据要写明是演示
- 功能尽量做成真正有用的东西，用真实数据和真实文档（比如本仓库的运维手册、网站自己的运行数据），少用虚构的演示数据；确实只能用演示数据的，标清楚
