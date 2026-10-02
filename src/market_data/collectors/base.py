from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Partition:
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}:{self.end.isoformat()}"


class Collector(Protocol):
    def plan(self, start: date, end: date) -> list[Partition]: ...
    def fetch(self, partition: Partition): ...
    def parse(self, raw_bytes: bytes): ...
    def normalize(self, records, retrieval_id: str, observed_at: str): ...
