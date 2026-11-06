"""HTTP request models. Business invariants remain in core/workflow."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestBody(BaseModel):
    model_config = ConfigDict(strict=True)


class NewMeeting(RequestBody):
    title: str = Field(min_length=1, max_length=200)
    date: str | None = None
    transcript: str = ''
    source: dict | None = None


class MeetingRequest(RequestBody):
    meeting_id: str


class ActionValues(RequestBody):
    title: str = Field(default='', max_length=5000)
    topic: str = Field(default='', max_length=5000)
    owner: str = Field(default='', max_length=5000)
    due_date: str = Field(default='', max_length=5000)
    acceptance_criteria: str = Field(default='', max_length=5000)


class ReviewRequest(MeetingRequest):
    action_id: str
    values: ActionValues = Field(default_factory=ActionValues)
    delete: bool = False
    confirmed: bool = False
    selected: bool = False


class SyncRequest(MeetingRequest):
    action_ids: list[str]
    project_id: str


class ResolveUncertainRequest(MeetingRequest):
    action_id: str
    resolution: Literal['created', 'not_created']


class TencentQuery(RequestBody):
    days: Literal[1, 3, 7] = 3


class TencentImport(RequestBody):
    choice_id: str


class ToolCall(RequestBody):
    name: str
    arguments: dict = Field(default_factory=dict)
