"""评测一次只跑一个（都要调用模型、花钱），前面有评测在跑时排队，跑完接着跑；同一个评测不重复排队。

2026-10-29：之前遇到正在跑的评测直接放弃，却回复「跑完会把结果发给你」，后点的 Agent 评测一直没跑。"""

import asyncio
from collections.abc import Awaitable, Callable


class EvalQueue:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.names: set[str] = set()  # 在跑或在排队的
        self.tasks: set[asyncio.Task] = set()  # 保留任务引用，免得后台任务被回收

    def submit(self, name: str, job: Callable[[], Awaitable[None]]) -> str:
        """返回 started（马上开始）、queued（排在前面的评测后面）、dup（同名的已经在跑或在排队）"""
        if name in self.names:
            return "dup"
        busy = self.lock.locked() or bool(self.names)
        self.names.add(name)

        async def run() -> None:
            try:
                async with self.lock:
                    await job()
            finally:
                self.names.discard(name)

        self.tasks.add(t := asyncio.create_task(run()))
        t.add_done_callback(self.tasks.discard)
        return "queued" if busy else "started"
