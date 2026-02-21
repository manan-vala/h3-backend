from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# LOCAL: Use SQLite for now. 
# SCALABLE: Change this string to a PostgreSQL URL later (e.g., "postgresql://user:pass@localhost/db")
SQLALCHEMY_DATABASE_URL = "sqlite:///./users.db"

# connect_args is needed only for SQLite
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get DB session per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()