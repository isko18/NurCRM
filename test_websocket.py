#!/usr/bin/env python3
"""
Скрипт для тестирования WebSocket соединений cafe приложения.

Использование:
    python test_websocket.py --url ws://localhost:8000/ws/cafe/orders/ --token YOUR_JWT_TOKEN
    
Или интерактивно:
    python test_websocket.py
"""

import asyncio
import json
import argparse
import websockets
from websockets.exceptions import ConnectionClosed


async def test_websocket(url, token=None, branch_id=None):
    """Тестирует WebSocket соединение"""
    
    # Формируем URL с параметрами
    if token:
        url = f"{url}?token={token}"
        if branch_id:
            url = f"{url}&branch_id={branch_id}"
    
    print(f"🔌 Подключение к: {url}")
    print(f"{'='*60}")
    
    try:
        async with websockets.connect(url) as websocket:
            print("✅ WebSocket подключен успешно!")
            
            # Ждем сообщение о подключении
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(message)
                print(f"\n📨 Получено сообщение о подключении:")
                print(json.dumps(data, indent=2, ensure_ascii=False))
                
                if data.get("type") == "connection_established":
                    print("\n✅ Подключение подтверждено!")
                    print(f"   Company ID: {data.get('company_id')}")
                    print(f"   Branch ID: {data.get('branch_id')}")
                    print(f"   Group: {data.get('group')}")
            except asyncio.TimeoutError:
                print("⏱️  Таймаут ожидания сообщения о подключении")
            
            # Тест ping/pong
            print(f"\n{'='*60}")
            print("📤 Отправка ping...")
            await websocket.send(json.dumps({"action": "ping"}))
            
            try:
                pong = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                pong_data = json.loads(pong)
                print(f"📨 Получен ответ: {json.dumps(pong_data, indent=2, ensure_ascii=False)}")
                if pong_data.get("type") == "pong":
                    print("✅ Ping/Pong работает!")
            except asyncio.TimeoutError:
                print("⏱️  Таймаут ожидания pong")
            
            # Ждем сообщения в течение 10 секунд
            print(f"\n{'='*60}")
            print("👂 Ожидание уведомлений (10 секунд)...")
            print("   (Создайте заказ или измените статус стола, чтобы увидеть уведомления)")
            
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                        data = json.loads(message)
                        print(f"\n📨 Получено уведомление:")
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    except asyncio.TimeoutError:
                        break
            except ConnectionClosed:
                print("\n❌ Соединение закрыто сервером")
                
    except websockets.exceptions.InvalidURI:
        print(f"❌ Неверный URL: {url}")
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"❌ Ошибка подключения: {e.status_code}")
        if e.status_code == 403:
            print("   Возможно, неверный токен или нет доступа")
    except Exception as e:
        print(f"❌ Ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description='Тестирование WebSocket для cafe приложения')
    parser.add_argument('--url', default='ws://localhost:8000/ws/cafe/orders/', 
                       help='URL WebSocket (по умолчанию: ws://localhost:8000/ws/cafe/orders/)')
    parser.add_argument('--token', help='JWT токен для аутентификации')
    parser.add_argument('--branch-id', help='ID филиала (опционально)')
    
    args = parser.parse_args()
    
    # Интерактивный режим
    if not args.token:
        print("🔐 Введите JWT токен для аутентификации:")
        args.token = input("Token: ").strip()
    
    if not args.token:
        print("❌ Токен обязателен для подключения")
        return
    
    # Запускаем тест
    asyncio.run(test_websocket(args.url, args.token, args.branch_id))


if __name__ == "__main__":
    main()
