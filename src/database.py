from functools import lru_cache

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.settings import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class RawSourceBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class KnowledgeBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_database_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def create_session_maker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@lru_cache
def get_raw_source_engine() -> Engine:
    return create_database_engine(get_settings().raw_source_database_url)


@lru_cache
def get_knowledge_base_engine() -> Engine:
    return create_database_engine(get_settings().knowledge_base_database_url)


@lru_cache
def get_raw_source_session_maker() -> sessionmaker[Session]:
    return create_session_maker(get_raw_source_engine())


@lru_cache
def get_knowledge_base_session_maker() -> sessionmaker[Session]:
    return create_session_maker(get_knowledge_base_engine())
