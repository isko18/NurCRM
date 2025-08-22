# apps/instagram/consumers.py
import os
import asyncio
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from django.shortcuts import get_object_or_404

from .models import CompanyIGAccount
from .service import IGChatService

POLL_INTERVAL = float(os.getenv("IG_POLL_INTERVAL", "0.6"))   # опрос сообщений текущего треда
INBOX_INTERVAL = float(os.getenv("IG_INBOX_INTERVAL", "2.0")) # опрос инбокса для новых тредов
POLL_LIMIT = int(os.getenv("IG_POLL_LIMIT", "12"))


class DirectConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.account_id = self.scope["url_route"]["kwargs"]["account_id"]
        self.thread_id = self.scope["url_route"]["kwargs"].get("thread_id")
        user = self.scope.get("user")

        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        # company scoping
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            account = await sync_to_async(get_object_or_404)(CompanyIGAccount, pk=self.account_id, is_active=True)
        else:
            account = await sync_to_async(get_object_or_404)(
                CompanyIGAccount.objects.filter(company_id=user.company_id, is_active=True),
                pk=self.account_id,
            )

        self.account = account
        self.svc = IGChatService(account)

        ok = await sync_to_async(self.svc.try_resume_session)()
        if not ok:
            await self.close(code=4401)
            return

        # рабочие поля
        self._stop = asyncio.Event()
        self._poll_task = None
        self._inbox_task = None
        self._seen = set()
        self._user_map = {}
        self._threads_cache = {}  # thread_id -> last_activity (iso)

        await self.accept()
        await self.send_json({"type": "connected"})

        # первый снимок списка тредов
        threads = await sync_to_async(self.svc.fetch_threads_live)(amount=50)
        self._threads_cache = {t["thread_id"]: t["last_activity"] for t in threads}
        await self.send_json({"type": "threads_snapshot", "threads": threads})

        # фоновое слежение за новыми тредами
        self._inbox_task = asyncio.create_task(self._inbox_loop())

        # если в URL указан тред — сразу смотрим его
        if self.thread_id:
            await self._start_watch(self.thread_id)

    async def disconnect(self, code):
        await self._stop_all()

    async def receive_json(self, content, **kwargs):
        t = content.get("type")
        if t == "ping":
            await self.send_json({"type": "pong"})
            return

        if t == "watch":
            thread_id = str(content.get("thread_id") or "").strip()
            if not thread_id:
                await self.send_json({"type": "error", "detail": "thread_id required"})
                return
            await self._start_watch(thread_id)
            return

        if t == "send":
            if not self.thread_id:
                await self.send_json({"type": "error", "detail": "no thread selected"})
                return
            text = (content.get("text") or "").strip()
            if not text:
                await self.send_json({"type": "error", "detail": "text is required"})
                return
            msg = await sync_to_async(self.svc.send_text)(self.thread_id, text)
            self._seen.add(msg["mid"])
            await self.send_json({"type": "outgoing", "message": msg})
            return

        await self.send_json({"type": "error", "detail": "unknown event type"})

    # -------- helpers --------
    async def _stop_all(self):
        self._stop.set()
        for task in (self._poll_task, self._inbox_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = self._inbox_task = None
        self._stop = asyncio.Event()

    async def _start_watch(self, thread_id: str):
        # остановить прежний поллер треда
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

        self.thread_id = thread_id
        self._seen = set()

        users = await sync_to_async(self.svc.fetch_thread_users)(thread_id)
        self._user_map = {u["pk"]: u["username"] for u in users}
        await self.send_json({"type": "participants", "users": users})

        # первоначальная выдача последних сообщений
        msgs = await sync_to_async(self.svc.fetch_messages_live)(
            thread_id,
            POLL_LIMIT,
        )
        for m in msgs:
            self._seen.add(m["mid"])
            # приклеим username, чтобы фронт не искал
            m["username"] = self._user_map.get(m["sender_pk"])
            await self.send_json({"type": "incoming", "message": m})

        await self.send_json({"type": "watching", "thread_id": thread_id})
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        backoff = 0.0
        while not self._stop.is_set():
            try:
                msgs = await sync_to_async(self.svc.fetch_messages_live)(
                    self.thread_id,
                    POLL_LIMIT,
                )
                for m in msgs:
                    mid = m.get("mid")
                    if not mid or mid in self._seen:
                        continue
                    self._seen.add(mid)
                    m["username"] = self._user_map.get(m["sender_pk"])
                    await self.send_json({"type": "incoming", "message": m})
                backoff = 0.0
            except Exception as e:
                await self.send_json({"type": "error", "detail": str(e)})
                backoff = min((backoff or 0.5) * 2, 8.0)
            await asyncio.sleep(backoff or POLL_INTERVAL)

    async def _inbox_loop(self):
        """
        Параллельный лёгкий опрос инбокса: новые треды и обновления тредов.
        Шлём thread_new / thread_update с превью последнего текста.
        """
        while not self._stop.is_set():
            try:
                threads = await sync_to_async(self.svc.fetch_threads_live)(amount=30)
                for t in threads:
                    tid = t["thread_id"]
                    last = t["last_activity"]
                    prev = self._threads_cache.get(tid)

                    # новый тред
                    if prev is None:
                        users = t.get("users") or await sync_to_async(self.svc.fetch_thread_users)(tid)
                        u_map = {u["pk"]: u["username"] for u in users}
                        preview = await sync_to_async(self.svc.fetch_last_text)(tid, user_map=u_map)
                        payload = dict(t)
                        payload["preview"] = preview
                        await self.send_json({"type": "thread_new", "thread": payload})
                        self._threads_cache[tid] = last
                        continue

                    # обновился (пришло новое)
                    if last and prev and last > prev:
                        u_map = {u["pk"]: u["username"] for u in (t.get("users") or [])}
                        preview = await sync_to_async(self.svc.fetch_last_text)(tid, user_map=u_map)
                        await self.send_json({
                            "type": "thread_update",
                            "thread_id": tid,
                            "last_activity": last,
                            "preview": preview,
                            "has_new": True,
                        })
                        self._threads_cache[tid] = last

            except Exception as e:
                await self.send_json({"type": "error", "detail": str(e)})

            await asyncio.sleep(INBOX_INTERVAL)
