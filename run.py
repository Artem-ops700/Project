from flask import Flask, render_template, send_from_directory, url_for, request, redirect, session, flash, jsonify
import os
import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import json
import requests
from bs4 import BeautifulSoup
import re
import time
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging

app = Flask(__name__,
            static_folder='static',
            static_url_path='/static',
            template_folder='templates')

app.secret_key = 'your-secret-key-here-change-it-2025'
app.permanent_session_lifetime = 3600  # Сессия живет 1 час

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============ ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ БИОГРАФИЙ ============

def fetch_wikipedia_bio(athlete_name):
    """Получает краткую биографию спортсмена из Wikipedia (русский раздел)"""
    try:
        search_name = athlete_name.replace(' ', '_')
        url = f"https://ru.wikipedia.org/api/rest_v1/page/summary/{search_name}"
        headers = {'User-Agent': 'SkiFanApp/1.0 (contact@example.com)'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            extract = data.get('extract', '')
            thumbnail = data.get('thumbnail', {}).get('source', '')
            return {
                'bio': extract[:1000] + ('...' if len(extract) > 1000 else ''),
                'image': thumbnail,
                'wiki_url': data.get('content_urls', {}).get('desktop', {}).get('page', '')
            }
        else:
            return {
                'bio': f'Биография для {athlete_name} временно отсутствует.',
                'image': '',
                'wiki_url': ''
            }
    except Exception as e:
        logger.error(f"Ошибка при запросе к Wikipedia для {athlete_name}: {e}")
        return {
            'bio': f'Не удалось загрузить биографию.',
            'image': '',
            'wiki_url': ''
        }


# ============ ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ДЕТАЛЕЙ СОБЫТИЯ (ЗАГЛУШКА) ============

def fetch_event_details(event_name, event_date, event_place):
    """
    Заглушка для получения дополнительной информации о событии.
    В реальном проекте здесь может быть парсинг с внешних сайтов.
    """
    return {
        'weather': 'Снег, -5°C',
        'participants': ['Александр Большунов', 'Сергей Устюгов', 'Юлия Ступак', 'Наталья Терентьева'],
        'last_winner': 'Александр Большунов' if 'Большунов' in event_name else 'Сергей Устюгов',
        'facts': [
            'Традиционная гонка с раздельным стартом',
            'Дистанция: 15 км',
            'Прошлогодний победитель: ' + ('Александр Большунов' if 'Большунов' in event_name else 'Сергей Устюгов')
        ]
    }


# ============ ФУНКЦИИ ДЛЯ АВТОМАТИЧЕСКОГО СБОРА СОРЕВНОВАНИЙ ============

def get_event_type(name):
    """Определяет тип события по названию"""
    name_lower = name.lower()
    if 'кубок' in name_lower or 'cup' in name_lower:
        return 'cup'
    elif 'чемпионат' in name_lower or 'championship' in name_lower:
        return 'championship'
    elif 'марафон' in name_lower or 'marathon' in name_lower:
        return 'marathon'
    elif 'олимп' in name_lower or 'olymp' in name_lower:
        return 'olympic'
    else:
        return 'other'


def parse_fis_calendar():
    """Парсит календарь Кубка мира с официального сайта FIS (упрощённо)"""
    events = []
    try:
        url = "https://www.fis-ski.com/DB/cross-country/calendar-results.html"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            tables = soup.find_all('table', class_='table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        text = row.get_text().strip()
                        if re.search(r'\d{1,2}\s+[а-я]+', text, re.IGNORECASE):
                            events.append(text[:200])
            if events:
                logger.info(f"Найдено {len(events)} событий на FIS")
        else:
            logger.warning(f"FIS ответил кодом {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка парсинга FIS: {e}")
    return events


def parse_flgr_calendar():
    """Парсит календарь с сайта Федерации лыжных гонок России"""
    events = []
    try:
        url = "https://www.flgr.ru/calendar/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            items = soup.find_all('div', class_=re.compile(r'event|calendar|item'))
            for item in items:
                text = item.get_text().strip()
                if len(text) > 20 and re.search(r'\d{1,2}\s+[а-я]+', text, re.IGNORECASE):
                    events.append(text[:200])
            logger.info(f"Найдено {len(events)} событий на FLGR")
        else:
            logger.warning(f"FLGR ответил кодом {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка парсинга FLGR: {e}")
    return events


def generate_fallback_events():
    """Генерирует тестовые события на ближайшие даты (если парсинг не сработал)"""
    events = []
    today = datetime.now()
    base_date = today.replace(day=1)  # первое число текущего месяца
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    for i in range(1, 7):
        event_date = base_date + timedelta(days=i * 7)
        month_str = months[event_date.month - 1]
        events.append({
            'name': f'Кубок мира – этап {i}',
            'date': f'{event_date.day} {month_str} {event_date.year}',
            'place': 'Лахти, Финляндия' if i % 2 else 'Тюмень, Россия',
            'multiplier': 1.5
        })
    rus_date = base_date + timedelta(days=45)
    month_str = months[rus_date.month - 1]
    events.append({
        'name': 'Чемпионат России',
        'date': f'{rus_date.day} {month_str} {rus_date.year}',
        'place': 'Малиновка, Россия',
        'multiplier': 1.8
    })
    return events


def parse_and_update_games():
    """
    Основная функция: пытается получить события из разных источников,
    преобразует в единый формат и обновляет таблицу games.
    """
    all_events = []

    # Пробуем парсить FIS
    fis_raw = parse_fis_calendar()
    if fis_raw:
        for text in fis_raw:
            parts = text.split(',')
            name = parts[0].strip() if parts else "Соревнование"
            place = parts[1].strip() if len(parts) > 1 else "Не указано"
            date_match = re.search(r'(\d{1,2})\s+([а-я]+)\s+(\d{4})', text, re.IGNORECASE)
            if date_match:
                date = f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}"
            else:
                date = datetime.now().strftime('%d %B %Y')
            all_events.append({
                'name': name,
                'date': date,
                'place': place,
                'multiplier': 1.5
            })

    # Пробуем парсить FLGR
    flgr_raw = parse_flgr_calendar()
    if flgr_raw:
        for text in flgr_raw:
            parts = text.split(',')
            name = parts[0].strip() if parts else "Соревнование"
            place = parts[1].strip() if len(parts) > 1 else "Россия"
            date_match = re.search(r'(\d{1,2})\s+([а-я]+)\s+(\d{4})', text, re.IGNORECASE)
            if date_match:
                date = f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}"
            else:
                date = datetime.now().strftime('%d %B %Y')
            all_events.append({
                'name': name,
                'date': date,
                'place': place,
                'multiplier': 1.3
            })

    # Если ничего не нашли, используем запасной вариант
    if not all_events:
        logger.warning("Не удалось получить данные из источников, использую тестовые события")
        all_events = generate_fallback_events()

    # Обновляем базу данных
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Удаляем старые завершённые события (более 30 дней)
    c.execute('DELETE FROM games WHERE status = "finished" AND date(created_at) < date("now", "-30 days")')

    # Для каждого нового события проверяем, есть ли уже такое (по имени и дате)
    for ev in all_events[:20]:  # ограничим количество
        # Определяем тип
        ev_type = get_event_type(ev['name'])

        c.execute('SELECT id FROM games WHERE event_name = ? AND event_date = ?',
                  (ev['name'], ev['date']))
        if not c.fetchone():
            event_id = f"auto_{int(time.time())}_{hash(ev['name']) % 10000}"
            c.execute('''
                INSERT INTO games (event_id, event_name, event_date, event_place, event_type, points_multiplier, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (event_id, ev['name'], ev['date'], ev['place'], ev_type, ev['multiplier'], 'upcoming'))
            logger.info(f"Добавлено новое событие: {ev['name']} {ev['date']} тип={ev_type}")

    conn.commit()
    conn.close()
    logger.info("Обновление календаря завершено")


def update_events_status():
    """Обновляет статусы соревнований на основе текущей даты"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    current_date = datetime.now()

    c.execute('SELECT id, event_date, status FROM games')
    games = c.fetchall()

    months_map = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    }

    for game_id, date_str, current_status in games:
        try:
            parts = date_str.split()
            if len(parts) >= 3:
                day = int(parts[0])
                month_name = parts[1].lower()
                year = int(parts[2])
                month = months_map.get(month_name, 1)
                event_date = datetime(year, month, day)

                if event_date > current_date:
                    new_status = 'upcoming'
                elif event_date <= current_date and (current_date - event_date).days <= 2:
                    new_status = 'active'
                else:
                    new_status = 'finished'

                if new_status != current_status:
                    c.execute('UPDATE games SET status = ? WHERE id = ?', (new_status, game_id))
        except Exception as e:
            logger.error(f"Ошибка обработки даты {date_str}: {e}")

    conn.commit()
    conn.close()
    logger.info("Статусы событий обновлены")


# ============ ФУНКЦИИ ДЛЯ АВТОМАТИЧЕСКОГО СБОРА ДАННЫХ (СТАРЫЕ) ============

def fetch_wikipedia_team():
    try:
        url = "https://ru.wikipedia.org/wiki/Сборная_России_по_лыжным_гонкам"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            tables = soup.find_all('table', {'class': 'wikitable'})
            team_data = {
                'men': {},
                'women': {},
                'excluded': [],
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            for table in tables:
                table_text = table.get_text().lower()
                if 'мужчины' in table_text or 'men' in table_text:
                    rows = table.find_all('tr')[1:]
                    current_group = "Основной состав"
                    for row in rows:
                        cols = row.find_all('td')
                        if len(cols) >= 2:
                            name = cols[0].get_text().strip()
                            if name and len(name) > 3:
                                if current_group not in team_data['men']:
                                    team_data['men'][current_group] = []
                                team_data['men'][current_group].append(name)
                elif 'женщины' in table_text or 'women' in table_text:
                    rows = table.find_all('tr')[1:]
                    current_group = "Основной состав"
                    for row in rows:
                        cols = row.find_all('td')
                        if len(cols) >= 2:
                            name = cols[0].get_text().strip()
                            if name and len(name) > 3:
                                if current_group not in team_data['women']:
                                    team_data['women'][current_group] = []
                                team_data['women'][current_group].append(name)
            json_path = os.path.join('static', 'data', 'team.json')
            os.makedirs(os.path.join('static', 'data'), exist_ok=True)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(team_data, f, ensure_ascii=False, indent=2)
            logger.info("✅ Данные состава обновлены с Wikipedia")
            return team_data
    except Exception as e:
        logger.error(f"Ошибка при парсинге Wikipedia: {e}")
        return None


def fetch_athlete_biography(athlete_name):
    try:
        search_name = athlete_name.replace(' ', '_')
        url = f"https://ru.wikipedia.org/wiki/{search_name}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            infobox = soup.find('table', {'class': 'infobox'})
            bio_data = {
                'name': athlete_name,
                'birth_date': None,
                'birth_place': None,
                'club': None,
                'coach': None,
                'achievements': []
            }
            if infobox:
                rows = infobox.find_all('tr')
                for row in rows:
                    text = row.get_text().strip()
                    if 'родился' in text.lower() or 'род.' in text.lower():
                        bio_data['birth_date'] = text
                    elif 'клуб' in text.lower():
                        bio_data['club'] = text
                    elif 'тренер' in text.lower():
                        bio_data['coach'] = text
            content = soup.find('div', {'class': 'mw-parser-output'})
            if content:
                paragraphs = content.find_all('p')
                for p in paragraphs[:3]:
                    bio_data['achievements'].append(p.get_text()[:200])
            bio_path = os.path.join('static', 'data', 'biographies.json')
            os.makedirs(os.path.join('static', 'data'), exist_ok=True)
            if os.path.exists(bio_path):
                with open(bio_path, 'r', encoding='utf-8') as f:
                    bios = json.load(f)
            else:
                bios = {}
            bios[athlete_name] = bio_data
            with open(bio_path, 'w', encoding='utf-8') as f:
                json.dump(bios, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ Биография {athlete_name} обновлена")
            return bio_data
    except Exception as e:
        logger.error(f"Ошибка при получении биографии {athlete_name}: {e}")
        return None


def fetch_all_biographies():
    try:
        json_path = os.path.join('static', 'data', 'team.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                team_data = json.load(f)
            all_athletes = []
            for group, athletes in team_data.get('men', {}).items():
                all_athletes.extend(athletes)
            for group, athletes in team_data.get('women', {}).items():
                all_athletes.extend(athletes)
            for athlete in all_athletes[:5]:
                fetch_athlete_biography(athlete)
            logger.info(f"✅ Биографии обновлены для {len(all_athletes)} спортсменов")
    except Exception as e:
        logger.error(f"Ошибка при обновлении биографий: {e}")


def fetch_sports_news():
    try:
        news_sources = [
            "https://www.sport-express.ru/skiing/",
            "https://www.championat.com/skiing/",
            "https://rsport.ria.ru/skiing/"
        ]
        all_news = []
        for source in news_sources:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(source, headers=headers, timeout=10)
                response.encoding = 'utf-8'
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    titles = soup.find_all(['h2', 'h3', 'a'], class_=re.compile('title|news|item'))
                    for title in titles[:5]:
                        text = title.get_text().strip()
                        if text and len(text) > 20 and ('лыжн' in text.lower() or 'гонк' in text.lower() or 'большунов' in text.lower()):
                            all_news.append({
                                'title': text,
                                'source': source,
                                'date': datetime.now().strftime('%d.%m.%Y')
                            })
            except Exception as e:
                logger.error(f"Ошибка при парсинге {source}: {e}")
                continue
        news_path = os.path.join('static', 'data', 'news.json')
        os.makedirs(os.path.join('static', 'data'), exist_ok=True)
        with open(news_path, 'w', encoding='utf-8') as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Новости обновлены, добавлено {len(all_news)} новостей")
        return all_news
    except Exception as e:
        logger.error(f"Ошибка при получении новостей: {e}")
        return []


def auto_update_all():
    logger.info("🔄 Запуск автоматического обновления данных...")
    parse_and_update_games()        # обновляем соревнования
    fetch_wikipedia_team()           # обновляем состав (опционально)
    fetch_all_biographies()          # биографии
    fetch_sports_news()              # новости
    update_events_status()            # статусы
    logger.info("✅ Автоматическое обновление завершено")


# ============ ИНИЦИАЛИЗАЦИЯ ПЛАНИРОВЩИКА ============

def init_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=auto_update_all, trigger="interval", hours=6)
    scheduler.add_job(func=update_events_status, trigger="interval", hours=1)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    logger.info("✅ Планировщик автоматического обновления запущен")


# ============ РАСШИРЕННЫЕ ФУНКЦИИ ДЛЯ БАЗЫ ДАННЫХ ============

def init_db():
    """Создает все таблицы в базе данных с расширенными полями"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # ===== ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            birth_date TEXT,
            notifications BOOLEAN DEFAULT 1,
            email_notifications BOOLEAN DEFAULT 1,
            points INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            total_predictions INTEGER DEFAULT 0,
            perfect_podiums INTEGER DEFAULT 0,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ===== ТАБЛИЦА ИГР (соревнований) =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_place TEXT NOT NULL,
            winner TEXT,
            second TEXT,
            third TEXT,
            status TEXT DEFAULT 'upcoming',
            event_type TEXT DEFAULT 'other',
            points_multiplier REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    ''')

    # ===== ТАБЛИЦА КОММЕНТАРИЕВ К СОРЕВНОВАНИЯМ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS event_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # ===== ТАБЛИЦА ПРОГНОЗОВ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL,
            winner TEXT NOT NULL,
            second TEXT,
            third TEXT,
            points INTEGER DEFAULT 0,
            is_correct BOOLEAN DEFAULT 0,
            perfect_podium BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (game_id) REFERENCES games (id),
            UNIQUE(user_id, game_id)
        )
    ''')

    # ===== ТАБЛИЦА ДОСТИЖЕНИЙ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            achievement_key TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            icon TEXT NOT NULL,
            points_awarded INTEGER DEFAULT 0,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE(user_id, achievement_key)
        )
    ''')

    # ===== ТАБЛИЦА ИСТОРИИ БАЛЛОВ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS points_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            points_change INTEGER NOT NULL,
            total_points INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # ===== ТАБЛИЦА НАСТРОЕК ПОЛЬЗОВАТЕЛЯ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            show_in_rating BOOLEAN DEFAULT 1,
            receive_notifications BOOLEAN DEFAULT 1,
            theme TEXT DEFAULT 'light',
            language TEXT DEFAULT 'ru',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # ===== ТАБЛИЦА НОВОСТЕЙ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            image_url TEXT,
            date TEXT NOT NULL,
            category TEXT,
            views INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ===== ТАБЛИЦА БИОГРАФИЙ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS biographies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_name TEXT UNIQUE NOT NULL,
            birth_date TEXT,
            birth_place TEXT,
            club TEXT,
            coach TEXT,
            achievements TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ===== ТАБЛИЦА СПОРТСМЕНОВ =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS athletes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            team TEXT NOT NULL,
            coach_group TEXT NOT NULL,
            bio TEXT,
            image_url TEXT,
            wiki_url TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")


def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # ... существующие таблицы ...

    # Добавляем поле region в athletes (если его нет)
    c.execute("PRAGMA table_info(athletes)")
    columns = [col[1] for col in c.fetchall()]
    if 'region' not in columns:
        c.execute("ALTER TABLE athletes ADD COLUMN region TEXT")

    # Таблица достижений
    c.execute('''
        CREATE TABLE IF NOT EXISTS athlete_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            athlete_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            date TEXT,
            description TEXT,
            FOREIGN KEY (athlete_id) REFERENCES athletes (id)
        )
    ''')

    # Добавим тестовые регионы и достижения для существующих спортсменов
    c.execute("SELECT id, name FROM athletes")
    athletes = c.fetchall()
    regions = ['Тюменская обл.', 'Архангельская обл.', 'Москва', 'Респ. Коми', 'Красноярский край']
    for i, (aid, name) in enumerate(athletes):
        # присвоим регион по индексу
        region = regions[i % len(regions)]
        c.execute("UPDATE athletes SET region = ? WHERE id = ?", (region, aid))
        # добавим достижения для некоторых
        if i % 3 == 0:
            c.execute('''
                INSERT INTO athlete_achievements (athlete_id, title, date, description)
                VALUES (?, ?, ?, ?)
            ''', (aid, 'Победитель этапа Кубка мира', '2025-01-15', 'Спринт, классика'))
            c.execute('''
                INSERT INTO athlete_achievements (athlete_id, title, date, description)
                VALUES (?, ?, ?, ?)
            ''', (aid, 'Серебро Чемпионата России', '2024-12-10', '15 км, свободный стиль'))

    conn.commit()
    conn.close()


def init_games():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM games')
    count = c.fetchone()[0]
    if count == 0:
        # Добавляем тестовые соревнования на случай отсутствия парсинга
        fallback = generate_fallback_events()
        for ev in fallback:
            ev_type = get_event_type(ev['name'])
            event_id = f"fallback_{int(time.time())}_{hash(ev['name']) % 10000}"
            c.execute('''
                INSERT INTO games (event_id, event_name, event_date, event_place, event_type, points_multiplier, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (event_id, ev['name'], ev['date'], ev['place'], ev_type, ev['multiplier'], 'upcoming'))
        conn.commit()
    conn.close()


def init_achievements():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS achievements_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            achievement_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            icon TEXT NOT NULL,
            points_bonus INTEGER DEFAULT 0
        )
    ''')
    c.execute('DELETE FROM achievements_list')
    achievements_data = [
        ('first_prediction', 'Новичок', 'Сделать первый прогноз', '🌱', 10),
        ('five_predictions', 'Активный', 'Сделать 5 прогнозов', '📊', 20),
        ('ten_predictions', 'Эксперт', 'Сделать 10 прогнозов', '🎯', 30),
        ('first_correct', 'Меткий', 'Первый правильный прогноз', '✅', 15),
        ('five_correct', 'Снайпер', '5 правильных прогнозов', '🎯', 25),
        ('ten_correct', 'Мастер', '10 правильных прогнозов', '🏆', 50),
        ('perfect_podium', 'Ясновидящий', 'Угадать точный подиум', '🔮', 100),
        ('three_perfect', 'Пророк', '3 точных подиума', '⚡', 200),
        ('hundred_points', 'Легенда', 'Набрать 100 баллов', '👑', 0),
        ('five_hundred_points', 'Миф', 'Набрать 500 баллов', '🌟', 0),
        ('thousand_points', 'Бог', 'Набрать 1000 баллов', '✨', 0),
    ]
    for ach in achievements_data:
        c.execute('''
            INSERT OR REPLACE INTO achievements_list (achievement_key, name, description, icon, points_bonus)
            VALUES (?, ?, ?, ?, ?)
        ''', ach)
    conn.commit()
    conn.close()


def init_news():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM news')
    count = c.fetchone()[0]
    if count == 0:
        current_date = datetime.now()
        news_data = [
            ('preparation', 'Подготовка к сезону',
             'Сборная завершила учебно‑тренировочные сборы в горах и перешла к ледовым тренировкам.',
             '/static/images/news/season-prep.jpg', f'{current_date.day} марта 2026', 'Тренировки'),
            ('juniors', 'Юниоры дебютируют',
             'В расширенный список вошли молодые гонщики — им дадут шанс на этапах Кубка Европы.',
             '/static/images/news/juniors.jpg', f'{current_date.day - 2} марта 2026', 'Молодежь'),
            ('olympics', 'Цель — Олимпиада',
             'Тренерский штаб обозначил приоритет: подготовка к следующей зимней Олимпиаде.',
             '/static/images/news/olympics.jpg', f'{current_date.day - 5} марта 2026', 'Олимпиада'),
        ]
        for news in news_data:
            c.execute('''
                INSERT OR IGNORE INTO news (news_key, title, content, image_url, date, category)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', news)
        conn.commit()
    conn.close()


def init_athletes():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM athletes')
    count = c.fetchone()[0]
    if count > 0:
        conn.close()
        return

    athletes_data = [
        # Мужчины - Группа Бородавко / Жмурко
        ('Александр Большунов', 'men', 'Бородавко / Жмурко', True),
        ('Артём Мальцев', 'men', 'Бородавко / Жмурко', True),
        ('Егор Митрошин', 'men', 'Бородавко / Жмурко', True),
        ('Денис Спицов', 'men', 'Бородавко / Жмурко', True),
        ('Алексей Червоткин', 'men', 'Бородавко / Жмурко', True),
        # Мужчины - Группа Сорина / Черноусова
        ('Сергей Ардашев', 'men', 'Сорина / Черноусова', True),
        ('Александр Бакуров', 'men', 'Сорина / Черноусова', True),
        ('Сергей Волков', 'men', 'Сорина / Черноусова', True),
        ('Никита Денисов', 'men', 'Сорина / Черноусова', True),
        ('Савелий Коростелёв', 'men', 'Сорина / Черноусова', True),
        ('Александр Терентьев', 'men', 'Сорина / Черноусова', True),
        ('Константин Тиунов', 'men', 'Сорина / Черноусова', True),
        ('Иван Якимушкин', 'men', 'Сорина / Черноусова', True),
        ('Сергей Устюгов', 'men', 'Сорина / Черноусова', True),
        # Мужчины - Группа Перевозчикова / Акимова
        ('Иван Горбунов', 'men', 'Перевозчикова / Акимова', True),
        ('Дмитрий Жуль', 'men', 'Перевозчикова / Акимова', True),
        ('Сергей Забалуев', 'men', 'Перевозчикова / Акимова', True),
        ('Павел Козадаев', 'men', 'Перевозчикова / Акимова', True),
        ('Илья Семиков', 'men', 'Перевозчикова / Акимова', True),
        # Мужчины - Группа Седова
        ('Александр Ившин', 'men', 'Седова', True),
        ('Платон Исламшин', 'men', 'Седова', True),
        ('Кирилл Кочегаров', 'men', 'Седова', True),
        ('Вячеслав Мамичев', 'men', 'Седова', True),
        ('Владислав Осипов', 'men', 'Седова', True),
        ('Дмитрий Пузанов', 'men', 'Седова', True),
        ('Никита Радионов', 'men', 'Седова', True),
        ('Павел Соловьёв', 'men', 'Седова', True),
        ('Илья Чертков', 'men', 'Седова', True),

        # Женщины - Группа Бородавко / Жмурко
        ('Алёна Баранова', 'women', 'Бородавко / Жмурко', True),
        ('Мария Истомина', 'women', 'Бородавко / Жмурко', True),
        ('Елизавета Пантрина', 'women', 'Бородавко / Жмурко', True),
        ('Анастасия Фалеева', 'women', 'Бородавко / Жмурко', True),
        # Женщины - Группа Сорина / Черноусова
        ('Дарья Непряева', 'women', 'Сорина / Черноусова', True),
        ('Татьяна Сорина', 'women', 'Сорина / Черноусова', True),
        ('Вероника Степанова', 'women', 'Сорина / Черноусова', True),
        ('Юлия Ступак', 'women', 'Сорина / Черноусова', True),
        ('Наталья Терентьева', 'women', 'Сорина / Черноусова', True),
        # Женщины - Группа Перевозчикова / Акимова
        ('Лидия Горбунова', 'women', 'Перевозчикова / Акимова', True),
        ('Дарья Канева', 'women', 'Перевозчикова / Акимова', True),
        ('Евгения Крупицкая', 'women', 'Перевозчикова / Акимова', True),
        ('Екатерина Смирнова', 'women', 'Перевозчикова / Акимова', True),
        ('Мистер Писькин', 'women', 'Перевозчикова / Акимова', False),
        # Женщины - Группа Седова
        ('Анастасия Кулешова', 'women', 'Седова', True),
        ('Екатерина Никитина', 'women', 'Седова', True),
        # Женщины - Группа Нутрихина
        ('Руслана Дьякова', 'women', 'Нутрихина', True),
        ('Арина Каличева', 'women', 'Нутрихина', True),
        ('Елизавета Маслакова', 'women', 'Нутрихина', True),
        ('Алина Пеклецова', 'women', 'Нутрихина', True),
        ('Арина Рощина', 'women', 'Нутрихина', True),
        ('Екатерина Шибекина', 'women', 'Нутрихина', True),
        ('Ксения Шорохова', 'women', 'Нутрихина', True),
    ]

    # Сначала добавляем всех активных и неактивных из основного списка
    for name, team, coach, active in athletes_data:
        bio_info = fetch_wikipedia_bio(name)
        c.execute('''
            INSERT INTO athletes (name, team, coach_group, bio, image_url, wiki_url, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, team, coach, bio_info['bio'], bio_info['image'], bio_info['wiki_url'], 1 if active else 0))
        time.sleep(0.3)

    # Отдельно добавляем исключённых, которых нет в основном списке (для полноты)
    excluded = [
        ('Ермил Вокуев', 'men', 'Исключён'),
        ('Илья Порошкин', 'men', 'Исключён'),
        ('Антон Тимашов', 'men', 'Исключён'),
        ('Анастасия Прокофьева', 'women', 'Исключён'),
        ('Олеся Ляшенко', 'women', 'Исключён'),
        ('Карина Гончарова', 'women', 'Исключён'),
        ('Екатерина Евтягина', 'women', 'Исключён'),
        ('Егор Гусак', 'men', 'Исключён'),
        ('Даниил Муралеев', 'men', 'Исключён'),
        ('Данил Нечипоренко', 'men', 'Исключён'),
        ('Илья Трегубов', 'men', 'Исключён'),
    ]
    for name, team, coach in excluded:
        # Проверим, нет ли уже такого в базе (на случай дубликата)
        c.execute('SELECT id FROM athletes WHERE name = ?', (name,))
        if not c.fetchone():
            bio_info = fetch_wikipedia_bio(name)
            c.execute('''
                INSERT INTO athletes (name, team, coach_group, bio, image_url, wiki_url, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (name, team, coach, bio_info['bio'], bio_info['image'], bio_info['wiki_url']))
            time.sleep(0.3)

    conn.commit()
    conn.close()
    print("✅ Таблица спортсменов заполнена.")


# ============ НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С БАЛЛАМИ ============

def add_points(user_id, points, source, source_id=None):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT points FROM users WHERE id = ?', (user_id,))
    current_points = c.fetchone()[0]
    new_total = current_points + points
    c.execute('UPDATE users SET points = ? WHERE id = ?', (new_total, user_id))
    c.execute('''
        INSERT INTO points_history (user_id, points_change, total_points, source, source_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, points, new_total, source, source_id))
    conn.commit()
    conn.close()
    check_all_achievements(user_id)
    return new_total


def check_all_achievements(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT points, correct_predictions, total_predictions FROM users WHERE id = ?', (user_id,))
    user_stats = c.fetchone()
    if not user_stats:
        conn.close()
        return
    points, correct, total = user_stats
    achievements_to_check = []
    if total >= 1:
        achievements_to_check.append(('first_prediction', user_id))
    if total >= 5:
        achievements_to_check.append(('five_predictions', user_id))
    if total >= 10:
        achievements_to_check.append(('ten_predictions', user_id))
    if correct >= 1:
        achievements_to_check.append(('first_correct', user_id))
    if correct >= 5:
        achievements_to_check.append(('five_correct', user_id))
    if correct >= 10:
        achievements_to_check.append(('ten_correct', user_id))
    if points >= 100:
        achievements_to_check.append(('hundred_points', user_id))
    for achievement_key, uid in achievements_to_check:
        c.execute('SELECT name, description, icon, points_bonus FROM achievements_list WHERE achievement_key = ?',
                  (achievement_key,))
        ach_data = c.fetchone()
        if ach_data:
            name, desc, icon, bonus = ach_data
            c.execute('SELECT id FROM achievements WHERE user_id = ? AND achievement_key = ?', (uid, achievement_key))
            if not c.fetchone():
                c.execute('''
                    INSERT INTO achievements (user_id, achievement_key, name, description, icon, points_awarded)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (uid, achievement_key, name, desc, icon, bonus))
                if bonus > 0:
                    add_points(uid, bonus, 'achievement', c.lastrowid)
    conn.commit()
    conn.close()


def get_top_users(limit=10):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        SELECT username, points, correct_predictions, total_predictions
        FROM users 
        WHERE points > 0 OR total_predictions > 0
        ORDER BY points DESC 
        LIMIT ?
    ''', (limit,))
    users = c.fetchall()
    conn.close()
    result = []
    for user in users:
        if user[1] >= 1000:
            rank = 'Легенда'
        elif user[1] >= 500:
            rank = 'Профессионал'
        elif user[1] >= 200:
            rank = 'Опытный'
        elif user[1] >= 50:
            rank = 'Любитель'
        else:
            rank = 'Новичок'
        accuracy = 0
        if user[3] > 0:
            accuracy = round((user[2] / user[3]) * 100, 1)
        result.append({
            'username': user[0],
            'points': user[1],
            'correct': user[2],
            'total': user[3],
            'rank': rank,
            'accuracy': accuracy
        })
    return result


# ============ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ============
init_db()
init_games()
init_achievements()
init_news()
init_athletes()


# ============ ОСНОВНЫЕ МАРШРУТЫ ============

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/team.html')
def team():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT id, name, team, coach_group, is_active FROM athletes ORDER BY team, coach_group, name')
    athletes_raw = c.fetchall()
    conn.close()
    athletes = []
    for a in athletes_raw:
        athletes.append({
            'id': a[0],
            'name': a[1],
            'team': a[2],
            'coach_group': a[3],
            'is_active': a[4]
        })
    return render_template('team.html', athletes=athletes)


@app.route('/athlete/<int:athlete_id>')
def athlete_detail(athlete_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT id, name, team, coach_group, bio, image_url, wiki_url, is_active FROM athletes WHERE id = ?', (athlete_id,))
    athlete = c.fetchone()
    conn.close()
    if not athlete:
        flash('Спортсмен не найден', 'danger')
        return redirect(url_for('team'))
    return render_template('athlete_detail.html', athlete=athlete)


@app.route('/events.html')
def events():
    update_events_status()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        SELECT id, event_id, event_name, event_date, event_place,
               winner, second, third, status, event_type, points_multiplier
        FROM games
        ORDER BY event_date
    ''')
    rows = c.fetchall()
    games = []
    for r in rows:
        games.append({
            'id': r[0],
            'event_id': r[1],
            'name': r[2],
            'date': r[3],
            'place': r[4],
            'winner': r[5],
            'second': r[6],
            'third': r[7],
            'status': r[8],
            'type': r[9] or 'other',
            'multiplier': r[10]
        })
    conn.close()
    now = datetime.now()
    return render_template('events.html', games=games, now=now)


@app.route('/event/<event_id>/comments')
def event_comments(event_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM games WHERE event_id = ?', (event_id,))
    event = c.fetchone()
    if not event:
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('events'))

    # Получаем детали события (из интернета или заглушку)
    details = fetch_event_details(event[2], event[3], event[4])

    c.execute('''
        SELECT event_comments.*, users.username 
        FROM event_comments 
        JOIN users ON event_comments.user_id = users.id 
        WHERE event_id = ? 
        ORDER BY created_at DESC
    ''', (event_id,))
    comments = c.fetchall()
    conn.close()
    return render_template('event_comments.html', event=event, comments=comments, details=details)


@app.route('/event/<event_id>/comment/add', methods=['POST'])
def add_event_comment(event_id):
    if 'user_id' not in session:
        flash('Войдите, чтобы оставить комментарий', 'danger')
        return redirect(url_for('login'))
    comment = request.form['comment']
    if not comment or len(comment.strip()) == 0:
        flash('Комментарий не может быть пустым', 'danger')
        return redirect(url_for('event_comments', event_id=event_id))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO event_comments (user_id, event_id, comment)
        VALUES (?, ?, ?)
    ''', (session['user_id'], event_id, comment))
    conn.commit()
    conn.close()
    flash('Комментарий добавлен!', 'success')
    return redirect(url_for('event_comments', event_id=event_id))


@app.route('/games')
def games_list():
    update_events_status()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM games ORDER BY event_date')
    games = c.fetchall()
    user_predictions = {}
    if 'user_id' in session:
        c.execute('SELECT game_id, winner FROM predictions WHERE user_id = ?',
                  (session['user_id'],))
        for pred in c.fetchall():
            user_predictions[pred[0]] = pred[1]
    conn.close()
    return render_template('games_list.html', games=games, user_predictions=user_predictions)


@app.route('/game/<int:game_id>')
def game_detail(game_id):
    if 'user_id' not in session:
        flash('Войдите, чтобы делать прогнозы', 'warning')
        return redirect(url_for('login'))

    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM games WHERE id = ?', (game_id,))
    game = c.fetchone()
    if not game:
        flash('Соревнование не найдено', 'danger')
        return redirect(url_for('games_list'))

    # Проверяем, делал ли пользователь уже прогноз
    c.execute('SELECT * FROM predictions WHERE user_id = ? AND game_id = ?',
              (session['user_id'], game_id))
    user_prediction = c.fetchone()

    # Список спортсменов для выпадающего списка
    c.execute('SELECT name FROM athletes WHERE is_active = 1 ORDER BY name')
    athletes = [row[0] for row in c.fetchall()]

    conn.close()
    return render_template('game_detail.html', game=game, user_prediction=user_prediction, athletes=athletes)


@app.route('/game/<int:game_id>/predict', methods=['POST'])
def make_prediction(game_id):
    if 'user_id' not in session:
        flash('Необходимо войти в аккаунт', 'danger')
        return redirect(url_for('login'))

    winner = request.form['winner']
    second = request.form.get('second', '')
    third = request.form.get('third', '')

    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Проверяем, существует ли уже прогноз
    c.execute('SELECT id FROM predictions WHERE user_id = ? AND game_id = ?',
              (session['user_id'], game_id))
    existing = c.fetchone()

    if existing:
        # Обновляем существующий прогноз
        c.execute('''
            UPDATE predictions 
            SET winner = ?, second = ?, third = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND game_id = ?
        ''', (winner, second, third, session['user_id'], game_id))
        flash('Прогноз обновлен!', 'success')
    else:
        # Создаём новый прогноз
        c.execute('''
            INSERT INTO predictions (user_id, game_id, winner, second, third)
            VALUES (?, ?, ?, ?, ?)
        ''', (session['user_id'], game_id, winner, second, third))
        # Увеличиваем счётчик прогнозов пользователя
        c.execute('''
            UPDATE users 
            SET total_predictions = total_predictions + 1 
            WHERE id = ?
        ''', (session['user_id'],))
        conn.commit()
        check_all_achievements(session['user_id'])
        flash('Прогноз сохранен!', 'success')

    conn.commit()
    conn.close()
    return redirect(url_for('game_detail', game_id=game_id))


@app.route('/game/<int:game_id>/results', methods=['POST'])
def set_results(game_id):
    if 'user_id' not in session or session['user_id'] != 1:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('games_list'))
    winner = request.form['winner']
    second = request.form['second']
    third = request.form['third']
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT points_multiplier FROM games WHERE id = ?', (game_id,))
    multiplier = c.fetchone()[0] or 1.0
    c.execute('''
        UPDATE games 
        SET winner = ?, second = ?, third = ?, status = 'finished', finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (winner, second, third, game_id))
    c.execute('SELECT id, user_id, winner, second, third FROM predictions WHERE game_id = ?',
              (game_id,))
    predictions = c.fetchall()
    for pred in predictions:
        pred_id, user_id, pred_winner, pred_second, pred_third = pred
        points = 0
        perfect = False
        if pred_winner == winner:
            points += int(10 * multiplier)
        if pred_second == second:
            points += int(5 * multiplier)
        if pred_third == third:
            points += int(3 * multiplier)
        if (pred_winner == winner and pred_second == second and pred_third == third):
            points += int(20 * multiplier)
            perfect = True
        if points > 0:
            c.execute('''
                UPDATE predictions 
                SET points = ?, is_correct = 1, perfect_podium = ?
                WHERE id = ?
            ''', (points, perfect, pred_id))
            if perfect:
                c.execute('''
                    UPDATE users 
                    SET points = points + ?,
                        correct_predictions = correct_predictions + 1,
                        perfect_podiums = perfect_podiums + 1
                    WHERE id = ?
                ''', (points, user_id))
            else:
                c.execute('''
                    UPDATE users 
                    SET points = points + ?,
                        correct_predictions = correct_predictions + 1
                    WHERE id = ?
                ''', (points, user_id))
            add_points(user_id, points, 'prediction', pred_id)
            check_all_achievements(user_id)
    conn.commit()
    conn.close()
    flash('Результаты сохранены, баллы начислены!', 'success')
    return redirect(url_for('games_list'))


@app.route('/achievements')
def user_achievements():
    if 'user_id' not in session:
        flash('Войдите, чтобы видеть достижения', 'warning')
        return redirect(url_for('login'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM achievements WHERE user_id = ? ORDER BY earned_at DESC',
              (session['user_id'],))
    achievements = c.fetchall()
    c.execute('SELECT * FROM achievements_list ORDER BY points_bonus DESC')
    all_achievements = c.fetchall()
    conn.close()
    return render_template('achievements.html', achievements=achievements, all_achievements=all_achievements)


@app.route('/rating')
def rating():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        SELECT username, points, correct_predictions, total_predictions,
               CASE 
                   WHEN points >= 1000 THEN 'Легенда'
                   WHEN points >= 500 THEN 'Профессионал'
                   WHEN points >= 200 THEN 'Опытный'
                   WHEN points >= 50 THEN 'Любитель'
                   ELSE 'Новичок'
               END as rank
        FROM users 
        WHERE points > 0
        ORDER BY points DESC 
        LIMIT 100
    ''')
    top_users = c.fetchall()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT SUM(points) FROM users')
    total_points = c.fetchone()[0] or 0
    c.execute('SELECT AVG(points) FROM users WHERE points > 0')
    avg_points = c.fetchone()[0] or 0
    conn.close()

    user_rank = None
    user_stats = None
    if 'user_id' in session:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*) + 1 
            FROM users 
            WHERE points > (SELECT points FROM users WHERE id = ?)
        ''', (session['user_id'],))
        user_rank = c.fetchone()[0]
        c.execute('SELECT points, total_predictions FROM users WHERE id = ?', (session['user_id'],))
        stats = c.fetchone()
        if stats:
            user_stats = {'points': stats[0], 'predictions': stats[1]}
        conn.close()
    return render_template('rating.html', top_users=top_users, total_users=total_users,
                          total_points=total_points, avg_points=round(avg_points, 1),
                          user_rank=user_rank, user_stats=user_stats)


@app.route('/api/top-users')
def api_top_users():
    top_users = get_top_users(10)
    current_user = None
    if 'user_id' in session:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT points, total_predictions FROM users WHERE id = ?', (session['user_id'],))
        user_data = c.fetchone()
        conn.close()
        if user_data:
            if user_data[0] >= 1000:
                rank = 'Легенда'
            elif user_data[0] >= 500:
                rank = 'Профессионал'
            elif user_data[0] >= 200:
                rank = 'Опытный'
            elif user_data[0] >= 50:
                rank = 'Любитель'
            else:
                rank = 'Новичок'
            current_user = {'points': user_data[0], 'predictions': user_data[1], 'rank': rank}
    return jsonify({'top_users': top_users, 'current_user': current_user})


@app.route('/api/user-stats/<int:user_id>')
def api_user_stats(user_id):
    if user_id != session.get('user_id') and session.get('user_id') != 1:
        return jsonify({'error': 'Доступ запрещен'}), 403
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        SELECT points, correct_predictions, total_predictions, perfect_podiums, created_at
        FROM users WHERE id = ?
    ''', (user_id,))
    stats = c.fetchone()
    c.execute('''
        SELECT points_change, total_points, source, created_at
        FROM points_history 
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 20
    ''', (user_id,))
    history = c.fetchall()
    c.execute('''
        SELECT name, icon, earned_at
        FROM achievements 
        WHERE user_id = ?
        ORDER BY earned_at DESC
    ''', (user_id,))
    achievements = c.fetchall()
    conn.close()
    return jsonify({'stats': stats, 'history': history, 'achievements': achievements})


@app.route('/stats')
def global_stats():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM predictions')
    total_predictions = c.fetchone()[0]
    c.execute('SELECT SUM(points) FROM users')
    total_points = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM games WHERE status = "finished"')
    finished_games = c.fetchone()[0]
    c.execute('SELECT AVG(points) FROM predictions WHERE points > 0')
    avg_points_per_prediction = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM predictions WHERE perfect_podium = 1')
    perfect_podiums = c.fetchone()[0]
    conn.close()
    return render_template('stats.html',
                           total_users=total_users,
                           total_predictions=total_predictions,
                           total_points=total_points,
                           finished_games=finished_games,
                           avg_points=round(avg_points_per_prediction, 1),
                           perfect_podiums=perfect_podiums)


# ============ МАРШРУТЫ ДЛЯ НОВОСТЕЙ ============

@app.route('/news/preparation')
def news_preparation():
    return render_template('news/preparation.html')


@app.route('/news/juniors')
def news_juniors():
    return render_template('news/juniors.html')


@app.route('/news/olympics')
def news_olympics():
    return render_template('news/olympics.html')


@app.route('/news/<news_key>')
def news_detail(news_key):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM news WHERE news_key = ?', (news_key,))
    news = c.fetchone()
    c.execute('SELECT news_key, title, image_url, date FROM news WHERE news_key != ? ORDER BY date DESC LIMIT 3',
              (news_key,))
    related = c.fetchall()
    conn.close()
    if not news:
        flash('Новость не найдена', 'danger')
        return redirect(url_for('index'))
    return render_template('news_detail.html', news=news, related=related)


# ============ НОВЫЕ МАРШРУТЫ (ДОПОЛНИТЕЛЬНЫЕ) ============

@app.route('/team-auto')
def team_auto():
    json_path = os.path.join('static', 'data', 'team.json')
    team_data = {}
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            team_data = json.load(f)
    return render_template('team_auto.html', team=team_data)


@app.route('/news-auto')
def news_auto():
    news_path = os.path.join('static', 'data', 'news.json')
    news = []
    if os.path.exists(news_path):
        with open(news_path, 'r', encoding='utf-8') as f:
            news = json.load(f)
    return render_template('news_auto.html', news=news)


@app.route('/debug-data')
def debug_data():
    result = "<h1>Собранные данные</h1><style>body{font-family:Arial;padding:20px}pre{background:#f4f4f4;padding:10px}</style>"
    json_files = ['team.json', 'news.json', 'biographies.json']
    for file in json_files:
        path = os.path.join('static', 'data', file)
        result += f"<h2>{file}</h2>"
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            result += f"<p>Файл существует, размер: {os.path.getsize(path)} байт</p>"
            result += f"<pre>{json.dumps(data, ensure_ascii=False, indent=2)[:1000]}...</pre>"
        else:
            result += f"<p style='color:red'>Файл {file} не найден!</p>"
    return result


@app.route('/create-test-data')
def create_test_data_route():
    team_data = {
        'men': {
            'Бородавко / Жмурко': ['Александр Большунов', 'Артём Мальцев', 'Денис Спицов', 'Алексей Червоткин'],
            'Сорина / Черноусова': ['Сергей Устюгов', 'Александр Терентьев', 'Иван Якимушкин', 'Савелий Коростелёв']
        },
        'women': {
            'Сорина / Черноусова': ['Юлия Ступак', 'Наталья Терентьева', 'Татьяна Сорина', 'Вероника Степанова'],
            'Бородавко / Жмурко': ['Анастасия Фалеева', 'Мария Истомина', 'Алёна Баранова']
        },
        'excluded': ['Ермил Вокуев', 'Илья Порошкин'],
        'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    os.makedirs('static/data', exist_ok=True)
    with open('static/data/team.json', 'w', encoding='utf-8') as f:
        json.dump(team_data, f, ensure_ascii=False, indent=2)

    news_data = [
        {'title': 'Александр Большунов выиграл спринт на этапе Кубка России в Тюмени',
         'source': 'Спорт-Экспресс', 'date': datetime.now().strftime('%d.%m.%Y')},
        {'title': 'Сборная России по лыжным гонкам готовится к Олимпиаде 2026 в Италии',
         'source': 'Чемпионат.com', 'date': datetime.now().strftime('%d.%m.%Y')},
        {'title': 'Юлия Ступак: "Чувствую себя отлично, готова к новым победам"',
         'source': 'РИА Новости', 'date': datetime.now().strftime('%d.%m.%Y')}
    ]
    with open('static/data/news.json', 'w', encoding='utf-8') as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)

    bios_data = {
        'Александр Большунов': {
            'name': 'Александр Большунов',
            'birth_date': '31 декабря 1996',
            'birth_place': 'пос. Подывотье, Брянская область',
            'club': 'Динамо',
            'coach': 'Юрий Бородавко',
            'achievements': ['Олимпийский чемпион 2022', 'Обладатель Кубка мира']
        }
    }
    with open('static/data/biographies.json', 'w', encoding='utf-8') as f:
        json.dump(bios_data, f, ensure_ascii=False, indent=2)

    flash('✅ Тестовые данные созданы', 'success')
    return redirect(url_for('debug_data'))


@app.route('/api/team-json')
def get_team_json():
    json_path = os.path.join('static', 'data', 'team.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({'error': 'Файл не найден'}), 404


@app.route('/api/biography/<athlete_name>')
def get_biography(athlete_name):
    bio_path = os.path.join('static', 'data', 'biographies.json')
    if os.path.exists(bio_path):
        with open(bio_path, 'r', encoding='utf-8') as f:
            bios = json.load(f)
            return jsonify(bios.get(athlete_name, {}))
    return jsonify({'error': 'Биография не найдена'}), 404


@app.route('/api/news-auto')
def get_auto_news():
    news_path = os.path.join('static', 'data', 'news.json')
    if os.path.exists(news_path):
        with open(news_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])


@app.route('/admin/update-all')
def admin_update_all():
    if 'user_id' not in session or session['user_id'] != 1:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('index'))
    auto_update_all()
    flash('✅ Все данные обновлены', 'success')
    return redirect(url_for('index'))


# ============ МАРШРУТЫ АВТОРИЗАЦИИ ============

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        birth_date = request.form.get('birth_date')
        notifications = 1 if request.form.get('notifications') else 0
        email_notifications = 1 if request.form.get('email_notifications') else 0

        if password != confirm_password:
            flash('Пароли не совпадают', 'danger')
            return render_template('register.html')
        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов', 'danger')
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        try:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('''
                INSERT INTO users (username, email, password, birth_date, notifications, email_notifications, last_login)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (username, email, hashed_password, birth_date, notifications, email_notifications))
            conn.commit()
            user_id = c.lastrowid
            c.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
            conn.close()
            session['user_id'] = user_id
            session['username'] = username
            session['email'] = email
            flash('Регистрация прошла успешно! Добро пожаловать!', 'success')
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            flash('Пользователь с таким email уже существует', 'danger')
            return render_template('register.html')
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        remember = request.form.get('remember')

        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = c.fetchone()

        if user and check_password_hash(user[3], password):
            c.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user[0],))
            conn.commit()
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['email'] = user[2]
            if remember:
                session.permanent = True
            flash(f'С возвращением, {user[1]}!', 'success')
            conn.close()
            return redirect(url_for('index'))
        else:
            flash('Неверный email или пароль', 'danger')
            conn.close()
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из аккаунта', 'success')
    return redirect(url_for('index'))


@app.route('/profile')
def profile():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите в систему', 'warning')
        return redirect(url_for('login'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = c.fetchone()
    c.execute('SELECT name, icon, earned_at FROM achievements WHERE user_id = ? ORDER BY earned_at DESC',
              (session['user_id'],))
    achievements = c.fetchall()
    conn.close()
    return render_template('profile.html', user=user, achievements=achievements)


@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите в систему', 'warning')
        return redirect(url_for('login'))
    username = request.form['username']
    email = request.form['email']
    birth_date = request.form.get('birth_date')
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        UPDATE users 
        SET username = ?, email = ?, birth_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (username, email, birth_date, session['user_id']))
    conn.commit()
    conn.close()
    session['username'] = username
    session['email'] = email
    flash('Профиль успешно обновлен!', 'success')
    return redirect(url_for('profile'))


@app.route('/update_notifications', methods=['POST'])
def update_notifications():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите в систему', 'warning')
        return redirect(url_for('login'))
    notifications = 1 if request.form.get('news_notifications') else 0
    email_notifications = 1 if request.form.get('email_notifications') else 0
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        UPDATE users 
        SET notifications = ?, email_notifications = ?
        WHERE id = ?
    ''', (notifications, email_notifications, session['user_id']))
    conn.commit()
    conn.close()
    flash('Настройки уведомлений сохранены!', 'success')
    return redirect(url_for('profile'))


@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите в систему', 'warning')
        return redirect(url_for('login'))
    current = request.form['current_password']
    new = request.form['new_password']
    confirm = request.form['confirm_password']
    if new != confirm:
        flash('Новые пароли не совпадают', 'danger')
        return redirect(url_for('profile'))
    if len(new) < 6:
        flash('Пароль должен быть не менее 6 символов', 'danger')
        return redirect(url_for('profile'))
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE id = ?', (session['user_id'],))
    user = c.fetchone()
    if not check_password_hash(user[0], current):
        flash('Неверный текущий пароль', 'danger')
        conn.close()
        return redirect(url_for('profile'))
    hashed = generate_password_hash(new)
    c.execute('UPDATE users SET password = ? WHERE id = ?', (hashed, session['user_id']))
    conn.commit()
    conn.close()
    flash('Пароль успешно изменен!', 'success')
    return redirect(url_for('profile'))


# ============ ОБРАБОТКА ОШИБОК ============

@app.errorhandler(404)
def page_not_found(e):
    return "<h1>404 Страница не найдена</h1><p>Проверьте правильность введенного адреса</p><a href='/'>Вернуться на главную</a>", 404


@app.errorhandler(500)
def internal_server_error(e):
    return "<h1>500 Внутренняя ошибка сервера</h1><p>Что-то пошло не так. Попробуйте позже.</p><a href='/'>Вернуться на главную</a>", 500


@app.route('/news/spring-starts')
def news_spring_starts():
    return render_template('news/spring_starts.html')

@app.route('/news/worldcup-calendar')
def news_worldcup_calendar():
    return render_template('news/worldcup_calendar.html')

@app.route('/news/world-championship-2027')
def news_world_championship_2027():
    return render_template('news/world_championship_2027.html')


# ============ ЗАПУСК СЕРВЕРА ============

if __name__ == '__main__':
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/images', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    os.makedirs('templates/news', exist_ok=True)
    os.makedirs('static/data', exist_ok=True)

    logger.info("🚀 Запуск сервера с автоматическим обновлением...")
    init_scheduler()
    # При первом запуске сразу парсим соревнования
    parse_and_update_games()
    update_events_status()
    auto_update_all()  # остальные обновления

    print("=" * 60)
    print("✅ СЕРВЕР ЗАПУЩЕН!")
    print("=" * 60)
    print("🌐 Главная: http://127.0.0.1:5010/")
    print("📊 Рейтинг: http://127.0.0.1:5010/rating")
    print("🎮 Прогнозы: http://127.0.0.1:5010/games")
    print("🏆 Состав: http://127.0.0.1:5010/team.html")
    print("👤 Спортсмен: http://127.0.0.1:5010/athlete/1")
    print("📅 Соревнования (динамические): http://127.0.0.1:5010/events.html")
    print("=" * 60)

    app.run(debug=True, port