from apps.users.models import User

# Проверяем есть ли вообще пользователь с таким UUID
User.objects.filter(id="feb5dd33-72fc-4961-98b0-216cad1a8bf3").exists()

# Проверяем принадлежит ли он текущей компании:
User.objects.filter(
    id="feb5dd33-72fc-4961-98b0-216cad1a8bf3",
    company="d9b9b4ff-2f6e-4c77-9b63-535d56441943"
).exists()
