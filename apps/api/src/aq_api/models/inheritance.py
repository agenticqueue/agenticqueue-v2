from pydantic import Field

from aq_api.models.auth import AQModel


class InheritanceReferenceLists(AQModel):
    direct: list[dict[str, object]] = Field(default_factory=list)
    inherited: list[dict[str, object]] = Field(default_factory=list)
