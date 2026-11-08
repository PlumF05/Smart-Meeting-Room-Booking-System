"""规则问答结构，不接受用户ID、工具指令或预约动作。"""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class RuleQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class RuleSource(BaseModel):
    id: str
    section: str
    title: str
    version: str
    excerpt: str


class RuleAnswer(BaseModel):
    answer: str
    grounded: bool
    sources: list[RuleSource]
    knowledge_version: str


class GeneratedRuleAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    answer: str = Field(min_length=1, max_length=3000)
    supported: bool
    evidence_ids: list[str] = Field(max_length=4)
