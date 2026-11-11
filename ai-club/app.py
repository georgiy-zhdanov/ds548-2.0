import time
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
from database import engine, SessionLocal
from models import Base, User, Course, Module, Lesson, calculate_elo
from functools import wraps
from sqlalchemy.orm import joinedload
import requests
import os
import json
import re
from groq import Groq
from dotenv import load_dotenv
load_dotenv()  # Загружает переменные из .env файла
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

groq_client = Groq(api_key=GROQ_API_KEY)

Base.metadata.create_all(bind=engine)

app = Flask(__name__)
app.secret_key = "change_me_to_random_string_in_production"

UPLOAD_FOLDER = '/root/ai_club/ds548-2.0/ai-club/uploads'
ALLOWED_EXTENSIONS = {'pdf', 'ipynb'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


def current_user():
    if "user_id" not in session:
        return None
    db = SessionLocal()
    u = db.query(User).filter(User.id == session["user_id"]).first()
    db.close()
    return u


# --- SEED: демо-курсы при первом запуске ---
def seed():
    db = SessionLocal()
    if db.query(Course).count() > 0:
        db.close()
        return

    # Курс 1: Python основы
    c1 = Course(title="Python с нуля", description="Базовый синтаксис, типы данных, циклы и функции.", author_id=None)
    db.add(c1); db.flush()
    m1 = Module(course_id=c1.id, title="Модуль 1 · Основы", order=1)
    db.add(m1); db.flush()
    db.add(Lesson(module_id=m1.id, title="Переменные и типы", type="markdown", order=1,
                  content="# Переменные\n\nВ Python не нужно объявлять тип переменной.\n\n```python\nx = 10\nname = 'Neo'\n```\n\n## Задание\nСоздай переменную `age` со своим возрастом."))
    db.add(Lesson(module_id=m1.id, title="Практика: Hello World", type="jupyter", order=2,
                  content='{"task": "Напиши функцию greet(name), которая возвращает \\"Привет, {name}!\\""}'))
    db.add(Lesson(module_id=m1.id, title="AI-тренер: циклы", type="ai_trainer", order=3,
                  content='{"topic": "циклы for и while", "steps_count": 3}'))
    db.commit()
    db.close()


seed()


# ================= ROUTES =================

@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/courses")
def courses():
    db = SessionLocal()
    # Загружаем курсы + модули + уроки одним запросом
    all_courses = db.query(Course).options(
        joinedload(Course.modules).joinedload(Module.lessons)
    ).all()
    
    courses_data = []
    for c in all_courses:
        lessons_count = sum(len(m.lessons) for m in c.modules)
        courses_data.append({
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "modules_count": len(c.modules),
            "lessons_count": lessons_count
        })
    db.close()
    return render_template("courses.html", user=current_user(), courses=courses_data)

@app.route("/course/<int:course_id>")
def course(course_id):
    db = SessionLocal()
    c = db.query(Course).options(
        joinedload(Course.modules).joinedload(Module.lessons)
    ).filter(Course.id == course_id).first()
    
    if not c:
        db.close()
        abort(404)
        
    # Преобразуем в словари, чтобы шаблон не пытался делать lazy-load
    course_data = {
        "id": c.id,
        "title": c.title,
        "description": c.description,
        "modules": [{
            "id": m.id,
            "title": m.title,
            "order": m.order,
            "lessons": [{
                "id": l.id,
                "title": l.title,
                "type": l.type,
                "order": l.order
            } for l in m.lessons]
        } for m in c.modules]
    }
    db.close()
    return render_template("course.html", user=current_user(), course=course_data)

@app.route("/lesson/<int:lesson_id>")
def lesson(lesson_id):
    db = SessionLocal()
    l = db.query(Lesson).options(
        joinedload(Lesson.module).joinedload(Module.course)
    ).filter(Lesson.id == lesson_id).first()
    
    if not l:
        db.close()
        abort(404)
        
    m = l.module
    c = m.course
    siblings = sorted(m.lessons, key=lambda x: x.order)
    prev_l = next((x for x in siblings if x.order < l.order), None)
    next_l = next((x for x in siblings if x.order > l.order), None)
    
    lesson_data = {
        "id": l.id,
        "title": l.title,
        "type": l.type,
        "content": l.content
    }
    module_data = {"title": m.title}
    course_data = {"id": c.id, "title": c.title}
    prev_data = {"id": prev_l.id, "title": prev_l.title} if prev_l else None
    next_data = {"id": next_l.id, "title": next_l.title} if next_l else None
    
    db.close()
    return render_template("lesson.html", user=current_user(),
                           lesson=lesson_data, module=module_data, 
                           course=course_data, prev=prev_data, next=next_data)

@app.route("/leaderboard")
def leaderboard():
    db = SessionLocal()
    top = db.query(User).order_by(User.elo_rating.desc()).limit(50).all()
    db.close()
    return render_template("leaderboard.html", user=current_user(), top=top)


@app.route("/profile")
@login_required
def profile():
    user = current_user()
    db = SessionLocal()
    
    # Находим все курсы, которые создал этот пользователь
    my_courses = db.query(Course).options(
        joinedload(Course.modules).joinedload(Module.lessons)
    ).filter(Course.author_id == user.id).all()
    
    courses_data = []
    for c in my_courses:
        lessons_count = sum(len(m.lessons) for m in c.modules)
        courses_data.append({
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "modules_count": len(c.modules),
            "lessons_count": lessons_count
        })
    
    db.close()
    return render_template("profile.html", user=user, my_courses=courses_data)


# ================= AUTH API =================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")
    avatar = data.get("avatar", "🤖")

    if len(username) < 3 or len(password) < 4:
        return jsonify({"error": "Ник ≥3 символов, пароль ≥4"}), 400

    db = SessionLocal()
    if db.query(User).filter(User.username == username).first():
        db.close()
        return jsonify({"error": "Ник уже занят"}), 409

    user = User(username=username, avatar=avatar)
    user.set_password(password)
    db.add(user); db.commit()
    session["user_id"] = user.id
    db.close()
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    db = SessionLocal()
    user = db.query(User).filter(User.username == data.get("username")).first()
    if not user or not user.check_password(data.get("password", "")):
        db.close()
        return jsonify({"error": "Неверный ник или пароль"}), 401
    session["user_id"] = user.id
    db.close()
    return jsonify({"ok": True})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/match", methods=["POST"])
@login_required
def match():
    data = request.json
    opponent_id = data.get("opponent_id")
    result = data.get("result")
    db = SessionLocal()
    me = db.query(User).filter(User.id == session["user_id"]).first()
    opp = db.query(User).filter(User.id == opponent_id).first()
    if not opp:
        db.close()
        return jsonify({"error": "Соперник не найден"}), 404
    score_map = {"win": 1.0, "loss": 0.0, "draw": 0.5}
    score = score_map.get(result)
    if score is None:
        db.close()
        return jsonify({"error": "Неверный результат"}), 400
    me.elo_rating = calculate_elo(me.elo_rating, opp.elo_rating, score)
    opp.elo_rating = calculate_elo(opp.elo_rating, me.elo_rating, 1 - score)
    db.commit(); db.close()
    return jsonify({"ok": True})

@app.route("/api/ai_trainer", methods=["POST"])
@login_required
def ai_trainer():
    data = request.json
    lesson_id = data.get("lesson_id") # Получаем ID урока
    user_message = data.get("message", "")
    history = data.get("history", [])

    if not GROQ_API_KEY or GROQ_API_KEY == "gsk_твой_ключ_здесь":
        return jsonify({
            "feedback": "⚠ В app.py не указан GROQ_API_KEY.",
            "is_correct": False,
            "next_step": "Сначала настрой API-ключ.",
            "lesson_completed": False
        }), 400

    # --- ДОСТАЕМ ТЕМУ ИЗ БАЗЫ ДАННЫХ ---
    db = SessionLocal()
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    
    topic = "Python основы" # Значение по умолчанию
    if lesson:
        try:
            # Парсим JSON, который мы сохранили при создании урока
            config = json.loads(lesson.content)
            topic = config.get("topic", "Python основы")
        except:
            pass
    db.close()
    # ------------------------------------

    system_prompt = f"""Ты — AI-тренер по программированию в НЕЙРОКЛУБЕ. 
Тема урока: {topic}.
Твоя задача: вести ученика по шагам. Не давай готовый код сразу, а направляй.
Отвечай СТРОГО в формате JSON:
{{
  "feedback": "Краткий разбор кода или ответа ученика",
  "is_correct": true или false,
  "next_step": "Следующая задача или подсказка",
  "lesson_completed": true или false
}}"""

    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=messages,
            model="openai/gpt-oss-120b",  
            response_format={"type": "json_object"},
            temperature=0.3
        )
        
        ai_text = chat_completion.choices[0].message.content
        clean_text = re.sub(r'^```json\s*|\s*```$', '', ai_text.strip(), flags=re.MULTILINE)
        parsed_response = json.loads(clean_text)
        return jsonify(parsed_response)

    except Exception as e:
        print(f"\n{'='*50}")
        print(f"GROQ ERROR TYPE: {type(e).__name__}")
        print(f"GROQ ERROR MSG:  {e}")
        print(f"{'='*50}\n")
        
        return jsonify({
            "feedback": f"⚠ Ошибка Groq: {str(e)[:150]}",
            "is_correct": False,
            "next_step": "Проверь консоль Flask для деталей.",
            "lesson_completed": False
        }), 500

# ================= CREATE COURSES =================

@app.route("/create-course")
@login_required
def create_course_page():
    return render_template("create_course.html", user=current_user())


@app.route("/api/create-course", methods=["POST"])
@login_required
def create_course():
    data = request.json
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    
    if not title:
        return jsonify({"error": "Название курса обязательно"}), 400
    
    db = SessionLocal()
    course = Course(
        title=title,
        description=description,
        author_id=session["user_id"]
    )
    db.add(course)
    db.commit()
    course_id = course.id
    db.close()
    
    return jsonify({"ok": True, "course_id": course_id})


@app.route("/api/create-module", methods=["POST"])
@login_required
def create_module():
    data = request.json
    course_id = data.get("course_id")
    title = data.get("title", "").strip()
    
    if not course_id or not title:
        return jsonify({"error": "ID курса и название модуля обязательны"}), 400
    
    db = SessionLocal()
    # Находим максимальный order для этого курса
    max_order = db.query(Module).filter(Module.course_id == course_id).count()
    
    module = Module(
        course_id=course_id,
        title=title,
        order=max_order + 1
    )
    db.add(module)
    db.commit()
    module_id = module.id
    db.close()
    
    return jsonify({"ok": True, "module_id": module_id})


@app.route("/api/create-lesson", methods=["POST"])
@login_required
def create_lesson():
    data = request.json
    module_id = data.get("module_id")
    title = data.get("title", "").strip()
    lesson_type = data.get("type", "markdown")
    content = data.get("content", "")
    
    if not module_id or not title:
        return jsonify({"error": "ID модуля и название урока обязательны"}), 400
    
    db = SessionLocal()
    # Находим максимальный order для этого модуля
    max_order = db.query(Lesson).filter(Lesson.module_id == module_id).count()
    
    # Формируем content в зависимости от типа
    if lesson_type == "markdown":
        lesson_content = content or "# Новый урок\n\nОписание урока..."
    elif lesson_type in ["ipynb_file", "pdf_file"]:
        lesson_content = content # Здесь будет лежать имя файла (unique_filename)
    elif lesson_type == "jupyter":
        lesson_content = json.dumps({"task": content or "Выполни задание в Jupyter"})
    elif lesson_type == "ai_trainer":
        lesson_content = json.dumps({
            "topic": content or "Python основы",
            "steps_count": 3
        })
    else:
        lesson_content = content
    
    lesson = Lesson(
        module_id=module_id,
        title=title,
        type=lesson_type,
        content=lesson_content,
        order=max_order + 1
    )
    db.add(lesson)
    db.commit()
    lesson_id = lesson.id
    db.close()
    
    return jsonify({"ok": True, "lesson_id": lesson_id})

# ================= EDIT & DELETE =================

@app.route("/edit-course/<int:course_id>")
@login_required
def edit_course(course_id):
    db = SessionLocal()
    c = db.query(Course).options(
        joinedload(Course.modules).joinedload(Module.lessons)
    ).filter(Course.id == course_id).first()
    
    if not c:
        db.close()
        abort(404)
    
    # Только автор может редактировать
    if c.author_id != session["user_id"]:
        db.close()
        return redirect(url_for("profile"))
    
    course_data = {
        "id": c.id,
        "title": c.title,
        "description": c.description or "",
        "modules": [{
            "id": m.id,
            "title": m.title,
            "order": m.order,
            "lessons": [{
                "id": l.id,
                "title": l.title,
                "type": l.type,
                "order": l.order
            } for l in sorted(m.lessons, key=lambda x: x.order)]
        } for m in sorted(c.modules, key=lambda x: x.order)]
    }
    db.close()
    return render_template("edit_course.html", user=current_user(), course=course_data)


@app.route("/api/course/<int:course_id>", methods=["PUT"])
@login_required
def update_course(course_id):
    db = SessionLocal()
    c = db.query(Course).filter(Course.id == course_id).first()
    if not c or c.author_id != session["user_id"]:
        db.close()
        return jsonify({"error": "Нет доступа"}), 403
    
    data = request.json
    c.title = data.get("title", c.title).strip()
    c.description = data.get("description", c.description or "").strip()
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/course/<int:course_id>", methods=["DELETE"])
@login_required
def delete_course(course_id):
    db = SessionLocal()
    c = db.query(Course).filter(Course.id == course_id).first()
    if not c or c.author_id != session["user_id"]:
        db.close()
        return jsonify({"error": "Нет доступа"}), 403
    
    db.delete(c)
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/module/<int:module_id>", methods=["DELETE"])
@login_required
def delete_module(module_id):
    db = SessionLocal()
    m = db.query(Module).filter(Module.id == module_id).first()
    if not m or m.course.author_id != session["user_id"]:
        db.close()
        return jsonify({"error": "Нет доступа"}), 403
    
    db.delete(m)
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/lesson/<int:lesson_id>", methods=["DELETE"])
@login_required
def delete_lesson(lesson_id):
    db = SessionLocal()
    l = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not l or l.module.course.author_id != session["user_id"]:
        db.close()
        return jsonify({"error": "Нет доступа"}), 403
    
    db.delete(l)
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/complete-lesson", methods=["POST"])
@login_required
def complete_lesson():
    data = request.json
    lesson_id = data.get("lesson_id")
    
    # Настройка: сколько ELO даем за прохождение одного AI-урока
    ELO_REWARD = 25 
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == session["user_id"]).first()
    
    if user:
        user.elo_rating += ELO_REWARD
        db.commit()
        new_elo = user.elo_rating
    else:
        new_elo = 0
        
    db.close()
    return jsonify({"ok": True, "new_elo": new_elo})
@app.route("/api/upload-lesson-file", methods=["POST"])
@login_required
def upload_lesson_file():
    if 'file' not in request.files:
        return jsonify({"error": "Нет файла в запросе"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Файл не выбран"}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Добавляем timestamp, чтобы файлы с одинаковыми именами не перезаписывались
        name, ext = os.path.splitext(filename)
        unique_filename = f"{name}_{int(time.time())}{ext}"
        
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(filepath)
        
        return jsonify({"ok": True, "filename": unique_filename})
    
    return jsonify({"error": "Недопустимый формат. Разрешены только .pdf и .ipynb"}), 400

@app.route("/api/lesson/<int:lesson_id>", methods=["PUT"])
@login_required
def update_lesson(lesson_id):
    db = SessionLocal()
    l = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    
    if not l or l.module.course.author_id != session["user_id"]:
        db.close()
        return jsonify({"error": "Нет доступа"}), 403
    
    data = request.json
    l.title = data.get("title", l.title).strip()
    l.type = data.get("type", l.type)
    
    # Обновляем контент в зависимости от типа
    content = data.get("content", "")
    
    if l.type == "markdown":
        l.content = content
    elif l.type == "jupyter":
        l.content = json.dumps({"task": content})
    elif l.type == "ai_trainer":
        l.content = json.dumps({
            "topic": content,
            "steps_count": 3
        })
    elif l.type in ["pdf_file", "ipynb_file"]:
        l.content = content  # Здесь хранится имя файла
    
    db.commit()
    db.close()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
