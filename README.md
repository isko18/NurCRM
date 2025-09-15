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
   3. **Установка зависимостей**
      ```python
      pip install -r requirements.txt
      ```
   4. **Миграции**
      ```python
      python manage.py makemigrations
      python manage.py migrate
      ```
   5. **Запуск**
      ```python
      python manage.py runserver
      ```
