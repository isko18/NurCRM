import requests
from bs4 import BeautifulSoup

def search_barcode_in_google(barcode):
    query = f"site:barcode-list.ru {barcode}"
    url = f"https://www.google.com/search?q={query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    resp = requests.get(url, headers=headers, timeout=5)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')
    for g in soup.select('div.g'):
        link = g.find('a')
        if link and link['href']:
            print(f"Найдено: {link['href']}")
            return link['href']
    print("❌ Ничего не найдено в Google")
    
search_barcode_in_google("4700003010988")
