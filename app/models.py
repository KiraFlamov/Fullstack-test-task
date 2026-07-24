from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)

    filename = Column(String, unique=True, nullable=False)

    downloaded_at = Column(DateTime, nullable=False)