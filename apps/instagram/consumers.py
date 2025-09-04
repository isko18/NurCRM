import os
import json
import asyncio
from typing import Optional
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async
from django.shortcuts import get_object_or_404
from django.utils.timezone import is_naive, make_aware
from datetime import datetime, timezone as dt_tz

from django.contrib.auth import get_user_model
try:
    from rest_framework_simplejwt.tokens import AccessToken
except Exception:
    AccessToken = None

from .models import CompanyIGAccount, IGThread, IGMessage
from .service import IGChatService

import logging
logger = logging.getLogger(__name__)

try:
    import orjson
except Exception:
    orjson = None

try:
    import msgpack
except Exception:
    msgpack = None

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

REDIS_URL       = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
POLL_LIMIT      = int(os.getenv("IG_POLL_LIMIT", "6"))
MAX_HIST        = int(os.getenv("IG_HIST_CACHE", "20"))
BOOTSTRAP_THREADS = int(os.getenv("IG_BOOTSTRAP_THREADS", "20"))
BOOTSTRAP_MSGS    = int(os.getenv("IG_BOOTSTRAP_MSGS", "10"))

GROUP_FMT         = "ig.{account_id}"
ACTIVE_SET_FMT    = "ig:active:{account_id}"
HIST_LIST_FMT     = "ig:hist:{thread_id}"
THREADS_ZSET_FMT  = "ig:threads:{account_id}"
THREAD_KEY_FMT    = "ig:thread:{thread_id}"
BOOTSTRAP_LOCK    = "ig:bootstrap:{account_id}"


def _dt_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if is_naive(dt):
        dt = make_aware(dt, timezone=dt_tz.utc)
    else:
        dt = dt.astimezone(dt_tz.utc)
    return int(dt.timestamp() * 1000)


def ts_json(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, datetime):
        return _dt_to_epoch_ms(val)
    return val


def dumps(obj: dict) -> str:
    if orjson:
        return orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj, separators=(",", ":"))


class DirectConsumer(AsyncJsonWebsocketConsumer):
    # ---------- fast JSON ----------
    @staticmethod
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError

    async def encode_json(self, content):
        if orjson:
            return orjson.dumps(content, default=self._default).decode("utf-8")
        return json.dumps(content, default=self._default, separators=(",", ":"))

    async def _send_pkt(self, payload: dict):
        if getattr(self, "_use_msgpack", False) and msgpack is not None:
            try:
                packed = msgpack.packb(
                    payload,
                    default=lambda o: o.isoformat() if isinstance(o, datetime) else o,
                    use_bin_type=True,
                )
                await self.send(bytes_data=packed)
                return
            except Exception as e:
                logger.warning("msgpack pack failed, fallback json: %s", e)
        await self.send(text_data=await self.encode_json(payload))

    # ---------- Redis helpers ----------
    async def _hist_read(self, thread_id: str, limit: int, offset: int = 0) -> list[dict]:
        if not self._r:
            return []
        key = HIST_LIST_FMT.format(thread_id=thread_id)
        try:
            if offset < 0:
                offset = 0
            llen = await self._r.llen(key)
            if not llen:
                return []
            start = max(0, llen - offset - limit)
            end = llen - offset - 1
            if start > end:
                return []
            raw = await self._r.lrange(key, start, end)
            out = []
            for s in raw or []:
                try:
                    m = orjson.loads(s) if orjson else json.loads(s)
                    out.append(m)
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning("hist read failed: %s", e)
            return []

    async def _hist_push_local(self, thread_id: str, msg: dict):
        if not self._r:
            return
        key = HIST_LIST_FMT.format(thread_id=thread_id)
        item = {
            "mid": msg.get("mid"),
            "text": msg.get("text") or "",
            "sender_pk": msg.get("sender_pk") or "",
            "username": msg.get("username"),
            "created_at": ts_json(msg.get("created_at")),
            "direction": msg.get("direction") or "out",
            "attachments": msg.get("attachments") or [],
        }
        try:
            await self._r.rpush(key, dumps(item))
            await self._r.ltrim(key, -MAX_HIST, -1)
        except Exception as e:
            logger.warning("hist push local failed: %s", e)

    async def _touch_thread_snapshot(self, thread_id: str, last_dt: Optional[datetime] = None):
        if not self._r:
            return
        last_dt = last_dt or datetime.now(dt_tz.utc)
        zkey = THREADS_ZSET_FMT.format(account_id=str(self.account.pk))
        tkey = THREAD_KEY_FMT.format(thread_id=thread_id)
        try:
            await self._r.zadd(zkey, {thread_id: int(last_dt.timestamp())})
            snap_raw = await self._r.get(tkey)
            if snap_raw:
                snap = orjson.loads(snap_raw) if orjson else json.loads(snap_raw)
                snap["last_activity"] = _dt_to_epoch_ms(last_dt)
            else:
                row = await sync_to_async(
                    lambda: IGThread.objects.filter(ig_account=self.account, thread_id=thread_id)
                    .values("title", "users").first(),
                    thread_sensitive=False
                )()
                snap = {
                    "thread_id": thread_id,
                    "title": (row or {}).get("title") or "",
                    "users": (row or {}).get("users") or [],
                    "last_activity": _dt_to_epoch_ms(last_dt),
                }
            await self._r.set(tkey, dumps(snap))
        except Exception as e:
            logger.warning("touch thread snapshot failed: %s", e)

    async def _threads_snapshot_redis(self, limit: int = 50) -> list[dict]:
        if not self._r:
            return []
        zkey = THREADS_ZSET_FMT.format(account_id=str(self.account.pk))
        try:
            tids = await self._r.zrevrange(zkey, 0, max(0, limit - 1))
            if not tids:
                return []
            pipe = self._r.pipeline()
            for tid in tids:
                pipe.get(THREAD_KEY_FMT.format(thread_id=tid))
            rows = await pipe.execute()
            out = []
            for s in rows:
                if not s:
                    continue
                try:
                    obj = orjson.loads(s) if orjson else json.loads(s)
                    obj["last_activity"] = ts_json(obj.get("last_activity"))
                    out.append(obj)
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning("threads snapshot redis failed: %s", e)
            return []

    # ---------- DB → Redis warmers ----------
    async def _warm_threads_from_db(self, limit: int = 200):
        rows = await sync_to_async(list, thread_sensitive=False)(
            IGThread.objects.filter(ig_account=self.account)
            .order_by("-last_activity")
            .values("thread_id", "title", "users", "last_activity")[:limit]
        )
        if not rows or not self._r:
            return
        zkey = THREADS_ZSET_FMT.format(account_id=str(self.account.pk))
        pipe = self._r.pipeline()
        now_ms = _dt_to_epoch_ms(datetime.now(dt_tz.utc)) or 0
        for th in rows:
            tid = th["thread_id"]
            last_ms = ts_json(th["last_activity"]) or now_ms
            score_sec = int(last_ms // 1000)
            row = {
                "thread_id": tid,
                "title": th["title"],
                "users": th["users"],
                "last_activity": last_ms,
            }
            pipe.set(THREAD_KEY_FMT.format(thread_id=tid), dumps(row))
            pipe.zadd(zkey, {tid: score_sec})
        try:
            await pipe.execute()
        except Exception as e:
            logger.warning("warm threads redis failed: %s", e)

    async def _warm_hist_from_db(self, thread_id: str, limit: int = MAX_HIST):
        if not self._r:
            return
        rows = await sync_to_async(list, thread_sensitive=False)(
            IGMessage.objects.filter(thread__ig_account=self.account, thread__thread_id=thread_id)
            .order_by("-created_at")
            .values("mid", "text", "sender_pk", "created_at", "direction", "attachments")[:limit]
        )
        if not rows:
            return
        key = HIST_LIST_FMT.format(thread_id=thread_id)
        payload = []
        for r in reversed(rows):
            payload.append(dumps({
                "mid": r["mid"],
                "text": r["text"],
                "sender_pk": r["sender_pk"],
                "username": None,
                "created_at": _dt_to_epoch_ms(r["created_at"]),
                "direction": r["direction"],
                "attachments": r.get("attachments") or [],
            }))
        try:
            pipe = self._r.pipeline()
            for s in payload:
                pipe.rpush(key, s)
            pipe.ltrim(key, -MAX_HIST, -1)
            await pipe.execute()
        except Exception as e:
            logger.warning("warm hist redis failed: %s", e)

    async def _bootstrap_if_empty(self):
        try:
            has_threads = await sync_to_async(
                IGThread.objects.filter(ig_account=self.account).exists,
                thread_sensitive=False,
            )()
            has_messages = await sync_to_async(
                IGMessage.objects.filter(thread__ig_account=self.account).exists,
                thread_sensitive=False,
            )()
            if has_threads or has_messages:
                return
        except Exception as e:
            logger.warning("bootstrap exists-check failed: %s", e)
            return

        lock_acquired = False
        if self._r:
            try:
                lock_key = BOOTSTRAP_LOCK.format(account_id=str(self.account.pk))
                lock_acquired = await self._r.set(lock_key, "1", ex=180, nx=True)
                if not lock_acquired:
                    await asyncio.sleep(1.0)
                    return
            except Exception:
                pass

        svc = IGChatService(self.account)
        try:
            ok = await sync_to_async(svc.try_resume_session)()
            if not ok:
                logger.warning("bootstrap: IG session not available for account %s", self.account.pk)
                return

            await sync_to_async(svc.initial_sync)(
                threads_limit=BOOTSTRAP_THREADS,
                msgs_per_thread=BOOTSTRAP_MSGS,
            )

            await self._warm_threads_from_db(limit=200)
            last_threads = await sync_to_async(list, thread_sensitive=False)(
                IGThread.objects.filter(ig_account=self.account)
                .order_by("-last_activity")
                .values_list("thread_id", flat=True)[:BOOTSTRAP_THREADS]
            )
            for tid in last_threads:
                await self._warm_hist_from_db(str(tid), limit=min(BOOTSTRAP_MSGS, MAX_HIST))
        finally:
            if self._r and lock_acquired:
                try:
                    await self._r.delete(BOOTSTRAP_LOCK.format(account_id=str(self.account.pk)))
                except Exception:
                    pass

    # ---------- lifecycle ----------
    async def connect(self):
        try:
            self.account_id = self.scope["url_route"]["kwargs"]["account_id"]
            self.thread_id: Optional[str] = self.scope["url_route"]["kwargs"].get("thread_id")
            user = self.scope.get("user")

            qs = parse_qs((self.scope.get("query_string") or b"").decode("utf-8"), keep_blank_values=True)
            enc = (qs.get("enc", [""])[0] or "").lower()
            self._use_msgpack = (enc in ("mp", "msgpack", "bin")) and (msgpack is not None)

            # JWT ?token=...
            if not user or not user.is_authenticated:
                token = (qs.get("token", [""])[0] or "").strip()
                if AccessToken and token:
                    try:
                        at = AccessToken(token)
                        uid = at.get("user_id")
                        if not uid:
                            await self.close(code=4401); return
                        User = get_user_model()
                        user = await sync_to_async(User.objects.get, thread_sensitive=False)(pk=uid)
                        self.scope["user"] = user
                    except Exception:
                        await self.close(code=4401)
                        return
                else:
                    await self.close(code=4401)
                    return

            user = self.scope["user"]

            # account
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
            self.group = GROUP_FMT.format(account_id=str(self.account.pk))

            # Redis
            self._r = None
            if aioredis:
                try:
                    self._r = aioredis.from_url(REDIS_URL, decode_responses=True)
                    await self._r.ping()
                except Exception as e:
                    logger.warning("Redis not available in consumer: %s", e)
                    self._r = None

            # IG client (persistent, логин один раз)
            self._svc = IGChatService(self.account)
            self._svc_ready = await sync_to_async(self._svc.try_resume_session)()

            # подписка и немедленное принятие
            await self.channel_layer.group_add(self.group, self.channel_name)
            await self.accept()
            await self._send_pkt({"type": "connected", "enc": "mp" if self._use_msgpack else "json"})

            # бутстрап в фоне
            asyncio.create_task(self._bootstrap_if_empty())

            # threads snapshot (Redis fast)
            threads = await self._threads_snapshot_redis(limit=50)
            if not threads:
                threads = await sync_to_async(list, thread_sensitive=False)(
                    IGThread.objects.filter(ig_account=self.account)
                    .order_by("-last_activity")
                    .values("thread_id", "title", "users", "last_activity")[:50]
                )
                threads = [
                    {"thread_id": th["thread_id"], "title": th["title"], "users": th["users"],
                     "last_activity": ts_json(th["last_activity"])}
                    for th in threads
                ]
            await self._send_pkt({"type": "threads_snapshot", "threads": threads})

            if self.thread_id:
                await self._start_watch(self.thread_id)

        except Exception as e:
            logger.exception("ws connect failed: %s", e)
            try:
                await self._send_pkt({"type": "error", "detail": "connect_failed"})
            finally:
                await self.close()

    async def disconnect(self, code):
        acc = getattr(self, "account", None)
        if getattr(self, "_r", None) and getattr(self, "_active_tid", None) and acc:
            try:
                key = ACTIVE_SET_FMT.format(account_id=str(acc.pk))
                await self._r.srem(key, self._active_tid)
            except Exception:
                pass
        if getattr(self, "group", None):
            try:
                await self.channel_layer.group_discard(self.group, self.channel_name)
            except Exception:
                pass
        if getattr(self, "_r", None):
            try:
                await self._r.close()
            except Exception:
                pass

    async def ig_event(self, event):
        await self._send_pkt(event["payload"])

    async def receive_json(self, content, **kwargs):
        t = content.get("type")

        if t == "ping":
            await self._send_pkt({"type": "pong"})
            return

        if t == "watch":
            thread_id = str(content.get("thread_id") or "").strip()
            if not thread_id:
                await self._send_pkt({"type": "error", "detail": "thread_id required"})
                return
            await self._start_watch(thread_id)
            return

        if t == "send":
            if not self.thread_id:
                await self._send_pkt({"type": "error", "detail": "no thread selected"})
                return
            text = (content.get("text") or "").strip()
            client_id = (content.get("client_id") or "").strip()
            if not text:
                await self._send_pkt({"type": "error", "detail": "text is required"})
                return

            try:
                # если ещё не логинились — один раз попробуем
                if not self._svc_ready:
                    self._svc_ready = await sync_to_async(self._svc.try_resume_session)()
                if not self._svc_ready:
                    await self._send_pkt({"type": "error", "detail": "IG session required"})
                    return

                raw = await sync_to_async(self._svc.send_text)(self.thread_id, text, client_id or None)
                msg = {**raw, "created_at": ts_json(raw.get("created_at"))}

                # локально в кеш/список
                await self._hist_push_local(self.thread_id, msg)
                await self._touch_thread_snapshot(self.thread_id, datetime.now(dt_tz.utc))

                # ответ отправителю: заменим client_id
                await self._send_pkt({"type": "outgoing", "message": msg, "thread_id": self.thread_id, "client_id": client_id or None})
                # другим вкладкам/операторам ничего не шлём (poller сам разошлёт входящие, а "out" мы фильтруем)
            except Exception as e:
                logger.exception("send failed: %s", e)
                await self._send_pkt({"type": "error", "detail": "send_failed"})
            return

        if t == "history":
            thread_id = str(content.get("thread_id") or "").strip()
            limit = int(content.get("limit") or 50)
            offset = int(content.get("offset") or 0)
            if not thread_id:
                await self._send_pkt({"type": "error", "detail": "thread_id required"})
                return

            hist = await self._hist_read(thread_id, limit, offset)
            if hist:
                await self._send_pkt({"type": "history", "thread_id": thread_id, "messages": hist})
                return

            await self._bootstrap_if_empty()
            base_qs = (
                IGMessage.objects
                .filter(thread__ig_account=self.account, thread__thread_id=thread_id)
                .order_by("-created_at")
                .values("mid", "text", "sender_pk", "created_at", "direction", "attachments")
            )
            sliced = await sync_to_async(list, thread_sensitive=False)(base_qs[offset:offset+limit])
            payload = [
                {"mid": m["mid"], "text": m["text"], "sender_pk": m["sender_pk"], "created_at": _dt_to_epoch_ms(m["created_at"]),
                 "direction": m["direction"], "attachments": m.get("attachments") or []}
                for m in reversed(sliced)
            ]
            await self._send_pkt({"type": "history", "thread_id": thread_id, "messages": payload})
            return

        if t == "threads":
            limit = int(content.get("limit") or 20)
            offset = int(content.get("offset") or 0)
            snap = await self._threads_snapshot_redis(limit=offset+limit)
            if not snap:
                await self._bootstrap_if_empty()
                snap = await self._threads_snapshot_redis(limit=offset+limit)
            if snap:
                await self._send_pkt({"type": "threads", "threads": snap[offset:offset+limit]})
                return

            threads = await sync_to_async(list, thread_sensitive=False)(
                IGThread.objects.filter(ig_account=self.account)
                .order_by("-last_activity")
                .values("thread_id", "title", "users", "last_activity")[offset:offset+limit]
            )
            payload = [
                {"thread_id": th["thread_id"], "title": th["title"], "users": th["users"], "last_activity": ts_json(th["last_activity"])}
                for th in threads
            ]
            await self._send_pkt({"type": "threads", "threads": payload})
            return

        if t == "delete_message":
            mid = str(content.get("mid") or "").strip()
            if not mid:
                await self._send_pkt({"type": "error", "detail": "mid required"})
                return
            tid_before = await sync_to_async(
                lambda: IGMessage.objects.filter(mid=mid, thread__ig_account=self.account)
                .values_list("thread__thread_id", flat=True).first(),
                thread_sensitive=False
            )()
            deleted = await sync_to_async(
                IGMessage.objects.filter(mid=mid, thread__ig_account=self.account).delete,
                thread_sensitive=False
            )()
            if deleted[0] > 0:
                if self._r and tid_before:
                    try:
                        key = HIST_LIST_FMT.format(thread_id=str(tid_before))
                        raw_list = await self._r.lrange(key, 0, -1)
                        victim = None
                        for s in raw_list or []:
                            try:
                                m = orjson.loads(s) if orjson else json.loads(s)
                                if m.get("mid") == mid:
                                    victim = s
                                    break
                            except Exception:
                                continue
                        if victim:
                            await self._r.lrem(key, 1, victim)
                    except Exception:
                        pass
                await self._send_pkt({"type": "message_deleted", "mid": mid})
            else:
                await self._send_pkt({"type": "error", "detail": f"message {mid} not found"})
            return

        if t == "delete_thread":
            tid = str(content.get("thread_id") or "").strip()
            if not tid:
                await self._send_pkt({"type": "error", "detail": "thread_id required"})
                return
            deleted = await sync_to_async(
                IGThread.objects.filter(ig_account=self.account, thread_id=tid).delete,
                thread_sensitive=False
            )()
            if deleted[0] > 0:
                if self._r:
                    try:
                        await self._r.delete(HIST_LIST_FMT.format(thread_id=tid))
                        await self._r.delete(THREAD_KEY_FMT.format(thread_id=tid))
                        await self._r.zrem(THREADS_ZSET_FMT.format(account_id=str(self.account.pk)), tid)
                    except Exception:
                        pass
                await self._send_pkt({"type": "thread_deleted", "thread_id": tid})
            else:
                await self._send_pkt({"type": "error", "detail": f"thread {tid} not found"})
            return

        await self._send_pkt({"type": "error", "detail": "unknown event type"})

    async def _start_watch(self, thread_id: str):
        self.thread_id = thread_id

        if self._r:
            try:
                key = ACTIVE_SET_FMT.format(account_id=str(self.account.pk))
                if getattr(self, "_active_tid", None) and self._active_tid != thread_id:
                    await self._r.srem(key, self._active_tid)
                await self._r.sadd(key, thread_id)
                self._active_tid = thread_id
            except Exception as e:
                logger.warning("active set write failed: %s", e)

        th = await sync_to_async(
            lambda: IGThread.objects.filter(ig_account=self.account, thread_id=thread_id)
            .values("users", "last_activity").first(),
            thread_sensitive=False
        )()
        users = (th or {}).get("users") or []

        hist = await self._hist_read(thread_id, POLL_LIMIT, 0)
        if not hist:
            msgs = await sync_to_async(list, thread_sensitive=False)(
                IGMessage.objects.filter(thread__ig_account=self.account, thread__thread_id=thread_id)
                .order_by("-created_at")
                .values("mid", "text", "sender_pk", "created_at", "direction", "attachments")[:POLL_LIMIT]
            )
            hist = [
                {"mid": m["mid"], "text": m["text"], "sender_pk": m["sender_pk"], "username": None,
                 "created_at": _dt_to_epoch_ms(m["created_at"]), "direction": m["direction"],
                 "attachments": m.get("attachments") or []}
                for m in reversed(msgs)
            ]

        await self._send_pkt({
            "type": "watch_init",
            "thread_id": thread_id,
            "users": users,
            "messages": hist,
            "last_activity": ts_json((th or {}).get("last_activity")),
        })
        await self._send_pkt({"type": "participants", "users": users})
        await self._send_pkt({"type": "watching", "thread_id": thread_id})
