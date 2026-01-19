"""
Общие утилиты для POS системы.
Содержит функции для работы с денежными суммами, количествами и Decimal значениями.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, Union
from django.db import models


# Константы для точности округления
Q2 = Decimal("0.01")  # Для денег (2 знака после запятой)
Q3 = Decimal("0.001")  # Для количества (3 знака после запятой)


def money(x: Optional[Decimal]) -> Decimal:
    """
    Округляет Decimal до 2 знаков после запятой (для денежных сумм).
    
    Args:
        x: Decimal значение или None
        
    Returns:
        Decimal округленное до 2 знаков
    """
    return (x or Decimal("0")).quantize(Q2, rounding=ROUND_HALF_UP)


def qty3(x: Optional[Decimal]) -> Decimal:
    """
    Округляет Decimal до 3 знаков после запятой (для количества товаров).
    Используется для весовых товаров.
    
    Args:
        x: Decimal значение или None
        
    Returns:
        Decimal округленное до 3 знаков
    """
    return (x or Decimal("0")).quantize(Q3, rounding=ROUND_HALF_UP)


def q2(x: Optional[Decimal]) -> Decimal:
    """
    Алиас для money() - округление до 2 знаков.
    Оставлен для обратной совместимости.
    """
    return money(x)


def _q2(x: Optional[Decimal]) -> Decimal:
    """
    Внутренняя функция округления до 2 знаков.
    Используется в некоторых местах для явного округления.
    """
    return (x or Decimal("0")).quantize(Q2, rounding=ROUND_HALF_UP)


def to_decimal(v: Union[str, int, float, Decimal, None], default: Optional[Decimal] = None) -> Optional[Decimal]:
    """
    Безопасное преобразование значения в Decimal.
    
    Args:
        v: Значение для преобразования (может быть строкой, числом, Decimal или None)
        default: Значение по умолчанию, если преобразование невозможно
        
    Returns:
        Decimal или default
    """
    if v in (None, "", "null", "None"):
        return default
    try:
        # Заменяем запятую на точку для корректного парсинга
        return Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return default


def as_decimal(v: Union[str, int, float, Decimal, None], default: Decimal = Decimal("0")) -> Decimal:
    """
    Преобразует значение в Decimal с гарантированным результатом.
    
    Args:
        v: Значение для преобразования
        default: Значение по умолчанию (по умолчанию Decimal("0"))
        
    Returns:
        Decimal значение (никогда не None)
    """
    d = to_decimal(v, default=None)
    if d is None:
        return default
    try:
        return Decimal(d)
    except Exception:
        return default


def fmt_money(x: Optional[Decimal]) -> str:
    """
    Форматирует Decimal как денежную сумму с 2 знаками после запятой.
    
    Args:
        x: Decimal значение
        
    Returns:
        Строка вида "123.45"
    """
    return f"{_q2(x):.2f}"


def fmt(x: Optional[Decimal]) -> str:
    """
    Алиас для fmt_money().
    """
    return fmt_money(x)


def has_field(model: type[models.Model], name: str) -> bool:
    """
    Проверяет, есть ли у модели поле с указанным именем.
    
    Args:
        model: Класс модели Django
        name: Имя поля
        
    Returns:
        True если поле существует, False иначе
    """
    try:
        return any(f.name == name for f in model._meta.get_fields())
    except Exception:
        return False


def get_attr(obj: Optional[object], name: str, default=None):
    """
    Безопасное получение атрибута объекта.
    
    Args:
        obj: Объект или None
        name: Имя атрибута
        default: Значение по умолчанию
        
    Returns:
        Значение атрибута или default
    """
    return getattr(obj, name, default) if obj is not None else default
