import os
import json
import asyncio
from typing import Dict, Optional
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import datetime

from .models import CompanyIGAccount, IGThread, IGMessage
from .service import IGChatService
import logging
logger = logging.getLogger(__name__)

try:
    import orjson  # быстрее стандартного json
except Exception:  # pragma: no cover
    orjson = None

POLL_INTERVAL = float(os.getenv("IG_POLL_INTERVAL", "0.6"))   # опрос сообщений текущего треда
INBOX_INTERVAL = float(os.getenv("IG_INBOX_INTERVAL", "2.0")) # опрос инбокса для новых тредов
POLL_LIMIT = int(os.getenv("IG_POLL_LIMIT", "12"))


def serialize_dt(dt_obj: datetime | str | None) -> str | None:
    if not dt_obj:
        return None
    if isinstance(dt_obj, datetime):
        return dt_obj.isoformat()
    return str(dt_obj)


class DirectConsumer(AsyncJsonWebsocketConsumer):
    """
    Оптимизации:
    - encode_json переопределён на orjson (если доступен)
    - кеш участников тредов self._thread_users_cache
    - .values() для списков тредов/истории (меньше ORM-накладных расходов)
    - thread_sensitive=False для read-only sync_to_async
    - .update() вместо save() для last_activity
    """

        # --- быстрый JSON-энкодер ---
    @staticmethod
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError

    # стало
    async def encode_json(self, content):
        if orjson:
            # orjson возвращает bytes → превращаем в str
            return orjson.dumps(content, default=self._default).decode("utf-8")
        # стандартный json — ок
        return json.dumps(content, default=self._default, separators=(",", ":"))


    async def connect(self):
        self.account_id = self.scope["url_route"]["kwargs"]["account_id"]
        self.thread_id: Optional[str] = self.scope["url_route"]["kwargs"].get("thread_id")
        user = self.scope.get("user")

        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        # company scoping
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            account = await sync_to_async(get_object_or_404, thread_sensitive=False)(
                CompanyIGAccount, pk=self.account_id, is_active=True
            )
        else:
            account = await sync_to_async(get_object_or_404, thread_sensitive=False)(
                CompanyIGAccount.objects.filter(company_id=user.company_id, is_active=True),
                pk=self.account_id,
            )

        self.account = account
        self.svc = IGChatService(account)

        ok = await sync_to_async(self.svc.try_resume_session)()  # svc — thread_sensitive=True
        if not ok:
            await self.close(code=4401)
            return

        # рабочие поля
        self._stop = asyncio.Event()
        self._poll_task = None
        self._inbox_task = None
        self._seen = set()
        self._user_map: Dict[str, str] = {}           # для текущего наблюдаемого треда
        self._thread_users_cache: Dict[str, Dict[str, str]] = {}  # для всех тредов (инбокс)
        self._threads_cache: Dict[str, datetime] = {}
        self._thread_obj: IGThread | None = None

        await self.accept()
        await self.send_json({"type": "connected"})

        # ✅ Проверяем БД: если пусто → тянем из Instagram
        threads_exist = await sync_to_async(
            IGThread.objects.filter(ig_account=self.account).exists, thread_sensitive=False
        )()
        if not threads_exist:
            # initial_sync ходит в сеть и БД — пусть будет thread_sensitive=True
            await sync_to_async(self.svc.initial_sync)(threads_limit=50, msgs_per_thread=20)

        # теперь список тредов уже есть в БД — достаём "легко"
        threads_qs = (
            IGThread.objects
            .filter(ig_account=self.account)
            .order_by("-last_activity")
            .values("thread_id", "title", "users", "last_activity")[:50]
        )
        threads = await sync_to_async(list, thread_sensitive=False)(threads_qs)

        payload = [
            {
                "thread_id": th["thread_id"],
                "title": th["title"],
                "users": th["users"],
                "last_activity": serialize_dt(th["last_activity"]),
            }
            for th in threads
        ]

        self._threads_cache = {
            th["thread_id"]: th["last_activity"] for th in threads if th["last_activity"]
        }
        await self.send_json({"type": "threads_snapshot", "threads": payload})

        # фоновый опрос инбокса
        self._inbox_task = asyncio.create_task(self._inbox_loop())

        if self.thread_id:
            await self._start_watch(self.thread_id)

    async def disconnect(self, code):
        await self._stop_all()

    async def _inbox_loop(self):
        while not self._stop.is_set():
            try:
                # Сетевой вызов — оставляем thread_sensitive=True (клиент может быть не потокобезопасен)
                threads = await sync_to_async(self.svc.fetch_threads_live)(amount=30)
                for t in threads:
                    tid = t["thread_id"]
                    last = t["last_activity"]  # datetime
                    prev = self._threads_cache.get(tid)

                    # всегда синкаем тред в БД
                    th = await sync_to_async(self.svc.sync_thread)(t)

                    if prev is None:
                        # новый тред → участников подтянем и закешируем
                        users = t.get("users")
                        if not users:
                            users = await sync_to_async(self.svc.fetch_thread_users)(
                                tid
                            )
                        u_map = {u["pk"]: u["username"] for u in users}
                        self._thread_users_cache[tid] = u_map

                        preview = await sync_to_async(self.svc.fetch_last_text)(
                            tid, user_map=u_map
                        )

                        # синкаем предпросмотр в БД, чтобы не потерять
                        if preview:
                            await sync_to_async(self.svc.sync_message)(th, preview)

                        payload = {
                            **t,
                            "last_activity": serialize_dt(t.get("last_activity")),
                            "users": users,
                            "preview": {
                                **preview,
                                "created_at": serialize_dt(preview["created_at"]),
                            } if preview else None,
                        }
                        await self.send_json({"type": "thread_new", "thread": payload})

                        if preview:
                            preview_msg = {
                                **preview,
                                "username": u_map.get(preview["sender_pk"]),
                                "created_at": serialize_dt(preview["created_at"]),
                            }
                            await self.send_json({
                                "type": "incoming",
                                "message": preview_msg,
                                "thread_id": tid,
                            })

                        self._threads_cache[tid] = last
                        continue

                    # существующий тред
                    if last and prev and last > prev:
                        # был апдейт — тянем последнее сообщение
                        u_map = self._thread_users_cache.get(tid)
                        if u_map is None:
                            users = t.get("users") or await sync_to_async(self.svc.fetch_thread_users)(tid)
                            u_map = {u["pk"]: u["username"] for u in users}
                            self._thread_users_cache[tid] = u_map

                        preview = await sync_to_async(self.svc.fetch_last_text)(tid, user_map=u_map)

                        if preview:
                            await sync_to_async(self.svc.sync_message)(th, preview)

                        await self.send_json({
                            "type": "incoming",
                            "message": ({
                                **preview,
                                "created_at": serialize_dt(preview["created_at"]),
                            } if preview else None),
                            "thread_id": tid,
                        })

                        await self.send_json({
                            "type": "thread_update",
                            "thread_id": tid,
                            "last_activity": serialize_dt(last),
                            "preview": ({
                                **preview,
                                "created_at": serialize_dt(preview["created_at"]),
                            } if preview else None),
                            "has_new": True,
                        })
                        self._threads_cache[tid] = last

            except Exception as e:
                logger.exception("inbox_loop error")
                await self.send_json({"type": "error", "detail": str(e)})

            await asyncio.sleep(INBOX_INTERVAL)

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
            if self._thread_obj:
                await sync_to_async(self.svc.sync_message)(self._thread_obj, msg)

            await self.send_json({
                "type": "outgoing",
                "message": {**msg, "created_at": serialize_dt(msg.get("created_at"))},
                "thread_id": self.thread_id,
            })
            return

        if t == "history":
            thread_id = str(content.get("thread_id") or "").strip()
            limit = int(content.get("limit") or 50)
            offset = int(content.get("offset") or 0)
            if not thread_id:
                await self.send_json({"type": "error", "detail": "thread_id required"})
                return

            # Берём только нужные поля через .values() и сразу в правильном порядке
            # (сначала последние N по убыванию, потом разворачиваем)
            base_qs = (
                IGMessage.objects
                .filter(thread__ig_account=self.account, thread__thread_id=thread_id)
                .order_by("-created_at")
                .values("mid", "text", "sender_pk", "created_at", "direction", "attachments")
            )
            sliced = await sync_to_async(list, thread_sensitive=False)(base_qs[offset:offset+limit])
            payload = [
                {
                    "mid": m["mid"],
                    "text": m["text"],
                    "sender_pk": m["sender_pk"],
                    "created_at": serialize_dt(m["created_at"]),
                    "direction": m["direction"],
                    "attachments": m.get("attachments") or [],
                }
                for m in reversed(sliced)
            ]
            await self.send_json({"type": "history", "thread_id": thread_id, "messages": payload})
            return

        if t == "threads":
            limit = int(content.get("limit") or 20)
            offset = int(content.get("offset") or 0)
            qs = (
                IGThread.objects
                .filter(ig_account=self.account)
                .order_by("-last_activity")
                .values("thread_id", "title", "users", "last_activity")
            )
            threads = await sync_to_async(list, thread_sensitive=False)(qs[offset:offset+limit])
            payload = [
                {
                    "thread_id": th["thread_id"],
                    "title": th["title"],
                    "users": th["users"],
                    "last_activity": serialize_dt(th["last_activity"]),
                }
                for th in threads
            ]
            await self.send_json({"type": "threads", "threads": payload})
            return

        if t == "delete_message":
            mid = str(content.get("mid") or "").strip()
            if not mid:
                await self.send_json({"type": "error", "detail": "mid required"})
                return
            # .delete() оставим как есть, но вызов через thread_sensitive=False не критичен
            deleted = await sync_to_async(
                IGMessage.objects.filter(mid=mid, thread__ig_account=self.account).delete,
                thread_sensitive=False
            )()
            if deleted[0] > 0:
                await self.send_json({"type": "message_deleted", "mid": mid})
            else:
                await self.send_json({"type": "error", "detail": f"message {mid} not found"})
            return

        if t == "delete_thread":
            tid = str(content.get("thread_id") or "").strip()
            if not tid:
                await self.send_json({"type": "error", "detail": "thread_id required"})
                return
            deleted = await sync_to_async(
                IGThread.objects.filter(ig_account=self.account, thread_id=tid).delete,
                thread_sensitive=False
            )()
            if deleted[0] > 0:
                await self.send_json({"type": "thread_deleted", "thread_id": tid})
            else:
                await self.send_json({"type": "error", "detail": f"thread {tid} not found"})
            return

        await self.send_json({"type": "error", "detail": "unknown event type"})

    # --- helpers ---
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
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

        self.thread_id = thread_id
        self._seen = set()

        # участники: используем кеш, при отсутствии — загрузим и закешируем
        u_map = self._thread_users_cache.get(thread_id)
        users = None
        if u_map is None:
            users = await sync_to_async(self.svc.fetch_thread_users)(thread_id)
            u_map = {u["pk"]: u["username"] for u in users}
            self._thread_users_cache[thread_id] = u_map
        else:
            # чтобы фронт получил список, если уже есть кеш, нужно построить users
            users = [{"pk": pk, "username": name} for pk, name in u_map.items()]

        self._user_map = u_map
        await self.send_json({"type": "participants", "users": users})

        self._thread_obj = await sync_to_async(self.svc.sync_thread)({
            "thread_id": thread_id,
            "title": "",
            "users": users,
            "last_activity": timezone.localtime(),
        })

        msgs = await sync_to_async(self.svc.fetch_messages_db)(self._thread_obj, POLL_LIMIT)

        if not msgs:
            msgs = await sync_to_async(self.svc.fetch_messages_live)(thread_id, POLL_LIMIT)
            for m in msgs:
                self._seen.add(m["mid"])
                if self._thread_obj:
                    await sync_to_async(self.svc.sync_message)(self._thread_obj, m)

        # готовим к отправке наружу
        prepared = []
        for m in msgs:
            prepared.append({
                **m,
                "username": self._user_map.get(m["sender_pk"]),
                "created_at": serialize_dt(m.get("created_at")),
            })
            self._seen.add(m["mid"])

        await self.send_json({"type": "history", "thread_id": thread_id, "messages": prepared})
        await self.send_json({"type": "watching", "thread_id": thread_id})

        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        backoff = 0.0
        # берём последнее сообщение быстро и без лишней гидрации
        last_qs = (
            IGMessage.objects
            .filter(thread=self._thread_obj)
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        last_row = await sync_to_async(lambda: next(iter(last_qs), None), thread_sensitive=False)()
        last_ts: datetime | None = last_row["created_at"] if last_row else None

        while not self._stop.is_set():
            try:
                msgs = await sync_to_async(self.svc.fetch_messages_live)(
                    self.thread_id,
                    POLL_LIMIT,
                    since=last_ts,
                )

                if msgs:
                    # синк + отправка
                    for m in msgs:
                        self._seen.add(m["mid"])
                        m["username"] = self._user_map.get(m["sender_pk"])
                        if self._thread_obj:
                            await sync_to_async(self.svc.sync_message)(self._thread_obj, m)

                        await self.send_json({
                            "type": "incoming",
                            "message": {**m, "created_at": serialize_dt(m["created_at"])},
                            "thread_id": self.thread_id,
                        })

                    last_ts = msgs[-1]["created_at"]

                    if self._thread_obj:
                        # обновляем last_activity без гидрации модели
                        await sync_to_async(
                            lambda: IGThread.objects.filter(pk=self._thread_obj.pk)
                            .update(last_activity=timezone.localtime()),
                            thread_sensitive=False
                        )()

                backoff = 0.0
            except Exception as e:
                logger.exception("poll_loop error")
                await self.send_json({"type": "error", "detail": str(e)})
                backoff = min((backoff or 0.5) * 2, 8.0)

            await asyncio.sleep(backoff or POLL_INTERVAL)
