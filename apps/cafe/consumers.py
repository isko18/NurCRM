# apps/cafe/consumers.py
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


class CafeOrderConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для уведомлений о заказах в кафе.
    Company и branch определяются автоматически из JWT токена пользователя.
    Опционально можно указать branch_id в query для выбора конкретного филиала (для owner/admin).
    URL: ws/cafe/orders/?token=<JWT>&branch_id=<uuid> (опционально)
    """
    
    async def connect(self):
        # Получаем пользователя из scope (уже обработан JWTAuthMiddleware)
        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser):
            logger.warning(f"[CafeOrderConsumer] Connection rejected: No user")
            await self.close(code=4003)
            return
        
        # Получаем company и branch из пользователя
        company, branch = await self._get_user_company_and_branch(user)
        
        if not company:
            logger.warning(f"[CafeOrderConsumer] Connection rejected: User {user.id} has no company")
            await self.close(code=4004, reason="User has no company")
            return
        
        # Опционально можно указать branch_id в query для выбора конкретного филиала
        query_string = self.scope.get("query_string", b"").decode()
        branch_id_from_query = None
        if query_string:
            from urllib.parse import parse_qs
            parsed = parse_qs(query_string)
            branch_id_from_query = parsed.get("branch_id", [None])[0]
        
        # Если указан branch_id в query и пользователь owner/admin - используем его
        if branch_id_from_query:
            is_owner_like = await self._is_owner_like(user)
            if is_owner_like:
                branch_from_query = await self._get_branch_by_id(branch_id_from_query, company.id)
                if branch_from_query:
                    branch = branch_from_query
        
        self.company_id = str(company.id)
        self.branch_id = str(branch.id) if branch else None
        
        # Формируем имя группы для подписки
        if self.branch_id:
            self.group_name = f"cafe_orders_{self.company_id}_{self.branch_id}"
        else:
            self.group_name = f"cafe_orders_{self.company_id}"
        
        # Подписываемся на группу
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        logger.info(f"[CafeOrderConsumer] Connected: user={user.id}, company={self.company_id}, branch={self.branch_id}, group={self.group_name}")
        
        # Отправляем подтверждение подключения
        await self.send(json.dumps({
            "type": "connection_established",
            "company_id": self.company_id,
            "branch_id": self.branch_id,
            "group": self.group_name
        }))
    
    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            logger.info(f"[CafeTableConsumer] Disconnected: group={self.group_name}, code={code}")
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
        try:
            payload = event.get("payload", {})
            logger.info(f"[CafeOrderConsumer] Received order_created event: order_id={payload.get('order', {}).get('id')}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "order_created",
                "data": payload
            }))
            logger.info(f"[CafeOrderConsumer] Sent order_created notification to client")
        except Exception as e:
            logger.error(f"[CafeOrderConsumer] Error in order_created: {e}", exc_info=True)
    
    async def order_updated(self, event):
        """Отправка уведомления об обновлении заказа"""
        try:
            payload = event.get("payload", {})
            logger.info(f"[CafeOrderConsumer] Received order_updated event: order_id={payload.get('order', {}).get('id')}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "order_updated",
                "data": payload
            }))
            logger.info(f"[CafeOrderConsumer] Sent order_updated notification to client")
        except Exception as e:
            logger.error(f"[CafeOrderConsumer] Error in order_updated: {e}", exc_info=True)
    
    async def table_status_changed(self, event):
        """Отправка уведомления об изменении статуса стола (FREE/BUSY) - для отслеживания занятости"""
        try:
            payload = event.get("payload", {})
            table_id = payload.get("table_id") or payload.get("table", {}).get("id")
            logger.info(f"[CafeOrderConsumer] Received table_status_changed event: table_id={table_id}, status={payload.get('status')}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "table_status_changed",
                "data": payload
            }))
            logger.info(f"[CafeOrderConsumer] Sent table_status_changed notification to client")
        except Exception as e:
            logger.error(f"[CafeOrderConsumer] Error in table_status_changed: {e}", exc_info=True)

    async def kitchen_task_ready(self, event):
        """Отправка уведомления о готовности блюда (задачи кухни)"""
        try:
            payload = event.get("payload", {})
            task_id = payload.get("task_id") or payload.get("task", {}).get("id")
            logger.info(f"[CafeOrderConsumer] Received kitchen_task_ready event: task_id={task_id}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "kitchen_task_ready",
                "data": payload
            }))
            logger.info(f"[CafeOrderConsumer] Sent kitchen_task_ready notification to client")
        except Exception as e:
            logger.error(f"[CafeOrderConsumer] Error in kitchen_task_ready: {e}", exc_info=True)
    
    @database_sync_to_async
    def _get_user_company_and_branch(self, user):
        """Получает company и branch из пользователя"""
        try:
            # Получаем компанию
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)
            if not company:
                return None, None
            
            # Получаем филиал
            branch = None
            
            # 1) primary_branch (property)
            primary = getattr(user, "primary_branch", None)
            if primary and getattr(primary, "company_id", None) == company.id:
                branch = primary
            
            # 2) user.branch
            if not branch:
                user_branch = getattr(user, "branch", None)
                if user_branch and getattr(user_branch, "company_id", None) == company.id:
                    branch = user_branch
            
            # 3) первый филиал из branch_memberships
            if not branch and hasattr(user, "branch_memberships"):
                membership = user.branch_memberships.filter(
                    branch__company_id=company.id
                ).select_related("branch").first()
                if membership and membership.branch:
                    branch = membership.branch
            
            return company, branch
        except Exception:
            return None, None
    
    @database_sync_to_async
    def _is_owner_like(self, user):
        """Проверяет, является ли пользователь owner/admin"""
        try:
            if getattr(user, "is_superuser", False):
                return True
            
            if getattr(user, "owned_company", None):
                return True
            
            if getattr(user, "is_admin", False):
                return True
            
            role = getattr(user, "role", None)
            if role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"):
                return True
            
            return False
        except Exception:
            return False
    
    @database_sync_to_async
    def _get_branch_by_id(self, branch_id, company_id):
        """Получает филиал по ID, если он принадлежит компании"""
        try:
            from apps.users.models import Branch
            return Branch.objects.get(id=branch_id, company_id=company_id)
        except Exception:
            return None


class CafeTableConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для уведомлений о столах в кафе.
    Company и branch определяются автоматически из JWT токена пользователя.
    Опционально можно указать branch_id в query для выбора конкретного филиала (для owner/admin).
    URL: ws/cafe/tables/?token=<JWT>&branch_id=<uuid> (опционально)
    """
    
    async def connect(self):
        # Получаем пользователя из scope (уже обработан JWTAuthMiddleware)
        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser):
            logger.warning(f"[CafeTableConsumer] Connection rejected: No user")
            await self.close(code=4003)
            return
        
        # Получаем company и branch из пользователя
        company, branch = await self._get_user_company_and_branch(user)
        
        if not company:
            logger.warning(f"[CafeTableConsumer] Connection rejected: User {user.id} has no company")
            await self.close(code=4004, reason="User has no company")
            return
        
        # Опционально можно указать branch_id в query для выбора конкретного филиала
        query_string = self.scope.get("query_string", b"").decode()
        branch_id_from_query = None
        if query_string:
            from urllib.parse import parse_qs
            parsed = parse_qs(query_string)
            branch_id_from_query = parsed.get("branch_id", [None])[0]
        
        # Если указан branch_id в query и пользователь owner/admin - используем его
        if branch_id_from_query:
            is_owner_like = await self._is_owner_like(user)
            if is_owner_like:
                branch_from_query = await self._get_branch_by_id(branch_id_from_query, company.id)
                if branch_from_query:
                    branch = branch_from_query
        
        self.company_id = str(company.id)
        self.branch_id = str(branch.id) if branch else None
        
        # Формируем имя группы для подписки
        if self.branch_id:
            self.group_name = f"cafe_tables_{self.company_id}_{self.branch_id}"
        else:
            self.group_name = f"cafe_tables_{self.company_id}"
        
        # Подписываемся на группу
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        logger.info(f"[CafeTableConsumer] Connected: user={user.id}, company={self.company_id}, branch={self.branch_id}, group={self.group_name}")
        
        # Отправляем подтверждение подключения
        await self.send(json.dumps({
            "type": "connection_established",
            "company_id": self.company_id,
            "branch_id": self.branch_id,
            "group": self.group_name
        }))
    
    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            logger.info(f"[CafeTableConsumer] Disconnected: group={self.group_name}, code={code}")
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
                logger.debug(f"[CafeTableConsumer] Received ping from client")
                await self.send(json.dumps({"type": "pong"}))
        except Exception as e:
            logger.error(f"[CafeTableConsumer] Error in receive: {e}", exc_info=True)
    
    async def table_created(self, event):
        """Отправка уведомления о создании стола"""
        try:
            payload = event.get("payload", {})
            table_id = payload.get("table_id") or payload.get("table", {}).get("id")
            logger.info(f"[CafeTableConsumer] Received table_created event: table_id={table_id}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "table_created",
                "data": payload
            }))
            logger.info(f"[CafeTableConsumer] Sent table_created notification to client")
        except Exception as e:
            logger.error(f"[CafeTableConsumer] Error in table_created: {e}", exc_info=True)
    
    async def table_updated(self, event):
        """Отправка уведомления об обновлении стола"""
        try:
            payload = event.get("payload", {})
            table_id = payload.get("table_id") or payload.get("table", {}).get("id")
            logger.info(f"[CafeTableConsumer] Received table_updated event: table_id={table_id}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "table_updated",
                "data": payload
            }))
            logger.info(f"[CafeTableConsumer] Sent table_updated notification to client")
        except Exception as e:
            logger.error(f"[CafeTableConsumer] Error in table_updated: {e}", exc_info=True)
    
    async def table_status_changed(self, event):
        """Отправка уведомления об изменении статуса стола (FREE/BUSY)"""
        try:
            payload = event.get("payload", {})
            table_id = payload.get("table_id") or payload.get("table", {}).get("id")
            status = payload.get("status")
            logger.info(f"[CafeTableConsumer] Received table_status_changed event: table_id={table_id}, status={status}, group={self.group_name}")
            await self.send(json.dumps({
                "type": "table_status_changed",
                "data": payload
            }))
            logger.info(f"[CafeTableConsumer] Sent table_status_changed notification to client")
        except Exception as e:
            logger.error(f"[CafeTableConsumer] Error in table_status_changed: {e}", exc_info=True)
    
    @database_sync_to_async
    def _get_user_company_and_branch(self, user):
        """Получает company и branch из пользователя"""
        try:
            # Получаем компанию
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)
            if not company:
                return None, None
            
            # Получаем филиал
            branch = None
            
            # 1) primary_branch (property)
            primary = getattr(user, "primary_branch", None)
            if primary and getattr(primary, "company_id", None) == company.id:
                branch = primary
            
            # 2) user.branch
            if not branch:
                user_branch = getattr(user, "branch", None)
                if user_branch and getattr(user_branch, "company_id", None) == company.id:
                    branch = user_branch
            
            # 3) первый филиал из branch_memberships
            if not branch and hasattr(user, "branch_memberships"):
                membership = user.branch_memberships.filter(
                    branch__company_id=company.id
                ).select_related("branch").first()
                if membership and membership.branch:
                    branch = membership.branch
            
            return company, branch
        except Exception:
            return None, None
    
    @database_sync_to_async
    def _is_owner_like(self, user):
        """Проверяет, является ли пользователь owner/admin"""
        try:
            if getattr(user, "is_superuser", False):
                return True
            
            if getattr(user, "owned_company", None):
                return True
            
            if getattr(user, "is_admin", False):
                return True
            
            role = getattr(user, "role", None)
            if role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"):
                return True
            
            return False
        except Exception:
            return False
    
    @database_sync_to_async
    def _get_branch_by_id(self, branch_id, company_id):
        """Получает филиал по ID, если он принадлежит компании"""
        try:
            from apps.users.models import Branch
            return Branch.objects.get(id=branch_id, company_id=company_id)
        except Exception:
            return None
