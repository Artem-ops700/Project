from flask import Flask, render_template, send_from_directory, url_for, request, redirect, session, flash, jsonify
import os
import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import json

app = Flask(__name__,
            static_folder='static',
            static_url_path='/static',
            template_folder='templates')

app.secret_key = 'your-secret-key-here-change-it-2025'
app.permanent_session_lifetime = 3600  # Сессия живет 1 час


# ============ РАСШИРЕННЫЕ ФУНКЦИИ ДЛЯ БАЗЫ ДАННЫХ ============

def init_db():
    """Создает все таблицы в базе данных с расширенными полями"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # ===== ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ (расширенная) =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            birth_date TEXT,
            notifications BOOLEAN DEFAULT 1,
            email_notifications BOOLEAN DEFAULT 1,

            -- Статистика игрока
            points INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            total_predictions INTEGER DEFAULT 0,
            perfect_podiums INTEGER DEFAULT 0,

            -- Временные метки
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
            status TEXT DEFAULT 'upcoming', -- upcoming, active, finished
            points_multiplier REAL DEFAULT 1.0, -- множитель баллов
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    ''')

    # ===== ТАБЛИЦА ПРОГНОЗОВ (расширенная) =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL,
            winner TEXT NOT NULL,
            second TEXT,
            third TEXT,

            -- Результаты прогноза
            points INTEGER DEFAULT 0,
            is_correct BOOLEAN DEFAULT 0,
            perfect_podium BOOLEAN DEFAULT 0,

            -- Временные метки
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (game_id) REFERENCES games (id),
            UNIQUE(user_id, game_id)
        )
    ''')

    # ===== ТАБЛИЦА ДОСТИЖЕНИЙ (ИСПРАВЛЕНО) =====
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

    # ===== ТАБЛИЦА ИСТОРИИ БАЛЛОВ (для графиков) =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS points_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            points_change INTEGER NOT NULL,
            total_points INTEGER NOT NULL,
            source TEXT NOT NULL, -- prediction, achievement, bonus
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

    conn.commit()
    conn.close()
    print("✅ Расширенная база данных инициализирована")


def init_games():
    """Заполняет таблицу games данными о соревнованиях с множителями"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Проверяем, есть ли уже данные
    c.execute('SELECT COUNT(*) FROM games')
    count = c.fetchone()[0]

    if count == 0:
        # Добавляем соревнования с множителями баллов
        games_data = [
            ('event1', 'Кубок Европы — спринт', '5 января 2025', 'Лахти, Финляндия', 1.0),
            ('event2', 'Чемпионат страны', '20 января 2025', 'Тюмень', 1.5),
            ('event3', 'Международный марафон', '10 февраля 2025', 'Осло, Норвегия', 1.2),
            ('event4', 'Олимпийские игры (эстафета)', '15 февраля 2025', 'Кортина-д\'Ампеццо, Италия', 2.0),
            ('event5', 'Кубок Европы — разделка', '1 марта 2025', 'Лахти, Финляндия', 1.0),
            ('event6', 'Чемпионат мира', '15 марта 2025', 'Тронхейм, Норвегия', 1.8),
        ]

        for game in games_data:
            c.execute('''
                INSERT OR IGNORE INTO games (event_id, event_name, event_date, event_place, points_multiplier)
                VALUES (?, ?, ?, ?, ?)
            ''', game)

        conn.commit()
        print("✅ Игры добавлены в базу данных с множителями")
    else:
        print("✅ Игры уже есть в базе данных")

    conn.close()


def init_achievements():
    """Инициализирует список всех возможных достижений"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Создаем таблицу со списком достижений
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

    # Очищаем таблицу
    c.execute('DELETE FROM achievements_list')

    # Список всех достижений
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
    print("✅ Список достижений инициализирован")


# ============ НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С БАЛЛАМИ ============

def add_points(user_id, points, source, source_id=None):
    """Добавляет баллы пользователю и записывает в историю"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Получаем текущие баллы
    c.execute('SELECT points FROM users WHERE id = ?', (user_id,))
    current_points = c.fetchone()[0]
    new_total = current_points + points

    # Обновляем баллы пользователя
    c.execute('UPDATE users SET points = ? WHERE id = ?', (new_total, user_id))

    # Записываем в историю
    c.execute('''
        INSERT INTO points_history (user_id, points_change, total_points, source, source_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, points, new_total, source, source_id))

    conn.commit()
    conn.close()

    # Проверяем достижения после добавления баллов
    check_all_achievements(user_id)

    return new_total


def check_all_achievements(user_id):
    """Проверяет все достижения пользователя"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Получаем статистику пользователя
    c.execute('''
        SELECT points, correct_predictions, total_predictions, perfect_podiums
        FROM users WHERE id = ?
    ''', (user_id,))
    user_stats = c.fetchone()

    if not user_stats:
        conn.close()
        return

    points, correct, total, perfect = user_stats

    # Проверяем каждое достижение
    achievements_to_check = []

    # Прогнозы
    if total >= 1:
        achievements_to_check.append(('first_prediction', user_id))
    if total >= 5:
        achievements_to_check.append(('five_predictions', user_id))
    if total >= 10:
        achievements_to_check.append(('ten_predictions', user_id))

    # Правильные прогнозы
    if correct >= 1:
        achievements_to_check.append(('first_correct', user_id))
    if correct >= 5:
        achievements_to_check.append(('five_correct', user_id))
    if correct >= 10:
        achievements_to_check.append(('ten_correct', user_id))

    # Точные подиумы
    if perfect >= 1:
        achievements_to_check.append(('perfect_podium', user_id))
    if perfect >= 3:
        achievements_to_check.append(('three_perfect', user_id))

    # Баллы
    if points >= 100:
        achievements_to_check.append(('hundred_points', user_id))
    if points >= 500:
        achievements_to_check.append(('five_hundred_points', user_id))
    if points >= 1000:
        achievements_to_check.append(('thousand_points', user_id))

    # Добавляем достижения
    for achievement_key, uid in achievements_to_check:
        # Получаем информацию о достижении
        c.execute('SELECT name, description, icon, points_bonus FROM achievements_list WHERE achievement_key = ?',
                  (achievement_key,))
        ach_data = c.fetchone()

        if ach_data:
            name, desc, icon, bonus = ach_data

            # Проверяем, есть ли уже такое достижение
            c.execute('SELECT id FROM achievements WHERE user_id = ? AND achievement_key = ?', (uid, achievement_key))
            if not c.fetchone():
                # Добавляем достижение
                c.execute('''
                    INSERT INTO achievements (user_id, achievement_key, name, description, icon, points_awarded)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (uid, achievement_key, name, desc, icon, bonus))

                # Начисляем бонусные баллы за достижение
                if bonus > 0:
                    add_points(uid, bonus, 'achievement', c.lastrowid)

    conn.commit()
    conn.close()


def get_user_rank(user_id):
    """Получает место пользователя в рейтинге"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    c.execute('''
        SELECT COUNT(*) + 1 
        FROM users 
        WHERE points > (SELECT points FROM users WHERE id = ?)
    ''', (user_id,))

    rank = c.fetchone()[0]
    conn.close()

    return rank


def get_top_users(limit=10):
    """Получает топ пользователей"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    c.execute('''
        SELECT username, points, correct_predictions, total_predictions, perfect_podiums
        FROM users 
        WHERE points > 0 OR total_predictions > 0
        ORDER BY points DESC 
        LIMIT ?
    ''', (limit,))

    users = c.fetchall()
    conn.close()

    result = []
    for user in users:
        # Определяем звание по баллам
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

        # Точность прогнозов
        accuracy = 0
        if user[3] > 0:
            accuracy = round((user[2] / user[3]) * 100, 1)

        result.append({
            'username': user[0],
            'points': user[1],
            'correct': user[2],
            'total': user[3],
            'perfect': user[4],
            'rank': rank,
            'accuracy': accuracy
        })

    return result


# ============ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ============
init_db()
init_games()
init_achievements()


# ============ ОСНОВНЫЕ МАРШРУТЫ ============

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/team.html')
def team():
    return render_template('team.html')


@app.route('/events.html')
def events():
    return render_template('events.html')


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

        # Валидация
        if password != confirm_password:
            flash('Пароли не совпадают', 'danger')
            return render_template('register.html')

        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов', 'danger')
            return render_template('register.html')

        # Хешируем пароль
        hashed_password = generate_password_hash(password)

        try:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('''
                INSERT INTO users (username, email, password, birth_date, notifications, email_notifications, last_login)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (username, email, hashed_password, birth_date, notifications, email_notifications))
            conn.commit()

            # Получаем ID нового пользователя
            user_id = c.lastrowid

            # Создаем настройки пользователя
            c.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))

            conn.close()

            # Сразу авторизуем пользователя
            session['user_id'] = user_id
            session['username'] = username
            session['email'] = email
            session['login_time'] = str(datetime.now())

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
            # Обновляем время последнего входа
            c.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user[0],))
            conn.commit()

            session['user_id'] = user[0]
            session['username'] = user[1]
            session['email'] = user[2]
            session['login_time'] = str(datetime.now())

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

    # Получаем достижения пользователя
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

    # Обновляем сессию
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

    current_password = request.form['current_password']
    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']

    if new_password != confirm_password:
        flash('Новые пароли не совпадают', 'danger')
        return redirect(url_for('profile'))

    if len(new_password) < 6:
        flash('Пароль должен быть не менее 6 символов', 'danger')
        return redirect(url_for('profile'))

    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE id = ?', (session['user_id'],))
    user = c.fetchone()

    if not check_password_hash(user[0], current_password):
        flash('Неверный текущий пароль', 'danger')
        conn.close()
        return redirect(url_for('profile'))

    hashed_password = generate_password_hash(new_password)
    c.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, session['user_id']))
    conn.commit()
    conn.close()

    flash('Пароль успешно изменен!', 'success')
    return redirect(url_for('profile'))


# ============ РЕЙТИНГ БОЛЕЛЬЩИКОВ ============

@app.route('/rating')
def rating():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Топ-100 пользователей по баллам
    c.execute('''
        SELECT username, points, correct_predictions, total_predictions, perfect_podiums,
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

    # Статистика по всем пользователям
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]

    c.execute('SELECT SUM(points) FROM users')
    total_points = c.fetchone()[0] or 0

    c.execute('SELECT AVG(points) FROM users WHERE points > 0')
    avg_points = c.fetchone()[0] or 0

    conn.close()

    # Если пользователь авторизован, получаем его место
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
            user_stats = {
                'points': stats[0],
                'predictions': stats[1]
            }
        conn.close()

    return render_template('rating.html',
                           top_users=top_users,
                           total_users=total_users,
                           total_points=total_points,
                           avg_points=round(avg_points, 1),
                           user_rank=user_rank,
                           user_stats=user_stats)


# ============ API ДЛЯ РЕЙТИНГА ============

@app.route('/api/top-users')
def api_top_users():
    """API для получения топ-10 пользователей"""
    top_users = get_top_users(10)

    # Получаем данные текущего пользователя (если авторизован)
    current_user = None
    if 'user_id' in session:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT points, total_predictions FROM users WHERE id = ?', (session['user_id'],))
        user_data = c.fetchone()
        conn.close()

        if user_data:
            # Определяем звание
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

            current_user = {
                'points': user_data[0],
                'predictions': user_data[1],
                'rank': rank
            }

    return jsonify({
        'top_users': top_users,
        'current_user': current_user
    })


@app.route('/api/user-stats/<int:user_id>')
def api_user_stats(user_id):
    """API для получения статистики пользователя"""
    if user_id != session.get('user_id') and session.get('user_id') != 1:
        return jsonify({'error': 'Доступ запрещен'}), 403

    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Получаем статистику
    c.execute('''
        SELECT points, correct_predictions, total_predictions, perfect_podiums, created_at
        FROM users WHERE id = ?
    ''', (user_id,))
    stats = c.fetchone()

    # Получаем историю баллов
    c.execute('''
        SELECT points_change, total_points, source, created_at
        FROM points_history 
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 20
    ''', (user_id,))
    history = c.fetchall()

    # Получаем достижения
    c.execute('''
        SELECT name, icon, earned_at
        FROM achievements 
        WHERE user_id = ?
        ORDER BY earned_at DESC
    ''', (user_id,))
    achievements = c.fetchall()

    conn.close()

    return jsonify({
        'stats': stats,
        'history': history,
        'achievements': achievements
    })


# ============ ИГРА "УГАДАЙ ПОБЕДИТЕЛЯ" ============

@app.route('/games')
def games_list():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Получаем все игры
    c.execute('''
        SELECT * FROM games 
        ORDER BY 
            CASE status
                WHEN 'upcoming' THEN 1
                WHEN 'active' THEN 2
                WHEN 'finished' THEN 3
            END,
            event_date
    ''')
    games = c.fetchall()

    # Для авторизованных пользователей - их прогнозы
    user_predictions = {}
    if 'user_id' in session:
        c.execute('SELECT game_id, winner FROM predictions WHERE user_id = ?',
                  (session['user_id'],))
        for pred in c.fetchall():
            user_predictions[pred[0]] = pred[1]

    conn.close()

    return render_template('games_list.html',
                           games=games,
                           user_predictions=user_predictions)


@app.route('/game/<int:game_id>')
def game_detail(game_id):
    if 'user_id' not in session:
        flash('Войдите, чтобы делать прогнозы', 'warning')
        return redirect(url_for('login'))

    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Информация об игре
    c.execute('SELECT * FROM games WHERE id = ?', (game_id,))
    game = c.fetchone()

    # Прогноз пользователя (если есть)
    c.execute('SELECT * FROM predictions WHERE user_id = ? AND game_id = ?',
              (session['user_id'], game_id))
    user_prediction = c.fetchone()

    # Список спортсменов для выбора
    athletes = [
        'Александр Большунов',
        'Сергей Устюгов',
        'Артём Мальцев',
        'Денис Спицов',
        'Алексей Червоткин',
        'Иван Якимушкин',
        'Савелий Коростелёв',
        'Александр Терентьев',
        'Юлия Ступак',
        'Наталья Терентьева',
        'Татьяна Сорина',
        'Вероника Степанова',
        'Дарья Непряева',
        'Анастасия Кулешова',
    ]

    conn.close()

    return render_template('game_detail.html',
                           game=game,
                           user_prediction=user_prediction,
                           athletes=athletes)


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

    # Проверяем, есть ли уже прогноз
    c.execute('SELECT id FROM predictions WHERE user_id = ? AND game_id = ?',
              (session['user_id'], game_id))
    existing = c.fetchone()

    if existing:
        # Обновляем существующий
        c.execute('''
            UPDATE predictions 
            SET winner = ?, second = ?, third = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND game_id = ?
        ''', (winner, second, third, session['user_id'], game_id))
        flash('Прогноз обновлен!', 'success')
    else:
        # Добавляем новый
        c.execute('''
            INSERT INTO predictions (user_id, game_id, winner, second, third)
            VALUES (?, ?, ?, ?, ?)
        ''', (session['user_id'], game_id, winner, second, third))

        # Увеличиваем счетчик total_predictions
        c.execute('''
            UPDATE users 
            SET total_predictions = total_predictions + 1 
            WHERE id = ?
        ''', (session['user_id'],))

        conn.commit()

        # Проверяем достижения после первого прогноза
        check_all_achievements(session['user_id'])

        flash('Прогноз сохранен!', 'success')

    conn.commit()
    conn.close()

    return redirect(url_for('game_detail', game_id=game_id))


@app.route('/game/<int:game_id>/results', methods=['POST'])
def set_results(game_id):
    # Этот маршрут для админа (ID админа = 1)
    if 'user_id' not in session or session['user_id'] != 1:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('games_list'))

    winner = request.form['winner']
    second = request.form['second']
    third = request.form['third']

    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Получаем информацию об игре (множитель баллов)
    c.execute('SELECT points_multiplier FROM games WHERE id = ?', (game_id,))
    multiplier = c.fetchone()[0] or 1.0

    # Обновляем результаты игры
    c.execute('''
        UPDATE games 
        SET winner = ?, second = ?, third = ?, status = 'finished', finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (winner, second, third, game_id))

    # Получаем все прогнозы на эту игру
    c.execute('SELECT id, user_id, winner, second, third FROM predictions WHERE game_id = ?',
              (game_id,))
    predictions = c.fetchall()

    # Начисляем баллы
    for pred in predictions:
        pred_id, user_id, pred_winner, pred_second, pred_third = pred
        points = 0
        perfect = False

        # Угадал победителя
        if pred_winner == winner:
            points += int(10 * multiplier)

        # Угадал второе место
        if pred_second == second:
            points += int(5 * multiplier)

        # Угадал третье место
        if pred_third == third:
            points += int(3 * multiplier)

        # Бонус за точный подиум
        if (pred_winner == winner and pred_second == second and pred_third == third):
            points += int(20 * multiplier)
            perfect = True

        if points > 0:
            # Обновляем баллы в прогнозе
            c.execute('''
                UPDATE predictions 
                SET points = ?, is_correct = 1, perfect_podium = ?
                WHERE id = ?
            ''', (points, perfect, pred_id))

            # Обновляем баллы пользователя
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

            # Записываем в историю
            add_points(user_id, points, 'prediction', pred_id)

            # Проверяем достижения
            check_all_achievements(user_id)

    conn.commit()
    conn.close()

    flash('Результаты сохранены, баллы начислены!', 'success')
    return redirect(url_for('games_list'))


# ============ ДОСТИЖЕНИЯ ============

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

    # Получаем все возможные достижения для сравнения
    c.execute('SELECT * FROM achievements_list ORDER BY points_bonus DESC')
    all_achievements = c.fetchall()

    conn.close()

    return render_template('achievements.html',
                           achievements=achievements,
                           all_achievements=all_achievements)


# ============ СТАТИСТИКА ============

@app.route('/stats')
def global_stats():
    """Глобальная статистика сайта"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Общая статистика
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM predictions')
    total_predictions = c.fetchone()[0]

    c.execute('SELECT SUM(points) FROM users')
    total_points = c.fetchone()[0] or 0

    c.execute('SELECT COUNT(*) FROM games WHERE status = "finished"')
    finished_games = c.fetchone()[0]

    # Статистика по прогнозам
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


# ============ ОБРАБОТКА ОШИБОК ============

@app.errorhandler(404)
def page_not_found(e):
    return "<h1>404 Страница не найдена</h1><p>Проверьте правильность введенного адреса</p><a href='/'>Вернуться на главную</a>", 404


@app.errorhandler(500)
def internal_server_error(e):
    return "<h1>500 Внутренняя ошибка сервера</h1><p>Что-то пошло не так. Попробуйте позже.</p><a href='/'>Вернуться на главную</a>", 500


# ============ ЗАПУСК СЕРВЕРА ============

if __name__ == '__main__':
    # Создаем структуру папок
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/images', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    os.makedirs('templates/news', exist_ok=True)

    print("=" * 60)
    print("✅ СЕРВЕР ЗАПУЩЕН С РАСШИРЕННОЙ БАЗОЙ ДАННЫХ!")
    print("=" * 60)
    print("🌐 Главная: http://127.0.0.1:5010/")
    print("📊 Рейтинг: http://127.0.0.1:5010/rating")
    print("🎮 Прогнозы: http://127.0.0.1:5010/games")
    print("🏆 Достижения: http://127.0.0.1:5010/achievements")
    print("📈 Статистика: http://127.0.0.1:5010/stats")
    print("=" * 60)

    app.run(debug=True, port=5010)