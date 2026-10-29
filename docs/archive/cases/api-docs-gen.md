---
title: 'API 文档自动生成：代码改了，文档跟着变'
summary: '从代码注解生成 OpenAPI 文档，每次合并自动发布文档站点，并检查接口是否有不兼容的变更。'
category: dev
status: design
pains: [accuracy, speed]
value: '文档和代码永远一致，不兼容变更合并前就被发现'
ai: boost
stack: ['OpenAPI', 'Redoc / Swagger UI', 'oasdiff', 'GitHub Actions']
order: 30
flow:
  - label: '代码 + 注解'
  - label: '生成 openapi.json'
  - label: 'oasdiff'
    detail: '检查不兼容变更'
  - label: '发布文档站点'
  - label: '前端和对接方查看'
related: [ai-code-review, api-test-loadtest]
tags: ['API 文档', 'OpenAPI', 'Swagger', '接口']
---

## 问题

接口文档靠手写的 Word 或 Wiki，代码改了文档没改，对接方按旧文档开发，联调时才发现字段变了。

## 适用场景

- 适合：前后端分离、有外部对接方的项目
- 不值得：只有内部一两个人调用的简单接口
- 前提：后端框架支持生成 OpenAPI

## 工具选型

| 方案                               | 特点             | 适用         |
| ---------------------------------- | ---------------- | ------------ |
| 框架自带生成（FastAPI、SpringDoc） | 零额外维护       | 国内外都常用 |
| Redoc / Swagger UI                 | 渲染文档         | 开源         |
| Apifox / YApi                      | 国产接口管理平台 | 国内常用     |
| Postman                            | 接口协作         | 外企常用     |

文档从代码生成才能保证一致；渲染用 Redoc 生成静态页面，托管简单。

## 怎么做

- 后端框架从代码和注解生成 OpenAPI 规范（FastAPI、Spring Doc、NestJS 都支持）
- CI 里导出 openapi.json，用 oasdiff 和主分支对比，删除字段、修改类型等不兼容变更直接标红
- 合并后用 Redoc 生成静态文档站点自动发布
- 前端可以用同一份规范自动生成请求代码和 Mock 数据

## 实施步骤

| 步骤 | 做什么                            | 怎么验证              |
| ---- | --------------------------------- | --------------------- |
| 1    | 开启框架的 OpenAPI 生成，补全注解 | 能访问 /openapi.json  |
| 2    | CI 导出规范文件                   | 产物里有 openapi.json |
| 3    | oasdiff 检查不兼容变更            | 故意删字段时 CI 失败  |
| 4    | 生成静态文档并发布                | 文档站点更新          |
| 5    | 前端用规范生成请求代码            | 手写的请求代码减少    |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
- run: python -c "import json, app; json.dump(app.app.openapi(), open('openapi.json', 'w'))"
- run: oasdiff breaking origin-openapi.json openapi.json --fail-on ERR
- run: npx @redocly/cli build-docs openapi.json -o site/index.html
```

## 效果指标

| 指标           | 做之前     | 预期效果（估算） |
| -------------- | ---------- | ---------------- |
| 文档和代码一致 | 经常不一致 | 始终一致         |
| 联调问题       | 字段对不上 | 提前发现         |
| 维护文档       | 手工更新   | 0                |

文档永远和代码一致，不兼容的接口变更在合并前被发现。

## 常见坑

- **生成的文档没有说明**：注解没写描述和示例。注解里写清字段含义和示例，代码审查时检查。
- **确实需要不兼容变更**：业务调整。升级接口版本（/v2），旧版本保留一段时间。
- **内部接口被公开文档暴露**：文档站点没有访问控制。内部文档放在内网或加认证。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：给缺少描述的接口和字段补写说明和示例
- **不该交给 AI**：规范生成和兼容性检查必须确定
- **怎么落地**：AI 生成的描述作为 PR 建议，由开发确认

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会让后端从代码和注解生成 OpenAPI 规范，CI 导出 openapi.json 后用 oasdiff 和主分支对比，删除字段、改类型这类不兼容变更直接让 CI 失败；合并后用 Redoc 生成静态文档自动发布，前端用同一份规范生成请求代码。文档从代码来，就永远不会过时。

常见追问：

**问：什么是不兼容的接口变更？**

答：删除字段、修改类型、把可选参数改成必填、删除接口；新增可选字段一般是兼容的。

**问：OpenAPI 和 Swagger 的关系？**

答：Swagger 是 OpenAPI 规范的前身和一套工具；现在规范叫 OpenAPI，Swagger UI 是渲染工具之一。

**问：接口版本怎么管理？**

答：URL 加版本号（/v1、/v2）或请求头指定版本，旧版本设下线时间并通知调用方。

**被问到「能不能用 AI」时：** 文档结构从代码生成，AI 帮忙补字段说明和示例。
