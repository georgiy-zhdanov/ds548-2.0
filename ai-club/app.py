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

GROQ_API_KEY = "insert api here"
groq_client = Groq(api_key=GROQ_API_KEY)

Base.metadata.create_all(bind=engine)

app = Flask(__name__)
app.secret_key = "change_me_to_random_string_in_production"


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
    return render_template("profile.html", user=current_user())


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
    lesson_context = data.get("context", "Python основы")
    user_message = data.get("message", "")
    history = data.get("history", [])

    # Проверяем ключ на лету
    if not GROQ_API_KEY or GROQ_API_KEY == "gsk_твой_ключ_здесь":
        return jsonify({
            "feedback": "⚠ В app.py не указан GROQ_API_KEY. Открой файл и вставь ключ с console.groq.com",
            "is_correct": False,
            "next_step": "Сначала настрой API-ключ.",
            "lesson_completed": False
        }), 400

    system_prompt = f"""Ты — AI-тренер по программированию в НЕЙРОКЛУБЕ. 
Тема урока: {lesson_context}.
Отвечай СТРОГО в формате JSON:
{{
  "feedback": "Краткий разбор",
  "is_correct": true или false,
  "next_step": "Следующий шаг или задача",
  "lesson_completed": true или false
}}"""

    messages = [{"role": "system", "content": system_prompt}] + history
    messages.append({"role": "user", "content": user_message})

    try:
        chat_completion = groq_client.chat.completions.create(
            messages=messages,
            # ⚠️ ИСПОЛЬЗУЕМ ТОЧНО РАБОЧУЮ МОДЕЛЬ:
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"},
            temperature=0.3
        )
        
        ai_text = chat_completion.choices[0].message.content
        clean_text = re.sub(r'^```json\s*|\s*```$', '', ai_text.strip(), flags=re.MULTILINE)
        parsed_response = json.loads(clean_text)
        return jsonify(parsed_response)

    except Exception as e:
        # ВАЖНО: выводим полную ошибку в консоль Flask
        print(f"\n{'='*50}")
        print(f"GROQ ERROR TYPE: {type(e).__name__}")
        print(f"GROQ ERROR MSG:  {e}")
        print(f"API KEY START:   {GROQ_API_KEY[:10]}...")
        print(f"{'='*50}\n")
        
        return jsonify({
            "feedback": f"⚠ Ошибка Groq: {str(e)[:150]}",
            "is_correct": False,
            "next_step": "Проверь консоль Flask для деталей.",
            "lesson_completed": False
        }), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)