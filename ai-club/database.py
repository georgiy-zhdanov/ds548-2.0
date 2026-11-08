from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# SQLite — создаст файл club.db в папке проекта
DATABASE_URL = "sqlite:///club.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

def get_db():
    """Генератор сессии БД для Flask"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()