"""Deterministic generation plans."""

import json
from dataclasses import asdict, dataclass

from infra_builder.models import InfraConfig

MANIFEST_NAME = ".infra-builder-manifest.json"
STATE_NAME = ".infra-builder-state.json"


@dataclass(frozen=True)
class PlannedAction:
    """A reviewable action in a generation plan."""

    id: str
    kind: str
    description: str
    risk: str
    resource_key: str
    depends_on: tuple[str, ...] = ()
    reversible: bool = True


def planned_files(config: InfraConfig) -> tuple[str, ...]:
    """Return all files generated for a configuration in stable order."""
    files: list[str] = []
    if config.runtime.type == "docker_compose":
        files.append("compose.yaml")
        if config.services.postgres.enabled:
            files.append(".env.example")
        if config.observability.metrics:
            files.append("monitoring/prometheus.yml")
    else:
        files.append(f"systemd/{config.application.name}.service")
    files.append(MANIFEST_NAME)
    return tuple(files)


def build_plan(config: InfraConfig) -> tuple[PlannedAction, ...]:
    """Create a stable plan without accessing or changing the filesystem."""
    output_dir = config.project.output_dir
    if config.runtime.type == "docker_compose":
        services = [config.application.name]
        if config.services.postgres.enabled:
            services.append("postgres")
        if config.services.redis.enabled:
            services.append("redis")
        if config.observability.metrics:
            services.append("prometheus")
        file_descriptions = {
            "compose.yaml": (
                "Generate compose.yaml for services: " + ", ".join(services)
            ),
            ".env.example": "Generate a placeholder for the PostgreSQL password "
            "environment variable",
            "monitoring/prometheus.yml": (
                "Generate Prometheus scrape configuration for the application"
            ),
        }
    else:
        file_descriptions = {
            f"systemd/{config.application.name}.service": (
                "Generate a review-only systemd unit template"
            )
        }

    actions = [
        PlannedAction(
            id="create-output-directory",
            kind="generate_directory",
            description=f"Create output directory {output_dir}",
            risk="local_change",
            resource_key=output_dir,
            reversible=False,
        )
    ]
    generated_file_actions: list[str] = []
    for index, file_path in enumerate(planned_files(config), start=1):
        kind = "record_manifest" if file_path == MANIFEST_NAME else "generate_file"
        action_id = f"generate-{index:02d}"
        dependencies = (
            tuple(generated_file_actions)
            if file_path == MANIFEST_NAME
            else ("create-output-directory",)
        )
        actions.append(
            PlannedAction(
                id=action_id,
                kind=kind,
                description=(
                    f"{file_descriptions[file_path]} at {output_dir}/{file_path}"
                    if file_path in file_descriptions
                    else f"Write {output_dir}/{file_path}"
                ),
                risk="local_change",
                resource_key=f"{output_dir}/{file_path}",
                depends_on=dependencies,
            )
        )
        if file_path != MANIFEST_NAME:
            generated_file_actions.append(action_id)
    return tuple(actions)


def plan_as_dict(config: InfraConfig) -> dict[str, object]:
    """Return the stable machine-readable plan representation."""
    return {
        "schema_version": 1,
        "project": config.project.name,
        "output_dir": config.project.output_dir,
        "actions": [asdict(action) for action in build_plan(config)],
    }


def format_plan_text(config: InfraConfig) -> str:
    """Render a stable human-readable plan."""
    lines = [
        f"Project: {config.project.name}",
        f"Runtime: {config.runtime.type}",
        f"Output: {config.project.output_dir}",
        "No changes have been made.",
        "Planned actions:",
    ]
    for action in build_plan(config):
        lines.append(f"  [{action.risk}] {action.description}")
    return "\n".join(lines)


def format_plan_json(config: InfraConfig) -> str:
    """Render the plan as stable machine-readable JSON."""
    return json.dumps(plan_as_dict(config), indent=2, sort_keys=True)
