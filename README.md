# CRMP Test Site

Веб-приложение для тестирования кандидатов на должности CRMP-сервера.

## Возможности

- Анкета кандидата: имя/фамилия, возраст, номер аккаунта, ник персонажа
- Базовые должности (Лидер организации, Администрация сервера) — 20 автогенерируемых вопросов, 7 минут
- Кастомные должности — админ сам пишет вопросы, задаёт их количество и время
- Контроль повторного прохождения по IP (14 дней)
- Админ может изменить дату следующей попытки для конкретного кандидата
- Админ-панель: анкеты, ответы, управление должностями, ручное решение

## Установка

```bash
cd crmp_test_site
pip install -r requirements.txt
```

## Запуск

```bash
# Стандартный запуск (логин admin / пароль admin123)
python app.py

# Свои данные админа через переменные окружения
CRMP_ADMIN_USER=myadmin CRMP_ADMIN_PASS=mypassword python app.py
```

Сайт откроется на http://localhost:5000

## Структура

```
crmp_test_site/
  app.py               # основной Flask-приложение
  questions_pool.py     # пул вопросов для базовых должностей (61 вопрос)
  crmp_test.db          # SQLite база (создаётся автоматически)
  requirements.txt
  templates/
    base.html
    index.html
    test_form.html
    test.html
    test_result.html
    admin_login.html
    admin_dashboard.html
    admin_positions.html
    admin_position_questions.html
    admin_candidates.html
    admin_candidate_detail.html
  static/
    style.css
```

## База данных

SQLite, файл `crmp_test.db` создаётся автоматически при первом запуске.
Базовые должности добавляются автоматически.

## Админ-панель

- URL: http://localhost:5000/admin/login
- Логин по умолчанию: admin
- Пароль по умолчанию: admin123
- **Смените пароль через переменные окружения!**

## Безопасность

- Перед публикацией смените пароль администратора
- Используйте HTTPS (reverse proxy с TLS)
- Регулярно делайте резервные копии crmp_test.db
