# syntax=docker/dockerfile:1
# LiveSync Bridge（https://github.com/vrtmrz/livesync-bridge，Self-hosted LiveSync 作者写的）：
# 把 CouchDB 里 Obsidian 的笔记和普通文件目录双向同步，给个人知识库的 MCP 服务读写（deploy/apps/notes/）。
# 作者没有发布镜像，这里固定在一个看过、本地试跑过的提交上构建（2026-11-07）；升级时改这个提交并重新试跑。
FROM denoland/deno:2.9.7
ADD https://github.com/vrtmrz/livesync-bridge.git#1c252305bbddcacd200b8560d3bb786afe0906e9 /app
WORKDIR /app
RUN chown -R deno:deno /app && mkdir -p /deno-dir/location_data && chown deno:deno /deno-dir/location_data
USER deno
# 按 deno.lock 装依赖，运行时不再联网下载
RUN deno install --frozen
CMD ["deno", "run", "-A", "main.ts"]
