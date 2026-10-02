"""Release 4 source-verification gate.

A candidate must pass every gate before a collector, schema, or schedule is added.
Keeping these decisions in code prevents an operator from accidentally enabling an
unverified dashboard scraper.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceVerification:
    source: str
    status: str
    machine_readable_endpoint: bool
    authentication_verified: bool
    schema_verified: bool
    pagination_verified: bool
    limits_verified: bool
    semantics_verified: bool
    history_verified: bool
    republication_verified: bool
    reason: str
    documentation_url: str

    @property
    def eligible(self) -> bool:
        return self.status == "verified" and all(
            (
                self.machine_readable_endpoint,
                self.authentication_verified,
                self.schema_verified,
                self.pagination_verified,
                self.limits_verified,
                self.semantics_verified,
                self.history_verified,
                self.republication_verified,
            )
        )

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["eligible"] = self.eligible
        return result


# Reviewed 2026-08-31. See docs/release-4-source-verification.md.
CANDIDATES = {
    "computeprices": SourceVerification(
        source="computeprices",
        status="blocked_rights",
        machine_readable_endpoint=True,
        authentication_verified=True,
        schema_verified=True,
        pagination_verified=True,
        limits_verified=True,
        semantics_verified=True,
        history_verified=True,
        republication_verified=False,
        reason="Archival and republication rights required by this service are not established.",
        documentation_url="https://computeprices.com/docs/api",
    ),
    "gpu_ai": SourceVerification(
        source="gpu_ai",
        status="unverified_interface",
        machine_readable_endpoint=False,
        authentication_verified=False,
        schema_verified=False,
        pagination_verified=False,
        limits_verified=False,
        semantics_verified=False,
        history_verified=False,
        republication_verified=False,
        reason="No authorized stable machine-readable pricing index interface was verified.",
        documentation_url="https://gpu.ai/docs",
    ),
}


def eligible_sources() -> frozenset[str]:
    return frozenset(name for name, result in CANDIDATES.items() if result.eligible)
