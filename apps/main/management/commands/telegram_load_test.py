"""
Нагрузочный тест серверного бота (ТЗ-BE-2026-07, п. 1.1.9).

Эмулирует Telegram: N ботов × M сообщений за окно, плюс повторные доставки.
Реальной отправки в Telegram и вызовов ИИ нет: send_message подменяется задержкой,
очередь celery — потоками-воркерами в процессе.

Цели: 95 % ответов ≤ 5 с, ни одного потерянного и ни одного дубля.

Пишет в базу (временные компании и боты, удаляются в конце) — запускать на
тестовой базе или staging:
    USE_SQLITE=1 python manage.py telegram_load_test --bots 1000 --messages 5 --yes
"""
import queue
import random
import statistics
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.test import Client


class Command(BaseCommand):
    help = "Нагрузочный тест вебхука и воркера серверного бота (эмуляция Telegram)."

    def add_arguments(self, parser):
        parser.add_argument("--bots", type=int, default=1000)
        parser.add_argument("--messages", type=int, default=5)
        parser.add_argument("--window", type=float, default=60.0, help="за сколько секунд прислать все сообщения")
        parser.add_argument("--workers", type=int, default=8, help="потоков-воркеров очереди")
        parser.add_argument("--senders", type=int, default=32, help="параллельных «доставок Telegram»")
        parser.add_argument("--duplicates", type=float, default=0.1, help="доля повторных доставок")
        parser.add_argument("--send-latency", type=float, default=0.05, help="эмуляция sendMessage, с")
        parser.add_argument("--yes", action="store_true", help="подтверждаю запись временных данных в базу")

    def handle(self, *args, **opts):
        if not opts["yes"]:
            raise CommandError("Команда создаёт временные компании и ботов. Запустите с --yes на тестовой базе.")

        from django.contrib.auth import get_user_model
        from apps.users.models import Company
        from apps.main.telegram_bot.models import TelegramBotSettings
        from apps.main.telegram_bot import tasks as tg_tasks

        User = get_user_model()
        run = uuid.uuid4().hex[:8]
        n_bots, n_msgs = opts["bots"], opts["messages"]

        self.stdout.write(f"Создаю {n_bots} ботов (run={run})…")
        owners = []
        for i in range(n_bots):
            u = User(email=f"loadtest-{run}-{i}@nurcrm.invalid")
            u.set_unusable_password()
            owners.append(u)
        owners = User.objects.bulk_create(owners)
        companies = Company.objects.bulk_create(
            [Company(name=f"LT {run} {i}", slug=f"lt-{run}-{i}", owner=owners[i]) for i in range(n_bots)]
        )
        bots = []
        for c in companies:
            b = TelegramBotSettings(
                company=c, mode=TelegramBotSettings.Mode.SERVER, secret_token="lt",
                owner_chat_id="1", ai_enabled=False, consultant_enabled=False,
            )
            b.token = f"{random.randint(10**8, 10**9)}:LOADTEST"
            bots.append(b)
        TelegramBotSettings.objects.bulk_create(bots)

        broker = queue.Queue()
        sent_at, replied = {}, Counter()
        reply_lat, intake_lat = [], []
        lock = threading.Lock()

        def fake_delay(settings_id, data, enqueued_at=None):
            broker.put((settings_id, data, enqueued_at))

        def fake_send(token, chat_id, text, *a, **kw):
            time.sleep(opts["send_latency"])
            return {"ok": True}

        def worker():
            while True:
                item = broker.get()
                if item is None:
                    return
                settings_id, data, enq = item
                try:
                    tg_tasks.process_telegram_update(settings_id, data, enq)
                finally:
                    close_old_connections()
                    broker.task_done()

        # Ответ фиксируется в момент «отправки» владельцу/покупателю
        def tracking_send(token, chat_id, text, *a, **kw):
            res = fake_send(token, chat_id, text)
            key = threading.current_thread().__dict__.get("lt_key")
            if key:
                with lock:
                    if replied[key] == 0:
                        reply_lat.append(time.time() - sent_at[key])
                    replied[key] += 1
            return res

        orig_process = tg_tasks.process_telegram_update.run

        def process_tracked(settings_id, data, enqueued_at=None):
            threading.current_thread().__dict__["lt_key"] = (settings_id, data.get("update_id"))
            return orig_process(settings_id, data, enqueued_at)

        deliveries = []
        for b in bots:
            for m in range(n_msgs):
                deliveries.append((b, 1000 + m))
        dup_count = int(len(deliveries) * opts["duplicates"])
        deliveries += random.sample(deliveries, dup_count)
        random.shuffle(deliveries)
        gap = opts["window"] / max(len(deliveries), 1)

        def deliver(idx_item):
            idx, (bot, update_id) = idx_item
            target = start + idx * gap
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
            key = (str(bot.id), update_id)
            with lock:
                sent_at.setdefault(key, time.time())
            payload = {
                "update_id": update_id,
                "message": {"date": int(time.time()), "chat": {"id": "1"}, "from": {"first_name": "LT"}, "text": "Ты работаешь?"},
            }
            t0 = time.time()
            resp = http.post(
                f"/api/telegram/webhook/{bot.bot_uuid}/", payload, content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="lt", secure=True,
            )
            with lock:
                intake_lat.append(time.time() - t0)
            close_old_connections()
            return resp.status_code

        http = Client()
        try:
            with patch.object(tg_tasks.process_telegram_update, "delay", side_effect=fake_delay), \
                    patch.object(tg_tasks.process_telegram_update, "run", side_effect=process_tracked), \
                    patch("apps.main.telegram_bot.services.telegram_api.send_message", side_effect=tracking_send), \
                    patch("apps.main.telegram_bot.services.telegram_api.send_voice", return_value={"ok": True}):
                threads = [threading.Thread(target=worker, daemon=True) for _ in range(opts["workers"])]
                for t in threads:
                    t.start()
                self.stdout.write(f"Доставка {len(deliveries)} обновлений ({dup_count} повторов) за {opts['window']:.0f} с…")
                start = time.time()
                with ThreadPoolExecutor(max_workers=opts["senders"]) as pool:
                    codes = Counter(pool.map(deliver, enumerate(deliveries)))
                broker.join()
                for _ in threads:
                    broker.put(None)
                elapsed = time.time() - start
        finally:
            TelegramBotSettings.objects.filter(company__in=companies).delete()
            Company.objects.filter(id__in=[c.id for c in companies]).delete()
            User.objects.filter(id__in=[u.id for u in owners]).delete()

        expected = n_bots * n_msgs
        lost = expected - len([k for k in sent_at if replied[k] > 0])
        dupes = sum(1 for k in sent_at if replied[k] > 1)
        reply_lat.sort()
        intake_lat.sort()

        def pct(arr, p):
            return arr[min(len(arr) - 1, int(len(arr) * p))] if arr else 0

        self.stdout.write(f"\nВремя прогона: {elapsed:.1f} с; коды вебхука: {dict(codes)}")
        self.stdout.write(f"Приём вебхука: p50 {pct(intake_lat, .5) * 1000:.0f} мс, p95 {pct(intake_lat, .95) * 1000:.0f} мс")
        self.stdout.write(
            f"Ответ: p50 {pct(reply_lat, .5):.2f} с, p95 {pct(reply_lat, .95):.2f} с, max {reply_lat[-1] if reply_lat else 0:.2f} с, "
            f"среднее {statistics.mean(reply_lat) if reply_lat else 0:.2f} с"
        )
        self.stdout.write(f"Ожидалось ответов: {expected}; потеряно: {lost}; дублей: {dupes}")
        ok = lost == 0 and dupes == 0 and pct(reply_lat, .95) <= 5.0
        self.stdout.write(self.style.SUCCESS("ПРОЙДЕН") if ok else self.style.ERROR("НЕ ПРОЙДЕН"))
