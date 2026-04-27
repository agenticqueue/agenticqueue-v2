from typing import Annotated
from uuid import UUID

from pydantic import Field

from aq_api.models.auth import AQModel

ProfileName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]{0,127}$"),
]
JsonObject = dict[str, object]


class ContractProfile(AQModel):
    id: UUID
    name: ProfileName
    version: int = Field(ge=1)
    description: str | None = Field(default=None, max_length=16384)
    required_dod_ids: list[str] = Field(default_factory=list)
    schema_: JsonObject = Field(default_factory=dict, alias="schema")


class ListContractProfilesResponse(AQModel):
    profiles: list[ContractProfile]


class DescribeContractProfileResponse(AQModel):
    profile: ContractProfile
