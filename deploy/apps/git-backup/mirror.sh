#!/bin/sh
# 把每个 GitHub 账号的全部仓库（含私有）镜像到 /data/<账号>/<仓库>.git：第一次完整克隆，以后只拉新的提交。
# 令牌来自 Secret github-backup，一个账号一个键（键名随意），挂载在 /tokens；只需要 Contents 和 Metadata 的只读权限。
# GitHub 上删掉的仓库不会从这里删除，备份里一直保留。
set -eu

total=0
failed=""
for f in /tokens/*; do
  token=$(cat "$f")
  # 令牌只放在请求头里，不写进镜像的远程地址，备份里不会出现令牌
  auth=$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')
  : > /tmp/repos
  page=1
  while :; do
    body=$(wget -qO- --header "Authorization: Bearer $token" --header "Accept: application/vnd.github+json" \
      "https://api.github.com/user/repos?affiliation=owner&per_page=100&page=$page")
    names=$(printf '%s' "$body" | grep -o '"full_name": *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/' || true)
    [ -n "$names" ] || break
    printf '%s\n' "$names" >> /tmp/repos
    page=$((page + 1))
  done
  if [ ! -s /tmp/repos ]; then
    echo "令牌 $(basename "$f") 一个仓库都没列出来：检查令牌是否过期、是否勾选了全部仓库" >&2
    exit 1
  fi
  while read -r repo; do
    dir="/data/$repo.git"
    if [ -d "$dir" ]; then
      echo "==> 更新 $repo"
      git -c http.extraHeader="Authorization: Basic $auth" -C "$dir" remote update --prune </dev/null || failed="$failed $repo"
    else
      echo "==> 首次克隆 $repo"
      mkdir -p "$(dirname "$dir")"
      git -c http.extraHeader="Authorization: Basic $auth" clone --mirror --quiet "https://github.com/$repo.git" "$dir" </dev/null || failed="$failed $repo"
    fi
    total=$((total + 1))
  done < /tmp/repos
done

echo "==> 同步了 $total 个仓库，占用 $(du -sh /data | cut -f1)"
if [ -n "$failed" ]; then
  echo "这些仓库同步失败：$failed" >&2
  exit 1
fi
