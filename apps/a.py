import re
import csv

# Путь к твоему файлу
input_file = "Новый4.xlsx"  # замените на путь к вашей выгрузке
output_file = "output.csv"

# Чтение бинарного файла
with open(input_file, "rb") as f:
    data = f.read()

# Ищем все последовательности читаемых символов (ASCII + расширенные UTF-8)
rows = re.findall(b'[\x20-\x7E\xC0-\xFF]+', data)

# Сохраняем в CSV
with open(output_file, "w", newline='', encoding="utf-8") as csvfile:
    writer = csv.writer(csvfile)
    for row in rows:
        line = row.decode("utf-8", errors="ignore")
        # Разделяем по табуляции или пробелу для грубой структуры
        writer.writerow(line.split())

print(f"Готово! Данные сохранены в {output_file}. Откройте его в Excel.")