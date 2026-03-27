"""Configuration for Adam."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class DatabaseSettings(BaseSettings):
    # Default: SQLite in .adam/ directory. Override with ADAM_DB__URL for Postgres.
    url: str = ""  # Empty = auto-detect (SQLite in project dir)
    echo: bool = False

    def get_url(self, project_dir: str = ".") -> str:
        """Resolve the database URL, defaulting to SQLite in .adam/."""
        if self.url:
            return self.url
        import os
        db_path = os.path.join(project_dir, ".adam", "adam.db")
        return f"sqlite+aiosqlite:///{db_path}"


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/1"


class LLMSettings(BaseSettings):
    anthropic_api_key: str = ""
    opus_model: str = "claude-opus-4-6"
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"

    max_concurrent_opus: int = 2
    max_concurrent_sonnet: int = 5
    max_concurrent_haiku: int = 10

    max_response_tokens: int = 64000  # Per-call output ceiling (API max)

    opus_token_budget: int = 0  # 0 = unlimited
    sonnet_token_budget: int = 0
    haiku_token_budget: int = 0


class OrchestratorSettings(BaseSettings):
    max_repair_rounds: int = 5
    acceptance_threshold: float = 0.6
    min_improvement_delta: float = 0.02
    hard_pass_required: bool = True
    run_soft_critics: bool = True
    visual_inspection: bool = True


class ExecutionSettings(BaseSettings):
    """Settings for shell execution (tests, builds, linters)."""
    default_timeout: int = 120  # seconds
    max_timeout: int = 600
    working_dir: str = "."
    shell: str = "/bin/bash"


class Settings(BaseSettings):
    project_name: str = "adam"
    debug: bool = False

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
