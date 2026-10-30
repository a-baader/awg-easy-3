"""Контракты плана и журнала; runtime-проверки остаются у preflight/manager."""
from typing import Dict, List, Literal, Optional, TypedDict


class Component(TypedDict, total=False):
    backend: Literal['amneziawg', 'wireguard']
    table: str
    interface: str
    container: str
    network4: str
    address4: str
    network6: str
    address6: str
    udp_port: int
    panel_port: int
    data_directory: str
    image: str
    image_ref: str
    version: str


class Plan(TypedDict, total=False):
    config_version: int
    version: str
    project: str
    source_commit: str
    language: str
    auto_check: bool
    components: Dict[str, Component]
    maintenance_components: List[str]
    manager_files: Dict[str, str]


class Journal(TypedDict, total=False):
    operation: Literal['install', 'extend', 'restore', 'update', 'source-update', 'uninstall', 'reinstall']
    phase: str
    previous: Optional[Plan]
    candidate: Plan
    selected: List[str]
    snapshot: str
    owner_pid: int
    lock_owner_pid: int
