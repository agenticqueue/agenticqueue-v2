import secrets

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aq_api.models import SetupResponse
from aq_api.models.db import Actor as DbActor
from aq_api.models.db import ApiKey as DbApiKey
from aq_api.models.db import Job as DbJob
from aq_api.models.db import Pipeline as DbPipeline
from aq_api.models.db import Project as DbProject
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)

SETUP_LOCK_KEY = "aq:setup-singleton"
SETUP_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('aq:setup-singleton'))"
FOUNDER_ACTOR_NAME = "founder"
FOUNDER_KEY_NAME = "founder"
BOOTSTRAP_PROJECT_NAME = "default"
BOOTSTRAP_PROJECT_SLUG = "default"
BOOTSTRAP_PROJECT_DESCRIPTION = "AQ default project for first-run installs."
SHIP_A_THING_TEMPLATE_NAME = "ship-a-thing"
SYSTEM_ACTOR_NAME = "aq-system-sweeper"
SYSTEM_ACTOR_KIND = "script"
READY_STATE = "ready"
BOOTSTRAP_CONTRACTS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "scope",
        {
            "contract_type": "scoping",
            "dod_items": [
                {
                    "id": "scope-statement",
                    "verification_method": "manual_review",
                    "evidence_required": "scope statement document path under plans/",
                    "acceptance_threshold": (
                        "scope names what's in and what's out; reviewed by Ghost"
                    ),
                }
            ],
        },
    ),
    (
        "build",
        {
            "contract_type": "coding-task",
            "dod_items": [
                {
                    "id": "tests-pass",
                    "verification_method": "command",
                    "evidence_required": "pytest output captured to artifacts",
                    "acceptance_threshold": (
                        "all tests pass; mypy --strict clean; ruff check clean"
                    ),
                },
                {
                    "id": "commit-pushed",
                    "verification_method": "command",
                    "evidence_required": "git rev-parse HEAD",
                    "acceptance_threshold": "branch tip pushed to origin",
                },
            ],
        },
    ),
    (
        "verify",
        {
            "contract_type": "verification",
            "dod_items": [
                {
                    "id": "claude-audit-pass",
                    "verification_method": "review",
                    "evidence_required": (
                        "claude per-story audit comment id on the parent ticket"
                    ),
                    "acceptance_threshold": "audit verdict APPROVED",
                }
            ],
        },
    ),
)


class AlreadySetupError(Exception):
    pass


async def acquire_setup_lock(session: AsyncSession) -> None:
    await session.execute(text(SETUP_LOCK_SQL))


def generate_founder_key() -> str:
    return f"aq2_{secrets.token_urlsafe(32)}"


async def _actors_exist(session: AsyncSession) -> bool:
    result = await session.scalar(
        select(
            exists().where(
                DbActor.id.is_not(None),
                ~(
                    (DbActor.name == SYSTEM_ACTOR_NAME)
                    & (DbActor.kind == SYSTEM_ACTOR_KIND)
                ),
            )
        )
    )
    return bool(result)


async def _founder_actor(session: AsyncSession) -> DbActor | None:
    result: DbActor | None = await session.scalar(
        select(DbActor)
        .where(DbActor.name == FOUNDER_ACTOR_NAME)
        .order_by(DbActor.created_at.asc(), DbActor.id.asc())
        .limit(1)
    )
    return result


async def _first_founder_project(
    session: AsyncSession,
    founder_id: object,
) -> DbProject | None:
    result: DbProject | None = await session.scalar(
        select(DbProject)
        .where(DbProject.created_by_actor_id == founder_id)
        .order_by(DbProject.created_at.asc(), DbProject.id.asc())
        .limit(1)
    )
    return result


async def _template_exists(session: AsyncSession, project_id: object) -> bool:
    result = await session.scalar(
        select(
            exists().where(
                DbPipeline.name == SHIP_A_THING_TEMPLATE_NAME,
                DbPipeline.project_id == project_id,
                DbPipeline.is_template.is_(True),
            )
        )
    )
    return bool(result)


async def _create_template_pipeline(
    session: AsyncSession,
    *,
    founder: DbActor,
    project: DbProject,
) -> DbPipeline:
    template = DbPipeline(
        project_id=project.id,
        name=SHIP_A_THING_TEMPLATE_NAME,
        is_template=True,
        created_by_actor_id=founder.id,
    )
    session.add(template)
    await session.flush()

    session.add_all(
        [
            DbJob(
                pipeline_id=template.id,
                project_id=project.id,
                state=READY_STATE,
                title=title,
                contract=contract,
                created_by_actor_id=founder.id,
            )
            for title, contract in BOOTSTRAP_CONTRACTS
        ]
    )
    await session.flush()
    return template


async def _ensure_template_pipeline(
    session: AsyncSession,
    *,
    founder: DbActor,
    project: DbProject,
) -> None:
    if not await _template_exists(session, project.id):
        await _create_template_pipeline(session, founder=founder, project=project)


async def run_setup(session: AsyncSession) -> SetupResponse:
    already_setup = False
    async with session.begin():
        await acquire_setup_lock(session)

        if await _actors_exist(session):
            existing_founder = await _founder_actor(session)
            if existing_founder is not None:
                existing_project = await _first_founder_project(
                    session,
                    existing_founder.id,
                )
                if existing_project is not None:
                    await _ensure_template_pipeline(
                        session,
                        founder=existing_founder,
                        project=existing_project,
                    )
            already_setup = True

        else:
            founder_key = generate_founder_key()
            founder = DbActor(name=FOUNDER_ACTOR_NAME, kind="human")
            session.add(founder)
            await session.flush()

            session.add(
                DbApiKey(
                    actor_id=founder.id,
                    name=FOUNDER_KEY_NAME,
                    key_hash=PASSWORD_HASHER.hash(founder_key),
                    prefix=founder_key[:DISPLAY_PREFIX_LENGTH],
                    lookup_id=lookup_id_for_key(founder_key),
                )
            )
            await session.flush()

            bootstrap_project = DbProject(
                name=BOOTSTRAP_PROJECT_NAME,
                slug=BOOTSTRAP_PROJECT_SLUG,
                description=BOOTSTRAP_PROJECT_DESCRIPTION,
                created_by_actor_id=founder.id,
            )
            session.add(bootstrap_project)
            await session.flush()

            await _create_template_pipeline(
                session,
                founder=founder,
                project=bootstrap_project,
            )

            return SetupResponse(
                actor_id=founder.id,
                founder_key=founder_key,
                bootstrap_project_id=bootstrap_project.id,
            )

    if already_setup:
        raise AlreadySetupError
    raise RuntimeError("setup transaction exited without result")
