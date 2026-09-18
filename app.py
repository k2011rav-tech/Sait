"""
CRMP Test Site - Flask + SQLite for testing CRMP server candidates.
"""

import os
import json
import random
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
import sqlite3

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort, g
)

from questions_pool import QUESTIONS_POOL

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crmp_test.db')

ADMIN_USERNAME = os.environ.get('CRMP_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('CRMP_ADMIN_PASS', 'admin123')

BASE_POSITIONS = ['Лидер организации', 'Администрация сервера']
QUESTIONS_PER_BASE_TEST = 20
BASE_TIME_LIMIT_MIN = 7
RETAKEN_DAYS = 14


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'custom',
    time_limit  INTEGER NOT NULL DEFAULT 7,
    questions_count INTEGER NOT NULL DEFAULT 20,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS custom_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    question    TEXT    NOT NULL,
    option_a    TEXT    NOT NULL,
    option_b    TEXT    NOT NULL,
    option_c    TEXT    NOT NULL,
    option_d    TEXT    NOT NULL,
    correct     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name        TEXT    NOT NULL,
    last_name         TEXT    NOT NULL,
    age               INTEGER NOT NULL,
    account_number    TEXT    NOT NULL,
    character_nickname TEXT   NOT NULL,
    position_id       INTEGER NOT NULL,
    ip_address        TEXT    NOT NULL,
    test_date         TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    next_attempt_date TEXT,
    answers_json      TEXT,
    score             INTEGER,
    total_questions   INTEGER,
    status            TEXT    NOT NULL DEFAULT 'pending',
    admin_notes       TEXT    DEFAULT '',
    FOREIGN KEY (position_id) REFERENCES positions(id)
);
"""


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SQL_SCHEMA)
    db.commit()
    cur = db.execute("SELECT COUNT(*) FROM positions WHERE type = 'base'")
    if cur.fetchone()[0] == 0:
        for pname in BASE_POSITIONS:
            db.execute(
                "INSERT INTO positions (name, type, time_limit, questions_count) VALUES (?, 'base', ?, ?)",
                (pname, BASE_TIME_LIMIT_MIN, QUESTIONS_PER_BASE_TEST)
            )
        db.commit()
    db.close()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Требуется авторизация.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    db = get_db()
    positions = db.execute('SELECT * FROM positions ORDER BY id').fetchall()
    return render_template('index.html', positions=positions)


@app.route('/test', methods=['GET', 'POST'])
def test_form():
    db = get_db()
    positions = db.execute('SELECT * FROM positions ORDER BY id').fetchall()
    if request.method == 'GET':
        return render_template('test_form.html', positions=positions)
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    age = request.form.get('age', '').strip()
    account_number = request.form.get('account_number', '').strip()
    character_nickname = request.form.get('character_nickname', '').strip()
    position_id = request.form.get('position_id', '')
    if not all([first_name, last_name, age, account_number, character_nickname, position_id]):
        flash('Все поля обязательны.', 'danger')
        return render_template('test_form.html', positions=positions)
    try:
        age_int = int(age)
        if age_int < 14 or age_int > 99:
            raise ValueError
    except ValueError:
        flash('Возраст: число от 14 до 99.', 'danger')
        return render_template('test_form.html', positions=positions)
    position = db.execute('SELECT * FROM positions WHERE id = ?', (position_id,)).fetchone()
    if not position:
        flash('Должность не найдена.', 'danger')
        return render_template('test_form.html', positions=positions)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0')
    ip = ip.split(',')[0].strip()
    last_attempt = db.execute(
        'SELECT * FROM candidates WHERE ip_address = ? AND position_id = ? ORDER BY test_date DESC LIMIT 1',
        (ip, position_id)
    ).fetchone()
    if last_attempt:
        next_date_str = last_attempt['next_attempt_date']
        if next_date_str:
            next_date = datetime.strptime(next_date_str, '%Y-%m-%d %H:%M:%S')
            if datetime.now() < next_date:
                remaining = next_date - datetime.now()
                days = remaining.days
                hours = remaining.seconds // 3600
                flash(f'Повторное прохождение через {days} дн. {hours} ч.', 'warning')
                return render_template('test_form.html', positions=positions)
    session['candidate'] = {
        'first_name': first_name, 'last_name': last_name, 'age': age_int,
        'account_number': account_number, 'character_nickname': character_nickname,
        'position_id': int(position_id), 'ip': ip,
    }
    return redirect(url_for('test_start'))


@app.route('/test/start')
def test_start():
    candidate = session.get('candidate')
    if not candidate:
        flash('Заполните анкету.', 'warning')
        return redirect(url_for('test_form'))
    db = get_db()
    position = db.execute('SELECT * FROM positions WHERE id = ?', (candidate['position_id'],)).fetchone()
    if not position:
        flash('Должность не найдена.', 'danger')
        return redirect(url_for('test_form'))
    if position['type'] == 'base':
        pool = QUESTIONS_POOL.copy()
        random.shuffle(pool)
        num = min(position['questions_count'], len(pool))
        selected = pool[:num]
        test_questions = []
        for q in selected:
            options = q['options'][:]
            correct_text = options[q['correct']]
            random.shuffle(options)
            test_questions.append({'question': q['q'], 'options': options, 'correct_text': correct_text})
    else:
        cqs = db.execute('SELECT * FROM custom_questions WHERE position_id = ? ORDER BY id', (position['id'],)).fetchall()
        if not cqs:
            flash('Для этой должности нет вопросов.', 'warning')
            return redirect(url_for('test_form'))
        num = min(position['questions_count'], len(cqs))
        selected = random.sample(list(cqs), num)
        test_questions = []
        for cq in selected:
            options = [cq['option_a'], cq['option_b'], cq['option_c'], cq['option_d']]
            correct_idx = cq['correct']
            correct_text = options[correct_idx] if 0 <= correct_idx < len(options) else options[0]
            indexed = list(enumerate(options))
            random.shuffle(indexed)
            shuffled_options = [o for _, o in indexed]
            test_questions.append({'question': cq['question'], 'options': shuffled_options, 'correct_text': correct_text})
    session['test'] = {'position_id': position['id'], 'position_name': position['name'], 'time_limit': position['time_limit'], 'questions': test_questions}
    session['test_start_time'] = datetime.now().isoformat()
    return render_template('test.html', position=position, questions=test_questions, time_limit=position['time_limit'])


@app.route('/test/submit', methods=['POST'])
def test_submit():
    test = session.get('test')
    candidate = session.get('candidate')
    if not test or not candidate:
        flash('Тест не найден.', 'danger')
        return redirect(url_for('test_form'))
    start_time = datetime.fromisoformat(session['test_start_time'])
    elapsed = (datetime.now() - start_time).total_seconds()
    time_limit_sec = test['time_limit'] * 60
    answers = []
    correct_count = 0
    for i, q in enumerate(test['questions']):
        user_answer = request.form.get(f'q{i}', '')
        answers.append({'question': q['question'], 'user_answer': user_answer, 'correct_answer': q['correct_text'], 'is_correct': user_answer == q['correct_text']})
        if user_answer == q['correct_text']:
            correct_count += 1
    total = len(test['questions'])
    time_exceeded = elapsed > time_limit_sec + 10
    db = get_db()
    next_attempt = (datetime.now() + timedelta(days=RETAKEN_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        "INSERT INTO candidates (first_name, last_name, age, account_number, character_nickname, position_id, ip_address, next_attempt_date, answers_json, score, total_questions, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate['first_name'], candidate['last_name'], candidate['age'], candidate['account_number'], candidate['character_nickname'], candidate['position_id'], candidate['ip'], next_attempt, json.dumps(answers, ensure_ascii=False), correct_count, total, 'pending')
    )
    db.commit()
    session.pop('test', None)
    session.pop('test_start_time', None)
    session.pop('candidate', None)
    return render_template('test_result.html', correct=correct_count, total=total, time_exceeded=time_exceeded, elapsed_sec=int(elapsed))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'GET':
        return render_template('admin_login.html')
    username = request.form.get('username', '')
    password = request.form.get('password', '')
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        flash('Вход выполнен.', 'success')
        return redirect(url_for('admin_dashboard'))
    flash('Неверный логин или пароль.', 'danger')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Вы вышли.', 'info')
    return redirect(url_for('index'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {}
    stats['total_candidates'] = db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0]
    stats['pending'] = db.execute("SELECT COUNT(*) FROM candidates WHERE status = 'pending'").fetchone()[0]
    stats['passed'] = db.execute("SELECT COUNT(*) FROM candidates WHERE status = 'passed'").fetchone()[0]
    stats['failed'] = db.execute("SELECT COUNT(*) FROM candidates WHERE status = 'failed'").fetchone()[0]
    stats['positions'] = db.execute('SELECT COUNT(*) FROM positions').fetchone()[0]
    recent = db.execute('SELECT c.*, p.name as position_name FROM candidates c JOIN positions p ON c.position_id = p.id ORDER BY c.test_date DESC LIMIT 10').fetchall()
    return render_template('admin_dashboard.html', stats=stats, recent=recent)


@app.route('/admin/positions')
@admin_required
def admin_positions():
    db = get_db()
    positions = db.execute('SELECT * FROM positions ORDER BY id').fetchall()
    return render_template('admin_positions.html', positions=positions)


@app.route('/admin/positions/add', methods=['POST'])
@admin_required
def admin_position_add():
    name = request.form.get('name', '').strip()
    time_limit = request.form.get('time_limit', '7').strip()
    questions_count = request.form.get('questions_count', '20').strip()
    if not name:
        flash('Укажите название.', 'danger')
        return redirect(url_for('admin_positions'))
    try:
        tl = int(time_limit)
        qc = int(questions_count)
        if tl < 1 or tl > 180 or qc < 1 or qc > 100:
            raise ValueError
    except ValueError:
        flash('Время: 1-180 мин. Вопросов: 1-100.', 'danger')
        return redirect(url_for('admin_positions'))
    db = get_db()
    db.execute("INSERT INTO positions (name, type, time_limit, questions_count) VALUES (?, 'custom', ?, ?)", (name, tl, qc))
    db.commit()
    flash(f'Должность «{name}» добавлена.', 'success')
    return redirect(url_for('admin_positions'))


@app.route('/admin/positions/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_position_delete(pid):
    db = get_db()
    pos = db.execute('SELECT * FROM positions WHERE id = ?', (pid,)).fetchone()
    if not pos:
        flash('Не найдена.', 'danger')
        return redirect(url_for('admin_positions'))
    if pos['type'] == 'base':
        flash('Базовые должности нельзя удалить.', 'danger')
        return redirect(url_for('admin_positions'))
    db.execute('DELETE FROM positions WHERE id = ?', (pid,))
    db.commit()
    flash(f'Должность «{pos["name"]}» удалена.', 'info')
    return redirect(url_for('admin_positions'))


@app.route('/admin/positions/<int:pid>/questions', methods=['GET', 'POST'])
@admin_required
def admin_position_questions(pid):
    db = get_db()
    position = db.execute('SELECT * FROM positions WHERE id = ?', (pid,)).fetchone()
    if not position:
        flash('Не найдена.', 'danger')
        return redirect(url_for('admin_positions'))
    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        option_a = request.form.get('option_a', '').strip()
        option_b = request.form.get('option_b', '').strip()
        option_c = request.form.get('option_c', '').strip()
        option_d = request.form.get('option_d', '').strip()
        correct = request.form.get('correct', '0')
        if not all([question, option_a, option_b, option_c, option_d]):
            flash('Все поля обязательны.', 'danger')
            return redirect(url_for('admin_position_questions', pid=pid))
        try:
            correct_int = int(correct)
            if correct_int < 0 or correct_int > 3:
                raise ValueError
        except ValueError:
            correct_int = 0
        db.execute("INSERT INTO custom_questions (position_id, question, option_a, option_b, option_c, option_d, correct) VALUES (?, ?, ?, ?, ?, ?, ?)", (pid, question, option_a, option_b, option_c, option_d, correct_int))
        db.commit()
        flash('Вопрос добавлен.', 'success')
        return redirect(url_for('admin_position_questions', pid=pid))
    questions = db.execute('SELECT * FROM custom_questions WHERE position_id = ? ORDER BY id', (pid,)).fetchall()
    return render_template('admin_position_questions.html', position=position, questions=questions)


@app.route('/admin/positions/<int:pid>/questions/<int:qid>/delete', methods=['POST'])
@admin_required
def admin_question_delete(pid, qid):
    db = get_db()
    db.execute('DELETE FROM custom_questions WHERE id = ? AND position_id = ?', (qid, pid))
    db.commit()
    flash('Вопрос удален.', 'info')
    return redirect(url_for('admin_position_questions', pid=pid))


@app.route('/admin/candidates')
@admin_required
def admin_candidates():
    db = get_db()
    status_filter = request.args.get('status', '')
    position_filter = request.args.get('position', '')
    query = 'SELECT c.*, p.name as position_name FROM candidates c JOIN positions p ON c.position_id = p.id WHERE 1=1'
    params = []
    if status_filter:
        query += ' AND c.status = ?'
        params.append(status_filter)
    if position_filter:
        query += ' AND c.position_id = ?'
        params.append(position_filter)
    query += ' ORDER BY c.test_date DESC'
    candidates = db.execute(query, params).fetchall()
    positions = db.execute('SELECT * FROM positions ORDER BY id').fetchall()
    return render_template('admin_candidates.html', candidates=candidates, positions=positions, current_status=status_filter, current_position=position_filter)


@app.route('/admin/candidates/<int:cid>')
@admin_required
def admin_candidate_detail(cid):
    db = get_db()
    candidate = db.execute('SELECT c.*, p.name as position_name FROM candidates c JOIN positions p ON c.position_id = p.id WHERE c.id = ?', (cid,)).fetchone()
    if not candidate:
        flash('Кандидат не найден.', 'danger')
        return redirect(url_for('admin_candidates'))
    answers = json.loads(candidate['answers_json']) if candidate['answers_json'] else []
    return render_template('admin_candidate_detail.html', candidate=candidate, answers=answers)


@app.route('/admin/candidates/<int:cid>/decision', methods=['POST'])
@admin_required
def admin_candidate_decision(cid):
    db = get_db()
    decision = request.form.get('decision')
    notes = request.form.get('admin_notes', '').strip()
    if decision not in ('passed', 'failed', 'pending'):
        flash('Некорректное решение.', 'danger')
        return redirect(url_for('admin_candidate_detail', cid=cid))
    db.execute('UPDATE candidates SET status = ?, admin_notes = ? WHERE id = ?', (decision, notes, cid))
    db.commit()
    labels = {'passed': 'Принят', 'failed': 'Отклонен', 'pending': 'Ожидает'}
    flash(f'Решение: {labels.get(decision, decision)}', 'success')
    return redirect(url_for('admin_candidate_detail', cid=cid))


@app.route('/admin/candidates/<int:cid>/retake-date', methods=['POST'])
@admin_required
def admin_candidate_retake(cid):
    db = get_db()
    new_date = request.form.get('next_attempt_date', '').strip()
    try:
        if new_date:
            dt = datetime.strptime(new_date, '%Y-%m-%dT%H:%M')
            new_date_db = dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            new_date_db = None
    except ValueError:
        flash('Некорректный формат даты.', 'danger')
        return redirect(url_for('admin_candidate_detail', cid=cid))
    db.execute('UPDATE candidates SET next_attempt_date = ? WHERE id = ?', (new_date_db, cid))
    db.commit()
    flash('Дата обновлена.', 'success')
    return redirect(url_for('admin_candidate_detail', cid=cid))


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
