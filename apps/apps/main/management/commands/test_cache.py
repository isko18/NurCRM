"""
Management команда для тестирования Redis кэша.
Использование: python manage.py test_cache
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.conf import settings
import time


class Command(BaseCommand):
    help = 'Тестирование Redis кэша'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Тестирование Redis кэша'))
        self.stdout.write(self.style.SUCCESS('=' * 50))

        # Проверка конфигурации
        cache_backend = settings.CACHES['default']['BACKEND']
        cache_location = settings.CACHES['default']['LOCATION']
        cache_options = settings.CACHES['default'].get('OPTIONS', {})
        
        self.stdout.write(f'\nBackend: {cache_backend}')
        self.stdout.write(f'Location: {cache_location}')
        self.stdout.write(f'Key Prefix: {settings.CACHES["default"].get("KEY_PREFIX", "нет")}')
        self.stdout.write(f'Ignore Exceptions: {cache_options.get("IGNORE_EXCEPTIONS", False)}')

        # Проверка подключения к Redis напрямую
        self.stdout.write('\n--- Проверка подключения к Redis ---')
        try:
            import redis
            from urllib.parse import urlparse
            
            parsed = urlparse(cache_location)
            redis_host = parsed.hostname or '127.0.0.1'
            redis_port = parsed.port or 6379
            redis_db = int(parsed.path.lstrip('/')) if parsed.path else 1
            redis_password = parsed.password
            
            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                decode_responses=False  # Получаем bytes для проверки
            )
            r.ping()
            self.stdout.write(self.style.SUCCESS(f'✓ Прямое подключение к Redis успешно (DB {redis_db})'))
            
            # Проверка ключей в Redis
            keys = r.keys('nurcrm:*')
            self.stdout.write(f'  Найдено ключей с префиксом "nurcrm:": {len(keys)}')
            
        except ImportError:
            self.stdout.write(self.style.WARNING('⚠ Библиотека redis не установлена, пропускаем прямую проверку'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Ошибка подключения к Redis: {e}'))
            self.stdout.write(self.style.WARNING('⚠ Проверьте, что Redis запущен и доступен'))

        # Тест 1: Запись
        self.stdout.write('\n--- Тест 1: Запись ---')
        try:
            result = cache.set('test_key', 'test_value', 60)
            if result:
                self.stdout.write(self.style.SUCCESS('✓ Тест 1: Запись в кэш успешна'))
            else:
                self.stdout.write(self.style.ERROR('✗ Тест 1: Запись вернула False (возможно, Redis недоступен)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Тест 1: Ошибка записи: {e}'))
            import traceback
            self.stdout.write(traceback.format_exc())

        # Тест 2: Чтение сразу после записи
        self.stdout.write('\n--- Тест 2: Чтение ---')
        try:
            # Небольшая задержка для надежности
            time.sleep(0.1)
            value = cache.get('test_key')
            self.stdout.write(f'  Полученное значение: {repr(value)}')
            if value == 'test_value':
                self.stdout.write(self.style.SUCCESS('✓ Тест 2: Чтение из кэша успешно'))
            elif value is None:
                self.stdout.write(self.style.ERROR('✗ Тест 2: Значение None (ключ не найден или истек)'))
                # Попробуем проверить напрямую в Redis
                try:
                    import redis
                    from urllib.parse import urlparse
                    parsed = urlparse(cache_location)
                    r = redis.Redis(
                        host=parsed.hostname or '127.0.0.1',
                        port=parsed.port or 6379,
                        db=int(parsed.path.lstrip('/')) if parsed.path else 1,
                        password=parsed.password,
                        decode_responses=False
                    )
                    full_key = f"nurcrm:1:test_key"  # Префикс + версия + ключ
                    redis_value = r.get(full_key)
                    if redis_value:
                        self.stdout.write(f'  ⚠ Ключ найден в Redis напрямую: {redis_value}')
                        self.stdout.write(self.style.WARNING('  Возможна проблема с сериализацией/десериализацией'))
                    else:
                        # Попробуем найти ключ без версии
                        keys = r.keys('nurcrm:*test_key*')
                        if keys:
                            self.stdout.write(f'  ⚠ Найдены похожие ключи: {keys}')
                        else:
                            self.stdout.write('  ⚠ Ключ не найден в Redis')
                except:
                    pass
            else:
                self.stdout.write(self.style.ERROR(f'✗ Тест 2: Неверное значение: {value}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Тест 2: Ошибка чтения: {e}'))
            import traceback
            self.stdout.write(traceback.format_exc())

        # Тест 3: Удаление
        self.stdout.write('\n--- Тест 3: Удаление ---')
        try:
            cache.delete('test_key')
            time.sleep(0.1)
            value = cache.get('test_key')
            if value is None:
                self.stdout.write(self.style.SUCCESS('✓ Тест 3: Удаление из кэша успешно'))
            else:
                self.stdout.write(self.style.ERROR(f'✗ Тест 3: Ключ не удален, значение: {value}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Тест 3: Ошибка удаления: {e}'))

        # Тест 4: Массовая запись (упрощенный)
        self.stdout.write('\n--- Тест 4: Массовая запись ---')
        try:
            test_data = {f'key_{i}': f'value_{i}' for i in range(5)}  # Уменьшили до 5
            result = cache.set_many(test_data, 60)
            if result:
                self.stdout.write('  Массовая запись выполнена')
            time.sleep(0.1)
            retrieved = cache.get_many(list(test_data.keys()))
            self.stdout.write(f'  Записано: {len(test_data)}, Прочитано: {len(retrieved)}')
            if len(retrieved) == len(test_data):
                self.stdout.write(self.style.SUCCESS('✓ Тест 4: Массовая запись/чтение успешна'))
                # Очистка
                cache.delete_many(list(test_data.keys()))
            else:
                self.stdout.write(self.style.ERROR(f'✗ Тест 4: Не все ключи сохранены: {len(retrieved)}/{len(test_data)}'))
                if retrieved:
                    self.stdout.write(f'  Сохраненные ключи: {list(retrieved.keys())}')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Тест 4: Ошибка массовой операции: {e}'))
            import traceback
            self.stdout.write(traceback.format_exc())

        # Тест 5: Проверка таймаута
        self.stdout.write('\n--- Тест 5: Проверка таймаута ---')
        try:
            cache.set('timeout_test', 'value', 2)
            time.sleep(3)
            value = cache.get('timeout_test')
            if value is None:
                self.stdout.write(self.style.SUCCESS('✓ Тест 5: Таймаут работает корректно'))
            else:
                self.stdout.write(self.style.WARNING('⚠ Тест 5: Таймаут не сработал (возможно, нормально)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Тест 5: Ошибка проверки таймаута: {e}'))

        # Рекомендации
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS('Тестирование завершено'))
        self.stdout.write('=' * 50)
        
        # Проверка настроек
        if cache_options.get('IGNORE_EXCEPTIONS'):
            self.stdout.write(self.style.WARNING('\n⚠ ВНИМАНИЕ: IGNORE_EXCEPTIONS=True'))
            self.stdout.write('  Это означает, что ошибки Redis будут игнорироваться.')
            self.stdout.write('  Для диагностики временно установите IGNORE_EXCEPTIONS=False')
        
        if cache_options.get('COMPRESSOR'):
            self.stdout.write(f'\nℹ Используется компрессор: {cache_options.get("COMPRESSOR")}')
            self.stdout.write('  Если есть проблемы, попробуйте временно отключить компрессор')

