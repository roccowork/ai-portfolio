#!/bin/sh
# 用 restic 把应用数据目录（/data）和 SQLite 一致性快照（/snapshot）加密备份到 R2，再按保留策略清理旧快照。
# 环境变量：
#   RESTIC_REPOSITORY、RESTIC_PASSWORD、AWS_ACCESS_KEY_ID、AWS_SECRET_ACCESS_KEY  来自 Secret backup-r2
#   BACKUP_HOST      快照的归属名。Pod 名每次都不同，不能用默认的主机名，否则保留策略无法按应用分组
#   BACKUP_EXCLUDES  额外的排除规则，空格分隔（可选）
set -eu

# 第一次运行时初始化仓库。仓库已存在时 init 会拒绝执行，不会覆盖已有备份
if ! restic cat config >/dev/null 2>&1; then
  echo "==> 读取仓库失败，尝试初始化（仓库已存在时这一步会报错退出，请检查密钥和网络）"
  restic init
fi

# 正在使用的数据库文件不直接备份，用 /snapshot 里的一致性副本代替
set -- --exclude '/data/*.db' --exclude '/data/*.db-wal' --exclude '/data/*.db-shm' --exclude '/data/*.db-journal'
for pattern in ${BACKUP_EXCLUDES:-}; do
  set -- "$@" --exclude "$pattern"
done

echo "==> 备份"
restic backup --host "$BACKUP_HOST" "$@" /data /snapshot

echo "==> 清理旧快照：保留最近 7 天、4 周、6 个月各一份"
restic forget --host "$BACKUP_HOST" --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

echo "==> 检查仓库结构"
restic check

restic snapshots --host "$BACKUP_HOST" --latest 3
