"""Service registry (harness/runtime/services.yaml) shared by supervisor, mesh and loadgen."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from harness.runtime.util import port

REGISTRY_PATH = Path(__file__).resolve().parent / "services.yaml"
APP_BASE = 8100
MESH_BASE = 8200
GROUPS = ("commerce", "ops", "all")


@dataclass(frozen=True)
class Service:
    index: int
    name: str
    repo: str
    stage: int
    module: str | None = None  # commerce: python -m services.<module>
    path: str | None = None  # ops: node entrypoint relative to the repo root
    health: str = "/healthz"
    edge: bool = False

    @property
    def group(self) -> str:
        return self.repo

    @property
    def env_var(self) -> str:
        return url_env_name(self.name)

    def app_port(self, offset: int = 0) -> int:
        return APP_BASE + self.index + offset

    def mesh_port(self, offset: int = 0) -> int:
        return MESH_BASE + self.index + offset

    def caller_port(self, offset: int = 0) -> int:
        """Port callers use: the edge for edge-api, otherwise the service's mesh listener."""
        return port("edge", offset) if self.edge else self.mesh_port(offset)


@dataclass(frozen=True)
class Sim:
    name: str
    module: str
    port: str
    admin_port: str
    url_env: str
    url_path: str = ""


@dataclass(frozen=True)
class Registry:
    services: tuple[Service, ...]
    sims: tuple[Sim, ...]
    repos: dict
    repo_order: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.services)

    def get(self, name: str) -> Service:
        for s in self.services:
            if s.name == name:
                return s
        raise KeyError(name)

    def by_repo(self, repo: str) -> list[Service]:
        return [s for s in self.services if s.repo == repo]

    def resolve(self, target: str) -> list[Service]:
        """Service name or group (commerce|ops|all) -> services in start order."""
        if target == "all":
            sel = list(self.services)
        elif target in self.repos:
            sel = self.by_repo(target)
        else:
            sel = [self.get(target)]
        return self.start_order(sel)

    def start_order(self, sel: list[Service] | None = None) -> list[Service]:
        sel = list(self.services) if sel is None else sel
        rank = {r: i for i, r in enumerate(self.repo_order)}
        return sorted(sel, key=lambda s: (rank.get(s.repo, 99), s.stage, s.index))

    def stages(self, sel: list[Service] | None = None) -> list[list[Service]]:
        """Group services (in start order) into consecutive stages that can start together."""
        out: list[list[Service]] = []
        key = None
        for s in self.start_order(sel):
            k = (s.repo, s.stage)
            if k != key:
                out.append([])
                key = k
            out[-1].append(s)
        return out

    def by_mesh_port(self, offset: int = 0) -> dict[int, Service]:
        return {s.mesh_port(offset): s for s in self.services}

    def url_env(self, offset: int = 0) -> dict[str, str]:
        """Discovery env shared by every service: <NAME>_URL for all services + sim URLs."""
        env = {s.env_var: f"http://127.0.0.1:{s.caller_port(offset)}" for s in self.services}
        for sim in self.sims:
            env[sim.url_env] = f"http://127.0.0.1:{port(sim.port, offset)}{sim.url_path}"
        return env


def url_env_name(name: str) -> str:
    return name.upper().replace("-", "_") + "_URL"


def load(path: str | Path = REGISTRY_PATH) -> Registry:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    health = doc.get("health", "/healthz")
    services = tuple(
        Service(
            index=int(s["index"]),
            name=s["name"],
            repo=s["repo"],
            stage=int(s.get("stage", 1)),
            module=s.get("module"),
            path=s.get("path"),
            health=s.get("health", health),
            edge=bool(s.get("edge", False)),
        )
        for s in doc["services"]
    )
    names = [s.name for s in services]
    if len(set(names)) != len(names) or len({s.index for s in services}) != len(services):
        raise ValueError("duplicate service name or index in registry")
    if set(names) & set(GROUPS):
        raise ValueError("service names must not collide with group names")
    sims = tuple(Sim(**s) for s in doc.get("sims", ()))
    repos = dict(doc["repos"])
    order = tuple(doc.get("repo_order") or repos)
    return Registry(services, sims, repos, order)


@lru_cache(maxsize=1)
def default() -> Registry:
    return load()
