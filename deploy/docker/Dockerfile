# 构建阶段：用 Node 生成静态文件
FROM node:24.21.0-alpine AS build
WORKDIR /app
RUN npm install -g pnpm@12.11.2
# 先只复制依赖清单，依赖不变时可复用这一层缓存
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# 构建时写入 commit SHA：CI 访问线上的 /version.txt 确认新版本已经上线（不需要访问集群）
ARG GIT_SHA=dev
RUN pnpm build && echo "$GIT_SHA" > dist/version.txt

# 运行阶段：非 root 的 Nginx（uid 101，监听 8080），只包含静态文件
FROM nginxinc/nginx-unprivileged:1.30.5-alpine
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
