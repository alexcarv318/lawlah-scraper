from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from src.database import RawSourceBase
from src.raw.models import cases, legislation  # noqa: F401
from src.settings import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().raw_source_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = RawSourceBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
