from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError, meta
from pydantic import (
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class PromptConfigError(ValueError):
    pass


class PromptSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    prompt_file: Path = Path("config/deepseek_prompt.yaml")


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    system_prompt: str
    instance_prompt: str
    _source_path: Path | None = PrivateAttr(default=None)

    @field_validator("system_prompt", "instance_prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value

    def render_instance(self, problem_statement: str) -> str:
        try:
            return (
                Environment(
                    undefined=StrictUndefined,
                    autoescape=False,
                    keep_trailing_newline=True,
                )
                .from_string(self.instance_prompt)
                .render(problem_statement=problem_statement)
            )
        except Exception as exc:
            raise PromptConfigError(
                f"Cannot render instance prompt from {self._source_path}: {exc}"
            ) from exc


def load_prompts(path: Path | None = None) -> PromptConfig:
    path = (
        (path if path is not None else PromptSettings().prompt_file)
        .expanduser()
        .resolve()
    )
    try:
        prompts = PromptConfig.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        prompts._source_path = path
        unknown = meta.find_undeclared_variables(
            Environment().parse(prompts.instance_prompt)
        ) - {"problem_statement"}
        if unknown:
            raise TemplateError(
                f"Unknown template variables: {', '.join(sorted(unknown))}"
            )
        prompts.render_instance("")
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValidationError,
        TemplateError,
    ) as exc:
        raise PromptConfigError(f"Cannot load prompts from {path}: {exc}") from exc
    return prompts
