import subprocess

def start_baileys(company_id: str):
    """Запуск Baileys-сессии через Node.js"""
    subprocess.Popen(["node", "apps/wa/baileys.js", str(company_id)])
