import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Retrieve database connection string from environment
# If not specified (e.g. running locally without docker), fallback to a lightweight local SQLite database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./store_intelligence.db")

# SQLite specific argument required for thread sharing
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# Create SQLAlchemy Database Engine
engine = create_engine(
    DATABASE_URL, 
    connect_args=connect_args,
    pool_pre_ping=True  # Automatically check and reconnect broken DB connections
)

# Session factory for generating database transaction contexts
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative Base for database schemas definition
Base = declarative_base()

def get_db():
    """
    FastAPI dependency that provides a transactional database session.
    Automatically closes the session once the request is complete.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
