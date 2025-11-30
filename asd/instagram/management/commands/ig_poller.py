# apps/instagram/management/commands/ig_poller.py
import os
import asyncio
import logging
import random
from datetime import datetime, timezone as dt_tz
from typing import Dict, Optional

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

# Интервалы/лимиты
INBOX_INTERVAL_ACTIVE = float(os.getenv("IG_INBOX_ACTIVE", "0.5"))
INBOX_INTERVAL_IDLE   = float(os.getenv("IG_INBOX_IDLE", "0.2"))
THREAD_POLL_INTERVAL  = float(os.getenv("IG_THREAD_POLL", "0.1"))
POLL_LIMIT            = int(os.getenv("IG_POLL_LIMIT", "6"))
REDIS_URL             = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_HIST              = int(os.getenv("IG_HIST_CACHE", "400"))

# Имена групп/ключей
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
        dt = timezone.make_aware(dt)
    return int(dt.timestamp() * 1000)


def ts_json(val):
    """
    Унификация временных меток:
      - datetime -> epoch ms (int)
      - int/float -> int
      - None -> None
      - прочее -> вернуть как есть
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, datetime):
        return _dt_to_epoch_ms(val)
    return val


def serialize_dt(dt_obj: datetime | str | None) -> str | None:
    """
    Для хранения в WM_HASH используем ISO-строку.
    """
    if not dt_obj:
        return None
    if isinstance(dt_obj, datetime):
        if timezone.is_naive(dt_obj):
            dt_obj = timezone.make_aware(dt_obj)
        return dt_obj.isoformat()
    return str(dt_obj)


def parse_dt(val) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _to_dt(val) -> Optional[datetime]:
    """
    Универсальная конверсия: epoch(sec/ms/us) ИЛИ ISO -> aware datetime UTC.
    Нужна для значений created_at, которые приходят как epoch-ms (int).
    """
    if not val:
        return None
    if isinstance(val, datetime):
        return timezone.make_aware(val) if timezone.is_naive(val) else val
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
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def dumps(obj: dict) -> str:
    if orjson:
        return orjson.dumps(obj).decode("utf-8")
    import json
    return json.dumps(obj, separators=(",", ":"))


class AccountWorker:
    """
    Воркер на IG-аккаунт:
      — опрашивает inbox и активные треды через IGChatService
      — синкает БД (IGThread/IGMessage)
      — шлёт события в Channels-группу (created_at/last_activity уже epoch-ms)
      — ведёт Redis-кэш истории (LIST) и снапшот списка тредов (ZSET + per-thread key)
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
        """
        Кладём нормализованную запись в Redis LIST (последние MAX_HIST).
        created_at — epoch ms.
        """
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
        """
        Обновляем ZSET (по времени) и персистим компактный снапшот треда.
        last_activity кладём как epoch-ms в JSON, а score — в секундах.
        """
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
                base_sleep = INBOX_INTERVAL_ACTIVE if active else INBOX_INTERVAL_IDLE

                updates_found = await self._tick_inbox(active_threads=active)
                if active:
                    await self._tick_active_threads(active)

                sleep_for = inbox_backoff or (max(0.6, base_sleep * 0.5) if updates_found else base_sleep)
                # небольшой джиттер, чтобы рассинхронизировать аккаунты/воркеры
                sleep_for *= 0.85 + 0.3 * random.random()
                await asyncio.sleep(sleep_for)
                inbox_backoff = 0.0
            except Exception as e:
                logger.exception("account loop error (%s): %s", getattr(self.account, "username", self.account.pk), e)
                inbox_backoff = min((inbox_backoff or 0.5) * 2, 8.0)
                await asyncio.sleep(inbox_backoff)

    async def _tick_inbox(self, active_threads: set[str]) -> bool:
        updates_found = False

        # Забираем треды
        threads = await asyncio.to_thread(self.cl.fetch_threads_live, 30)

        for t in threads:
            tid = t["thread_id"]
            # Нормализуем last_activity к datetime для сравнения
            last = parse_dt(t.get("last_activity"))
            prev = self.threads_cache.get(tid)

            # Для sync в БД лучше передать корректный datetime
            t_for_db = {**t, "last_activity": last}
            th = await self._sync_thread(t_for_db)

            # users cache
            u_map = self.thread_users.get(tid)
            users = t.get("users")
            if u_map is None:
                if not users:
                    users = await asyncio.to_thread(self.cl.fetch_thread_users, tid)
                u_map = {u["pk"]: u["username"] for u in (users or [])}
                self.thread_users[tid] = u_map
            elif users is None:
                users = [{"pk": pk, "username": name} for pk, name in u_map.items()]

            # поддерживаем Redis snapshot
            await self._snapshot_thread(t_for_db, users, last)

            # Новый тред
            if prev is None:
                preview = await asyncio.to_thread(self.cl.fetch_last_text, tid, u_map)
                if preview:
                    # в БД — как есть (datetime), в Redis/клиента — нормализуем ниже
                    await self._sync_msg(th, preview)
                    wm = _to_dt(preview.get("created_at"))   # FIX: epoch-ms -> datetime
                    if wm:
                        await self._set_wm(tid, wm)
                    await self._hist_push(tid, preview, u_map)

                await self.group_send({
                    "type": "thread_new",
                    "thread": {
                        **t,
                        "last_activity": ts_json(last),   # epoch-ms
                        "users": users,
                        "preview": (
                            {**preview, "created_at": ts_json(preview["created_at"])}
                            if preview else None
                        ),
                    }
                })

                if preview and (tid not in active_threads):
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
                if tid not in active_threads:
                    since_dt = await self._get_wm(tid) or prev
                    new_msgs = await asyncio.to_thread(
                        self.cl.fetch_messages_live,
                        tid,
                        POLL_LIMIT,
                        user_map=None,
                        include_usernames=False,
                        since=since_dt,
                    )
                    if new_msgs:
                        for m in new_msgs:
                            await self._sync_msg(th, m)
                            await self._hist_push(tid, m, u_map)

                        await self.group_send({
                            "type": "incoming_batch",
                            "thread_id": tid,
                            "messages": [
                                {**m, "created_at": ts_json(m.get("created_at")), "username": u_map.get(m.get("sender_pk"))}
                                for m in new_msgs
                            ],
                        })

                        last_created = _to_dt(new_msgs[-1].get("created_at"))  # FIX
                        if last_created:
                            await self._set_wm(tid, last_created)

                preview = await asyncio.to_thread(self.cl.fetch_last_text, tid, u_map)
                await self.group_send({
                    "type": "thread_update",
                    "thread_id": tid,
                    "last_activity": ts_json(last),   # epoch-ms
                    "preview": (
                        {**preview, "created_at": ts_json(preview["created_at"])}
                        if preview else None
                    ),
                    "has_new": True,
                })

                self.threads_cache[tid] = last
                updates_found = True

        return updates_found

    async def _tick_active_threads(self, active_threads: set[str]):
        tasks = [self._poll_thread_once(tid) for tid in list(active_threads)[:20]]
        if tasks:
            await asyncio.gather(*tasks)

    async def _poll_thread_once(self, thread_id: str):
        try:
            th = await asyncio.to_thread(IGThread.objects.get, ig_account=self.account, thread_id=thread_id)
        except IGThread.DoesNotExist:
            th = await self._sync_thread({
                "thread_id": thread_id,
                "title": "",
                "users": [{"pk": pk, "username": name} for pk, name in (self.thread_users.get(thread_id, {})).items()],
                "last_activity": timezone.localtime(),
            })

        u_map = await self._ensure_thread_users(thread_id)

        since_dt = await self._get_wm(thread_id)
        msgs = await asyncio.to_thread(
            self.cl.fetch_messages_live,
            thread_id,
            POLL_LIMIT,
            user_map=None,
            include_usernames=False,
            since=since_dt,
        )

        if msgs:
            for m in msgs:
                await self._sync_msg(th, m)
                await self._hist_push(thread_id, m, u_map)

            await self.group_send({
                "type": "incoming_batch",
                "thread_id": thread_id,
                "messages": [
                    {**m, "created_at": ts_json(m.get("created_at")), "username": u_map.get(m.get("sender_pk"))}
                    for m in msgs
                ],
            })

            last_created = _to_dt(msgs[-1].get("created_at"))  # FIX
            if last_created:
                await self._set_wm(thread_id, last_created)

        # Поддержка Redis snapshot по last_activity
        now_dt = timezone.localtime()
        await self._snapshot_thread(
            {"thread_id": thread_id, "title": ""},
            [{"pk": pk, "username": name} for pk, name in u_map.items()],
            now_dt,
        )

        # Обновим last_activity в БД без гидрации
        await asyncio.to_thread(
            lambda: IGThread.objects.filter(pk=th.pk).update(last_activity=now_dt)
        )
        await asyncio.sleep(THREAD_POLL_INTERVAL)


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
