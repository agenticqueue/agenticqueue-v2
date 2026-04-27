from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from aq_api.models.auth import AQModel, coerce_utc_datetime
from aq_api.models.jobs import PageLimit
from aq_api.models.projects import Cursor

CommentBody = Annotated[
    str,
    Field(min_length=1, max_length=16384, pattern=r"^[^\x00]*$", repr=False),
]


class JobComment(AQModel):
    id: UUID
    job_id: UUID
    author_actor_id: UUID
    body: CommentBody
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_must_be_utc(cls, value: object) -> datetime:
        return coerce_utc_datetime(value)


class CommentOnJobRequest(AQModel):
    body: CommentBody


class CommentOnJobResponse(AQModel):
    comment: JobComment


class ListJobCommentsRequest(AQModel):
    limit: PageLimit = 50
    cursor: Cursor = None


class ListJobCommentsResponse(AQModel):
    comments: list[JobComment]
    next_cursor: Cursor = None
