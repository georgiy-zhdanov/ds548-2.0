from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    avatar = Column(String(50), default="🤖")
    elo_rating = Column(Float, default=1000.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    courses = relationship("Course", back_populates="author")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Course(Base):
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    author_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    author = relationship("User", back_populates="courses")
    modules = relationship("Module", back_populates="course", cascade="all, delete-orphan")


class Module(Base):
    __tablename__ = "modules"
    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    title = Column(String(200), nullable=False)
    order = Column(Integer, default=0)

    course = relationship("Course", back_populates="modules")
    lessons = relationship("Lesson", back_populates="module", cascade="all, delete-orphan")


class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(Integer, primary_key=True)
    module_id = Column(Integer, ForeignKey("modules.id"), nullable=False)
    title = Column(String(200), nullable=False)
    type = Column(String(20), nullable=False)  # 'markdown' | 'jupyter' | 'ai_trainer'
    content = Column(Text)  # markdown текст / JSON с настройками
    order = Column(Integer, default=0)

    module = relationship("Module", back_populates="lessons")


# --- Формула ELO ---
def calculate_elo(rating_a: float, rating_b: float, score_a: float, k: int = 32) -> float:
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    return round(rating_a + k * (score_a - expected_a), 1)