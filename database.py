from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Using Postgres under the hood
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://kriti:kriti_pwd@localhost:5432/routeopti"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get DB session per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()