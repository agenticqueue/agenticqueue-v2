from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Actor(Base):
    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('human','agent','script','routine')",
            name="actors_kind_check",
        ),
        Index(
            "actors_name_active_uniq",
            "name",
            unique=True,
            postgresql_where=text("deactivated_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_actor_id IS NULL)",
            name="api_keys_revoked_fields_check",
        ),
        Index(
            "api_keys_actor_active_idx",
            "actor_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("api_keys_lookup_id_uniq", "lookup_id", unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    lookup_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
    )


class AuditLogEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("audit_log_ts_idx", text("ts DESC")),
        Index("audit_log_actor_ts_idx", "authenticated_actor_id", text("ts DESC")),
        Index("audit_log_op_ts_idx", "op", text("ts DESC")),
        Index(
            "audit_log_target_idx",
            "target_kind",
            "target_id",
            postgresql_where=text("target_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    op: Mapped[str] = mapped_column(Text, nullable=False)
    authenticated_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    claimed_actor_identity: Mapped[str | None] = mapped_column(Text)
    target_kind: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("slug", name="projects_slug_key"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_by_actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
    )


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        Index(
            "labels_project_name_active_uniq",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_template: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    cloned_from_pipeline_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="RESTRICT"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_by_actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            (
                "state IN "
                "('draft','ready','in_progress','done','failed',"
                "'blocked','pending_review','cancelled')"
            ),
            name="jobs_state_check",
        ),
        Index(
            "idx_jobs_labels_gin",
            "labels",
            postgresql_using="gin",
        ),
        Index(
            "idx_jobs_state_project_created",
            "state",
            "project_id",
            "created_at",
            postgresql_where=text("state = 'ready'"),
        ),
        Index(
            "idx_jobs_in_progress_heartbeat",
            "claim_heartbeat_at",
            "id",
            postgresql_where=text("state = 'in_progress'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    pipeline_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    contract: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    claimed_by_actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_by_actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
    )


class JobEdge(Base):
    __tablename__ = "job_edges"
    __table_args__ = (
        CheckConstraint(
            "edge_type IN ('gated_on','parent_of','sequence_next')",
            name="job_edges_edge_type_check",
        ),
        PrimaryKeyConstraint(
            "from_job_id",
            "to_job_id",
            "edge_type",
            name="job_edges_pkey",
        ),
    )

    from_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_type: Mapped[str] = mapped_column(Text, nullable=False)


class JobComment(Base):
    __tablename__ = "job_comments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "attached_to_kind IN ('job','pipeline','project')",
            name="decisions_attached_to_kind_check",
        ),
        Index(
            "idx_decisions_attached",
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        Index("idx_decisions_actor", "created_by_actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    attached_to_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attached_to_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    supersedes_decision_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="RESTRICT"),
    )
    created_by_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Learning(Base):
    __tablename__ = "learnings"
    __table_args__ = (
        CheckConstraint(
            "attached_to_kind IN ('job','pipeline','project')",
            name="learnings_attached_to_kind_check",
        ),
        Index(
            "idx_learnings_attached",
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        Index("idx_learnings_actor", "created_by_actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    attached_to_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attached_to_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Objective(Base):
    __tablename__ = "objectives"
    __table_args__ = (
        CheckConstraint(
            "attached_to_kind IN ('project','pipeline')",
            name="objectives_attached_to_kind_check",
        ),
        Index(
            "idx_objectives_attached",
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        Index("idx_objectives_actor", "created_by_actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    attached_to_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attached_to_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str | None] = mapped_column(Text)
    target_value: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Visual(Base):
    __tablename__ = "visuals"
    __table_args__ = (
        CheckConstraint(
            "attached_to_kind IN ('project','pipeline','job','decision','learning')",
            name="visuals_attached_to_kind_check",
        ),
        CheckConstraint(
            "type IN ('mermaid','graphviz','plantuml','vega-lite','ascii')",
            name="visuals_type_check",
        ),
        Index(
            "idx_visuals_attached",
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        Index("idx_visuals_actor", "created_by_actor_id", "created_at"),
        Index("idx_visuals_type", "type"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    attached_to_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attached_to_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Component(Base):
    __tablename__ = "components"
    __table_args__ = (
        CheckConstraint(
            "attached_to_kind IN ('project','pipeline')",
            name="components_attached_to_kind_check",
        ),
        Index(
            "idx_components_attached",
            "attached_to_kind",
            "attached_to_id",
            "created_at",
        ),
        Index("idx_components_actor", "created_by_actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    attached_to_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attached_to_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    access_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("actors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
