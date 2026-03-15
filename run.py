from flask import Flask, render_template, send_from_directory, url_for, request, redirect, session, flash
import os
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__,
            static_folder='static',
            static_url_path='/static',
            template_folder='templates')

app.secret_key = 'your-secret-key-here-change-it-2025'
app.permanent_session_lifetime = 3600  # Сессия живет 1 час


# ============ ФУНКЦИИ ДЛЯ БАЗЫ ДАННЫХ ============

def init_db():
    """Создает все таблицы в базе данных"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Таблица пользователей
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица игр (соревнований) для прогнозов
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица прогнозов пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL,
            winner TEXT NOT NULL,
            second TEXT,
            third TEXT,
            points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (game_id) REFERENCES games (id),
            UNIQUE(user_id, game_id)
        )
    ''')

    # Таблица достижений
    c.execute('''
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            achievement TEXT NOT NULL,
            description TEXT NOT NULL,
            icon TEXT NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")


def init_games():
    """Заполняет таблицу games данными о соревнованиях"""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Проверяем, есть ли уже данные
    c.execute('SELECT COUNT(*) FROM games')
    count = c.fetchone()[0]

    if count == 0:
        # Добавляем соревнования из events.html
        games_data = [
            ('event1', 'Кубок Европы — спринт', '5 января 2025', 'Лахти, Финляндия'),
            ('event2', 'Чемпионат страны', '20 января 2025', 'Тюмень'),
            ('event3', 'Международный марафон', '10 февраля 2025', 'Осло, Норвегия'),
            ('event4', 'Олимпийские игры (эстафета)', '15 февраля 2025', 'Кортина-д\'Ампеццо, Италия'),
            ('event5', 'Кубок Европы — разделка', '1 марта 2025', 'Лахти, Финляндия'),
            ('event6', 'Чемпионат мира', '15 марта 2025', 'Тронхейм, Норвегия'),
        ]

        for game in games_data:
            c.execute('''
                INSERT OR IGNORE INTO games (event_id, event_name, event_date, event_place)
                VALUES (?, ?, ?, ?)
            ''', game)

        conn.commit()
        print("✅ Игры добавлены в базу данных")
    else:
        print("✅ Игры уже есть в базе данных")

    conn.close()


# ============ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ============
init_db()  # Сначала создаем таблицы
init_games()  # Потом заполняем игры


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
                INSERT INTO users (username, email, password, birth_date, notifications, email_notifications)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (username, email, hashed_password, birth_date, notifications, email_notifications))
            conn.commit()

            # Получаем ID нового пользователя
            user_id = c.lastrowid
            conn.close()

            # Сразу авторизуем пользователя
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
        conn.close()

        if user and check_password_hash(user[3], password):  # user[3] это password
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['email'] = user[2]

            if remember:
                session.permanent = True

            flash('Вы успешно вошли!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверный email или пароль', 'danger')

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
    conn.close()

    return render_template('profile.html', user=user)


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
        SET username = ?, email = ?, birth_date = ?
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

    # Статистика по всем пользователям
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]

    c.execute('SELECT SUM(points) FROM users')
    total_points = c.fetchone()[0] or 0

    conn.close()

    # Если пользователь авторизован, получаем его место
    user_rank = None
    if 'user_id' in session:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*) + 1 
            FROM users 
            WHERE points > (SELECT points FROM users WHERE id = ?)
        ''', (session['user_id'],))
        user_rank = c.fetchone()[0]
        conn.close()

    return render_template('rating.html',
                           top_users=top_users,
                           total_users=total_users,
                           total_points=total_points,
                           user_rank=user_rank)


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
            SET winner = ?, second = ?, third = ?, created_at = CURRENT_TIMESTAMP
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

        flash('Прогноз сохранен!', 'success')

    conn.commit()
    conn.close()

    return redirect(url_for('game_detail', game_id=game_id))


def check_achievements(user_id, cursor):
    """Проверка и начисление достижений"""
    # Получаем статистику пользователя
    cursor.execute('''
        SELECT points, correct_predictions, 
               (SELECT COUNT(*) FROM predictions WHERE user_id = ?) as total
        FROM users WHERE id = ?
    ''', (user_id, user_id))
    user = cursor.fetchone()

    if not user:
        return

    # Первый прогноз
    cursor.execute('SELECT COUNT(*) FROM predictions WHERE user_id = ?', (user_id,))
    if cursor.fetchone()[0] == 1:
        cursor.execute('''
            INSERT OR IGNORE INTO achievements (user_id, achievement, description, icon)
            VALUES (?, ?, ?, ?)
        ''', (user_id, 'Новичок', 'Сделал первый прогноз', '🌱'))

    # 10 правильных прогнозов
    if user[1] >= 10:
        cursor.execute('''
            INSERT OR IGNORE INTO achievements (user_id, achievement, description, icon)
            VALUES (?, ?, ?, ?)
        ''', (user_id, 'Эксперт', '10 правильных прогнозов', '🎯'))

    # 100 баллов
    if user[0] >= 100:
        cursor.execute('''
            INSERT OR IGNORE INTO achievements (user_id, achievement, description, icon)
            VALUES (?, ?, ?, ?)
        ''', (user_id, 'Легенда', '100 баллов', '🏆'))


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

    conn.close()

    return render_template('achievements.html', achievements=achievements)


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

    print("=" * 50)
    print("✅ СЕРВЕР ЗАПУЩЕН!")
    print("=" * 50)
    print("🌐 Главная: http://127.0.0.1:5010/")
    print("📊 Рейтинг: http://127.0.0.1:5010/rating")
    print("🎮 Прогнозы: http://127.0.0.1:5010/games")
    print("=" * 50)

    app.run(debug=True, port=5010)