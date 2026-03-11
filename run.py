from flask import Flask, render_template, send_from_directory, url_for
import os

app = Flask(__name__,
            static_folder='static',
            template_folder='.')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/team.html')
def team():
    return render_template('team.html')


@app.route('/events.html')
def events():
    return render_template('events.html')


@app.route('/static/css/<path:filename>')
def serve_css(filename):
    return send_from_directory('static/css', filename)


if __name__ == '__main__':
    # Создаем структуру папок
    os.makedirs('static/css', exist_ok=True)

    app.run(debug=True, port=5000)