# 
    Backend NurCRM

1. **Создание виртуального окружения**

   ```python
   python -m venv venv 
   ```
2. **Активация виртуального окружения**

   ```python
   ./venv/Scripts/activate
   ```
   **3.Установка зависимостей**

   ```python
   pip install -r requirements.txt
   ```
   4. **Миграции**
      ```python
      python manage.py makemigrations
      python manage.py migrate
      ```
    5. **Запуск**
       ```python2
       python manage.py runserver
       ```

## Документация

- [Документация по работе с весами (Rongta, CAS, PLU, POS)](docs/SCALES_DOCUMENTATION_RU.md)
- [Настройки разбора весовых штрихкодов](docs/scale_barcode_settings_frontend.md)
- [Экспорт весовых товаров под весы Rongta](docs/scale_export_frontend_api.md)
- [Документация кассы (POS)](apps/main/README_MARKET_POS.md)

