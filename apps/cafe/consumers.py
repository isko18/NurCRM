# apps/cafe/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

User = get_user_model()


class CafeOrderConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для уведомлений о заказах в кафе.
    Подключается по company_id и опционально branch_id.
    URL: ws/cafe/orders/?token=<JWT>&company_id=<uuid>&branch_id=<uuid>
    """
    
    async def connect(self):
        # Получаем пользователя из scope (уже обработан JWTAuthMiddleware)
        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser):
            await self.close(code=4003)
            return
        
        # Получаем company_id и branch_id из query string
        query_string = self.scope.get("query_string", b"").decode()
        params = {}
        if query_string:
            from urllib.parse import parse_qs
            parsed = parse_qs(query_string)
            params = {k: v[0] if v else None for k, v in parsed.items()}
        
        company_id = params.get("company_id")
        branch_id = params.get("branch_id")
        
        if not company_id:
            await self.close(code=4004)
            return
        
        # Проверяем, что пользователь имеет доступ к этой компании
        has_access = await self._check_company_access(user, company_id)
        if not has_access:
            await self.close(code=4005)
            return
        
        self.company_id = company_id
        self.branch_id = branch_id
        
        # Формируем имя группы для подписки
        if branch_id:
            self.group_name = f"cafe_orders_{company_id}_{branch_id}"
        else:
            self.group_name = f"cafe_orders_{company_id}"
        
        # Подписываемся на группу
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        # Отправляем подтверждение подключения
        await self.send(json.dumps({
            "type": "connection_established",
            "company_id": company_id,
            "branch_id": branch_id,
            "group": self.group_name
        }))
    
    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )
    
    async def receive(self, text_data=None, bytes_data=None):
        """Обработка входящих сообщений от клиента"""
        if not text_data:
            return
        
        try:
            data = json.loads(text_data)
            action = data.get("action")
            
            if action == "ping":
                await self.send(json.dumps({"type": "pong"}))
        except Exception:
            pass
    
    async def order_created(self, event):
        """Отправка уведомления о создании заказа"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "order_created",
            "data": payload
        }))
    
    async def order_updated(self, event):
        """Отправка уведомления об обновлении заказа"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "order_updated",
            "data": payload
        }))
    
    async def table_status_changed(self, event):
        """Отправка уведомления об изменении статуса стола (FREE/BUSY) - для отслеживания занятости"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "table_status_changed",
            "data": payload
        }))
    
    @database_sync_to_async
    def _check_company_access(self, user, company_id):
        """Проверка доступа пользователя к компании"""
        try:
            # Проверяем, что пользователь принадлежит компании
            user_company = getattr(user, "company", None) or getattr(user, "owned_company", None)
            if user_company and str(user_company.id) == company_id:
                return True
            
            # Проверяем через branch
            if hasattr(user, "branch") and user.branch:
                if str(user.branch.company_id) == company_id:
                    return True
            
            # Проверяем через branch_memberships
            if hasattr(user, "branch_memberships"):
                from apps.users.models import Branch
                from django.db.models import Q
                memberships = user.branch_memberships.filter(
                    branch__company_id=company_id
                ).exists()
                if memberships:
                    return True
            
            return False
        except Exception:
            return False


class CafeTableConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для уведомлений о столах в кафе.
    Подключается по company_id и опционально branch_id.
    URL: ws/cafe/tables/?token=<JWT>&company_id=<uuid>&branch_id=<uuid>
    """
    
    async def connect(self):
        # Получаем пользователя из scope (уже обработан JWTAuthMiddleware)
        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser):
            await self.close(code=4003)
            return
        
        # Получаем company_id и branch_id из query string
        query_string = self.scope.get("query_string", b"").decode()
        params = {}
        if query_string:
            from urllib.parse import parse_qs
            parsed = parse_qs(query_string)
            params = {k: v[0] if v else None for k, v in parsed.items()}
        
        company_id = params.get("company_id")
        branch_id = params.get("branch_id")
        
        if not company_id:
            await self.close(code=4004)
            return
        
        # Проверяем, что пользователь имеет доступ к этой компании
        has_access = await self._check_company_access(user, company_id)
        if not has_access:
            await self.close(code=4005)
            return
        
        self.company_id = company_id
        self.branch_id = branch_id
        
        # Формируем имя группы для подписки
        if branch_id:
            self.group_name = f"cafe_tables_{company_id}_{branch_id}"
        else:
            self.group_name = f"cafe_tables_{company_id}"
        
        # Подписываемся на группу
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        # Отправляем подтверждение подключения
        await self.send(json.dumps({
            "type": "connection_established",
            "company_id": company_id,
            "branch_id": branch_id,
            "group": self.group_name
        }))
    
    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )
    
    async def receive(self, text_data=None, bytes_data=None):
        """Обработка входящих сообщений от клиента"""
        if not text_data:
            return
        
        try:
            data = json.loads(text_data)
            action = data.get("action")
            
            if action == "ping":
                await self.send(json.dumps({"type": "pong"}))
        except Exception:
            pass
    
    async def table_created(self, event):
        """Отправка уведомления о создании стола"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "table_created",
            "data": payload
        }))
    
    async def table_updated(self, event):
        """Отправка уведомления об обновлении стола"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "table_updated",
            "data": payload
        }))
    
    async def table_status_changed(self, event):
        """Отправка уведомления об изменении статуса стола (FREE/BUSY)"""
        payload = event.get("payload", {})
        await self.send(json.dumps({
            "type": "table_status_changed",
            "data": payload
        }))
    
    @database_sync_to_async
    def _check_company_access(self, user, company_id):
        """Проверка доступа пользователя к компании"""
        try:
            # Проверяем, что пользователь принадлежит компании
            user_company = getattr(user, "company", None) or getattr(user, "owned_company", None)
            if user_company and str(user_company.id) == company_id:
                return True
            
            # Проверяем через branch
            if hasattr(user, "branch") and user.branch:
                if str(user.branch.company_id) == company_id:
                    return True
            
            # Проверяем через branch_memberships
            if hasattr(user, "branch_memberships"):
                from apps.users.models import Branch
                from django.db.models import Q
                memberships = user.branch_memberships.filter(
                    branch__company_id=company_id
                ).exists()
                if memberships:
                    return True
            
            return False
        except Exception:
            return False
