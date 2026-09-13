"""Central configuration, loaded from environment / .env.

Every tunable lives here so you can dial cost, concurrency, and model
choice without touching agent code.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM: Groq
    groq_api_key: str = ""

    # Search
    tavily_api_key: str = ""

    @field_validator("groq_api_key", "tavily_api_key", mode="after")
    @classmethod
    def _strip_secret(cls, v: str) -> str:
        # Defend against a stray newline/space when a key is pasted into a
        # dashboard env var — otherwise it becomes an illegal HTTP header.
        return v.strip() if isinstance(v, str) else v

    # Model tiering (all free on Groq). Note: Groq's catalog changes over
    # time — run `GET /openai/v1/models` to see what your key can access,
    # then set these in .env. Small model for high-volume mechanical
    # stages, larger model for synthesis quality.
    manager_model: str = "openai/gpt-oss-20b"
    researcher_model: str = "openai/gpt-oss-20b"
    synthesizer_model: str = "openai/gpt-oss-120b"
    critic_model: str = "openai/gpt-oss-20b"

    # Pipeline guardrails. Tuned for richer reports: more sub-questions,
    # more sources each, and more content per source feeding the LLMs.
    max_sub_questions: int = 5
    max_results_per_search: int = 6
    max_content_chars: int = 3500
    max_concurrency: int = 3
    enable_critic: bool = False
    max_critic_revisions: int = 1

    # LLM call reliability
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # Persistence
    sqlite_path: str = "data/runs.db"


settings = Settings()
