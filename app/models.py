import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL = "sqlite:///./lifecycle.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class LifecycleStateEnum(str, enum.Enum):
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATED = "VALIDATED"
    RELEASED = "RELEASED"
    OPERATING = "OPERATING"
    RETIRED = "RETIRED"


# Valid one-step forward transitions
VALID_TRANSITIONS: dict[LifecycleStateEnum, LifecycleStateEnum] = {
    LifecycleStateEnum.CREATED: LifecycleStateEnum.IN_PROGRESS,
    LifecycleStateEnum.IN_PROGRESS: LifecycleStateEnum.VALIDATED,
    LifecycleStateEnum.VALIDATED: LifecycleStateEnum.RELEASED,
    LifecycleStateEnum.RELEASED: LifecycleStateEnum.OPERATING,
    LifecycleStateEnum.OPERATING: LifecycleStateEnum.RETIRED,
}


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    lifecycle_state: Mapped["LifecycleState"] = relationship(
        "LifecycleState", back_populates="entity", uselist=False, cascade="all, delete-orphan"
    )
    transition_logs: Mapped[list["TransitionLog"]] = relationship(
        "TransitionLog", back_populates="entity", cascade="all, delete-orphan"
    )


class LifecycleState(Base):
    __tablename__ = "lifecycle_states"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String, ForeignKey("entities.id"), nullable=False, unique=True)
    current_state: Mapped[LifecycleStateEnum] = mapped_column(
        Enum(LifecycleStateEnum), nullable=False, default=LifecycleStateEnum.CREATED
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    entity: Mapped["Entity"] = relationship("Entity", back_populates="lifecycle_state")


class TransitionLog(Base):
    __tablename__ = "transition_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String, ForeignKey("entities.id"), nullable=False)
    from_state: Mapped[str] = mapped_column(String, nullable=True)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    entity: Mapped["Entity"] = relationship("Entity", back_populates="transition_logs")


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
