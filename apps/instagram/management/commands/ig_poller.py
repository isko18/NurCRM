import os
import asyncio
import logging
import random
from datetime import datetime, timezone as dt_tz
from typing import Dict, Optional, List

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.utils import timezone
from channels.layers import get_channel_layer

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

try:
    import orjson
except Exception:
    orjson = None

from ...models import CompanyIGAccount, IGThread, IGMessage
from ...service import IGChatService

logger = logging.getLogger(__name__)

# ====== интервалы/лимиты (резвые дефолты) ======
INBOX_INTERVAL_ACTIVE = float(os.getenv("IG_INBOX_ACTIVE", "0.25"))
INBOX_INTERVAL_IDLE   = float(os.getenv("IG_INBOX_IDLE", "1.5"))
THREAD_POLL_INTERVAL  = float(os.getenv("IG_THREAD_POLL", "0.25"))
POLL_LIMIT            = max(5, int(os.getenv("IG_POLL_LIMIT", "200")))
REDIS_URL             = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_HIST              = int(os.getenv("IG_HIST_CACHE", "200"))

# ====== имена групп/ключей ======
GROUP_FMT         = "ig.{account_id}"          # Channels group (без ':')
ACTIVE_SET_FMT    = "ig:active:{account_id}"   # Redis Set активных тредов
WM_HASH_FMT       = "ig:wm:{account_id}"       # Redis Hash watermarks (ISO)
HIST_LIST_FMT     = "ig:hist:{thread_id}"      # Redis List истории треда
THREADS_ZSET_FMT  = "ig:threads:{account_id}"  # Redis ZSET списка тредов
THREAD_KEY_FMT    = "ig:thread:{thread_id}"    # Redis per-thread snapshot


# ---------- helpers: time / json ----------
def _dt_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_tz.utc)
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


def serialize_dt(dt_obj: datetime | str | None) -> str | None:
    if not dt_obj:
        return None
    if isinstance(dt_obj, datetime):
        if timezone.is_naive(dt_obj):
            dt_obj = timezone.make_aware(dt_obj, timezone=dt_tz.utc)
        else:
            dt_obj = dt_obj.astimezone(dt_tz.utc)
        return dt_obj.isoformat()
    return str(dt_obj)


def parse_dt(val) -> Optional[datetime]:
    if isinstance(val, datetime):
        return timezone.make_aware(val, timezone=dt_tz.utc) if timezone.is_naive(val) else val.astimezone(dt_tz.utc)
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        return dt.astimezone(dt_tz.utc)
    except Exception:
        return None


def _to_dt(val) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return timezone.make_aware(val, timezone=dt_tz.utc) if timezone.is_naive(val) else val.astimezone(dt_tz.utc)
    s = str(val)
    if s.isdigit():
        try:
            n = int(s)
            if n > 10**14:    # microseconds
                return datetime.fromtimestamp(n / 1_000_000, tz=dt_tz.utc)
            if n > 10**11:    # milliseconds
                return datetime.fromtimestamp(n / 1_000, tz=dt_tz.utc)
            return datetime.fromtimestamp(n, tz=dt_tz.utc)  # seconds
        except Exception:
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        return dt.astimezone(dt_tz.utc)
    except Exception:
        return None


def dumps(obj: dict) -> str:
    if orjson:
        return orjson.dumps(obj).decode("utf-8")
    import json
    return json.dumps(obj, separators=(",", ":"))


def _created_dt(msg: dict) -> Optional[datetime]:
    """Безопасно достаём created_at как aware datetime (UTC)."""
    return _to_dt(msg.get("created_at"))


class AccountWorker:
    """
    Реактивный воркер IG-аккаунта:
      — быстрый опрос inbox/активных тредов
      — синк БД
      — события в Channels-группу (timestamps уже epoch-ms)
      — Redis: история (LIST) и снепшоты тредов (ZSET + per-thread key)
    """
    def __init__(self, account: CompanyIGAccount, channel_layer, r=None):
        self.account = account
        self.cl = IGChatService(account)
        self.channel_layer = channel_layer
        self.r = r  # aioredis client (decode_responses=True)
        self.group = GROUP_FMT.format(account_id=str(account.pk))

        self.threads_cache: Dict[str, datetime] = {}      # thread_id -> last_activity (datetime)
        self.thread_users: Dict[str, Dict[str, str]] = {} # thread_id -> {pk: username}

    async def group_send(self, payload: dict):
        await self.channel_layer.group_send(self.group, {"type": "ig.event", "payload": payload})

    # ---------- Redis helpers ----------
    async def _get_active_threads(self) -> set[str]:
        if not self.r:
            return set()
        key = ACTIVE_SET_FMT.format(account_id=str(self.account.pk))
        try:
            return set(await self.r.smembers(key) or [])
        except Exception as e:
            logger.warning("active set read failed: %s", e)
            return set()

    async def _get_wm(self, thread_id: str) -> Optional[datetime]:
        if not self.r:
            return None
        key = WM_HASH_FMT.format(account_id=str(self.account.pk))
        try:
            iso = await self.r.hget(key, thread_id)
            return parse_dt(iso) if iso else None
        except Exception:
            return None

    async def _set_wm(self, thread_id: str, ts: datetime):
        if not self.r:
            return
        key = WM_HASH_FMT.format(account_id=str(self.account.pk))
        try:
            await self.r.hset(key, thread_id, serialize_dt(ts))
        except Exception as e:
            logger.warning("wm hset failed: %s", e)

    async def _hist_push(self, thread_id: str, msg: dict, u_map: Dict[str, str]):
        if not self.r:
            return
        key = HIST_LIST_FMT.format(thread_id=thread_id)
        item = {
            "mid": msg.get("mid"),
            "text": msg.get("text") or "",
            "sender_pk": msg.get("sender_pk") or "",
            "username": u_map.get(msg.get("sender_pk") or "") if u_map else None,
            "created_at": ts_json(msg.get("created_at")),
            "direction": msg.get("direction") or "in",
            "attachments": msg.get("attachments") or [],
        }
        try:
            await self.r.rpush(key, dumps(item))
            await self.r.ltrim(key, -MAX_HIST, -1)
        except Exception as e:
            logger.warning("hist push failed: %s", e)

    async def _snapshot_thread(self, t: dict, users: list | None, last_dt: Optional[datetime]):
        if not self.r:
            return
        tid = t["thread_id"]
        score = int(last_dt.timestamp()) if last_dt else 0
        zkey = THREADS_ZSET_FMT.format(account_id=str(self.account.pk))
        try:
            await self.r.zadd(zkey, {tid: score})
            payload = {
                "thread_id": tid,
                "title": t.get("title") or "",
                "users": users or [],
                "last_activity": ts_json(last_dt),  # epoch-ms
            }
            await self.r.set(THREAD_KEY_FMT.format(thread_id=tid), dumps(payload))
        except Exception as e:
            logger.warning("snapshot thread failed: %s", e)

    # ---------- helpers ----------
    async def _ensure_thread_users(self, thread_id: str) -> Dict[str, str]:
        m = self.thread_users.get(thread_id)
        if m is not None:
            return m
        users = await asyncio.to_thread(self.cl.fetch_thread_users, thread_id)
        u_map = {u["pk"]: u["username"] for u in (users or [])}
        self.thread_users[thread_id] = u_map
        return u_map

    async def _sync_thread(self, t: dict) -> IGThread:
        return await asyncio.to_thread(self.cl.sync_thread, t)

    async def _sync_msg(self, th: IGThread, m: dict) -> IGMessage:
        return await asyncio.to_thread(self.cl.sync_message, th, m)

    # ---------- main loop ----------
    async def run(self):
        ok = await asyncio.to_thread(self.cl.try_resume_session)
        if not ok:
            logger.error("resume session failed for %s", getattr(self.account, "username", self.account.pk))
            return

        # warm cache из БД
        try:
            rows = await sync_to_async(list)(
                IGThread.objects.filter(ig_account=self.account)
                .order_by("-last_activity")
                .values("thread_id", "last_activity")[:200]
            )
            for row in rows:
                if row["last_activity"]:
                    self.threads_cache[row["thread_id"]] = row["last_activity"]
        except Exception:
            pass

        inbox_backoff = 0.0
        while True:
            try:
                active = await self._get_active_threads()

                base_sleep = THREAD_POLL_INTERVAL if active else INBOX_INTERVAL_IDLE

                updates_found = await self._tick_inbox(active_threads=active)
                if active:
                    await self._tick_active_threads(active)

                sleep_for = inbox_backoff or (max(0.12, base_sleep * (0.6 if updates_found else 1.0)))
                sleep_for *= 0.9 + 0.2 * random.random()  # лёгкий джиттер
                await asyncio.sleep(sleep_for)
                inbox_backoff = 0.0
            except Exception as e:
                logger.exception("account loop error (%s): %s",
                                 getattr(self.account, "username", self.account.pk), e)
                inbox_backoff = min((inbox_backoff or 0.25) * 2, 4.0)
                await asyncio.sleep(inbox_backoff)

    async def _tick_inbox(self, active_threads: set[str]) -> bool:
        updates_found = False
        threads = await asyncio.to_thread(self.cl.fetch_threads_live, 30)

        for t in threads:
            tid = t["thread_id"]
            last = parse_dt(t.get("last_activity"))
            prev = self.threads_cache.get(tid)

            t_for_db = {**t, "last_activity": last}
            th = await self._sync_thread(t_for_db)

            u_map = self.thread_users.get(tid)
            users = t.get("users")
            if u_map is None:
                if not users:
                    users = await asyncio.to_thread(self.cl.fetch_thread_users, tid)
                u_map = {u["pk"]: u["username"] for u in (users or [])}
                self.thread_users[tid] = u_map
            elif users is None:
                users = [{"pk": pk, "username": name} for pk, name in u_map.items()]

            # актуализируем снэпшот
            await self._snapshot_thread(t_for_db, users, last)

            # Новый тред
            if prev is None:
                preview = await asyncio.to_thread(self.cl.fetch_last_text, tid, u_map)
                if preview:
                    await self._sync_msg(th, preview)
                    wm = _created_dt(preview)
                    if wm:
                        await self._set_wm(tid, wm)
                    await self._hist_push(tid, preview, u_map)

                await self.group_send({
                    "type": "thread_new",
                    "thread": {
                        **t,
                        "last_activity": ts_json(last),
                        "users": users,
                        "preview": ({**preview, "created_at": ts_json(preview["created_at"])}
                                    if preview else None),
                    }
                })

                # на клиент слать входящие превью — только если это не наше исходящее
                if preview and preview.get("direction") != "out" and (tid not in active_threads):
                    await self.group_send({
                        "type": "incoming_batch",
                        "thread_id": tid,
                        "messages": [{
                            **preview,
                            "created_at": ts_json(preview["created_at"]),
                            "username": u_map.get(preview.get("sender_pk")),
                        }],
                    })

                self.threads_cache[tid] = last
                updates_found = True
                continue

            # Обновление существующего
            if last and prev and last > prev:
                # Подтягиваем новые с учётом WM/prev
                since_dt = await self._get_wm(tid) or prev
                new_msgs: List[dict] = await asyncio.to_thread(
                    self.cl.fetch_messages_live, tid, POLL_LIMIT,
                    user_map=None, include_usernames=False, since=since_dt,
                )

                if new_msgs:
                    # sync + history cache
                    for m in new_msgs:
                        await self._sync_msg(th, m)
                        await self._hist_push(tid, m, u_map)

                    # отправляем только входящие, чтобы не дублировать исходящие
                    in_only = [m for m in new_msgs if m.get("direction") != "out"]
                    if in_only:
                        await self.group_send({
                            "type": "incoming_batch",
                            "thread_id": tid,
                            "messages": [
                                {**m, "created_at": ts_json(m.get("created_at")),
                                 "username": u_map.get(m.get("sender_pk"))}
                                for m in in_only
                            ],
                        })

                    last_created = _created_dt(new_msgs[-1]) or last
                    if last_created:
                        await self._set_wm(tid, last_created)
                        # актуализируем зет/снэпшот по реальному времени сообщения
                        await self._snapshot_thread(t_for_db, users, last_created)

                preview = await asyncio.to_thread(self.cl.fetch_last_text, tid, u_map)
                await self.group_send({
                    "type": "thread_update",
                    "thread_id": tid,
                    "last_activity": ts_json(last),
                    "preview": ({**preview, "created_at": ts_json(preview["created_at"])}
                                if preview else None),
                    "has_new": bool(new_msgs),
                })

                self.threads_cache[tid] = last
                updates_found = updates_found or bool(new_msgs)

        return updates_found

    async def _tick_active_threads(self, active_threads: set[str]):
        tasks = [self._poll_thread_once(tid) for tid in list(active_threads)[:20]]
        if tasks:
            await asyncio.gather(*tasks)

    async def _poll_thread_once(self, thread_id: str):
        try:
            th = await asyncio.to_thread(IGThread.objects.get,
                                         ig_account=self.account, thread_id=thread_id)
        except IGThread.DoesNotExist:
            th = await self._sync_thread({
                "thread_id": thread_id, "title": "",
                "users": [{"pk": pk, "username": name}
                          for pk, name in (self.thread_users.get(thread_id, {})).items()],
                "last_activity": timezone.now(),
            })

        u_map = await self._ensure_thread_users(thread_id)

        since_dt = await self._get_wm(thread_id)
        msgs: List[dict] = await asyncio.to_thread(
            self.cl.fetch_messages_live, thread_id, POLL_LIMIT,
            user_map=None, include_usernames=False, since=since_dt,
        )

        last_created: Optional[datetime] = None
        if msgs:
            for m in msgs:
                await self._sync_msg(th, m)
                await self._hist_push(thread_id, m, u_map)
            last_created = _created_dt(msgs[-1])

            # только входящие — в канал
            in_only = [m for m in msgs if m.get("direction") != "out"]
            if in_only:
                await self.group_send({
                    "type": "incoming_batch",
                    "thread_id": thread_id,
                    "messages": [
                        {**m, "created_at": ts_json(m.get("created_at")),
                         "username": u_map.get(m.get("sender_pk"))}
                        for m in in_only
                    ],
                })

            if last_created:
                await self._set_wm(thread_id, last_created)

        # снэпшот по реальному времени сообщения (если было), иначе — по now()
        snapshot_dt = last_created or timezone.now()
        await self._snapshot_thread({"thread_id": thread_id, "title": ""},
                                    [{"pk": pk, "username": name} for pk, name in u_map.items()],
                                    snapshot_dt)
        await asyncio.to_thread(lambda: IGThread.objects.filter(pk=th.pk).update(last_activity=snapshot_dt))

        if msgs:
            last = msgs[-1]
            try:
                await self.group_send({
                    "type": "thread_update",
                    "thread_id": thread_id,
                    "last_activity": ts_json(snapshot_dt),
                    "preview": {**last, "created_at": ts_json(last.get("created_at"))},
                    "has_new": True,
                })
            except Exception as e:
                logger.warning("thread_update broadcast failed: %s", e)


class Command(BaseCommand):
    help = "Instagram central poller with Redis history & threads snapshots"

    def add_arguments(self, parser):
        parser.add_argument("--accounts", nargs="*", help="Limit to specific account UUIDs", default=None)

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO)
        asyncio.run(self._amain(opts))

    async def _amain(self, opts):
        channel_layer = get_channel_layer()
        if channel_layer is None:
            raise RuntimeError("CHANNEL_LAYERS not configured for Redis")

        r = None
        if aioredis:
            try:
                r = aioredis.from_url(REDIS_URL, decode_responses=True)
                await r.ping()
            except Exception as e:
                logger.warning("Redis not available for history cache: %s", e)
                r = None

        qs = CompanyIGAccount.objects.filter(is_active=True)
        if opts.get("accounts"):
            qs = qs.filter(pk__in=opts["accounts"])
        accounts = await sync_to_async(list)(qs)

        if not accounts:
            logger.info("No active accounts found.")
            return

        workers = [AccountWorker(acc, channel_layer, r) for acc in accounts]
        tasks = [asyncio.create_task(w.run()) for w in workers]

        logger.info("IG poller started for %d account(s).", len(tasks))
        try:
            await asyncio.gather(*tasks)
        finally:
            if r:
                await r.close()
