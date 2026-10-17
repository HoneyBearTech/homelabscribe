"""Collectors read one source (a Docker host, a Proxmox cluster) and return normalized models.

To add a source, write a class with a `collect() -> tuple[list[Host], list[Service]]` method.
Nothing downstream needs to change.
"""

from typing import Protocol

from homelabscribe.models import Host, Service


class Collector(Protocol):
    def collect(self) -> tuple[list[Host], list[Service]]: ...
