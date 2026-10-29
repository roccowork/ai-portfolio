// 用 SQLite 的在线备份接口生成一致的数据库副本：应用可以继续读写，得到的是某一时刻完整的数据库。
// 直接复制正在使用的 .db 文件（尤其是 WAL 模式下）可能得到半新半旧、无法打开的文件。
// 只用 Node 自带的 node:sqlite，不需要额外安装 sqlite3。
// 用法：node snapshot.mjs <源数据库> <目标文件>
import { backup, DatabaseSync } from 'node:sqlite';

const [src, dest] = process.argv.slice(2);
if (!src || !dest) {
  console.error('用法: node snapshot.mjs <源数据库> <目标文件>');
  process.exit(2);
}

// 只读打开：源文件不存在时直接报错，而不是新建一个空库
const db = new DatabaseSync(src, { readOnly: true });
const pages = await backup(db, dest);
db.close();

// 打开副本做完整性检查，坏的快照不上传
const copy = new DatabaseSync(dest, { readOnly: true });
const { integrity_check: result } = copy.prepare('PRAGMA integrity_check').get();
copy.close();
if (result !== 'ok') {
  console.error(`快照完整性检查失败：${result}`);
  process.exit(1);
}
console.log(`快照完成：${src} → ${dest}，共 ${pages} 页`);
