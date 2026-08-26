from typing import TYPE_CHECKING

from .handoff import validate_public_handoff
from .paths import WorkbenchPaths
from .source_artifacts import (
    ActionPlan,
    ApprovalReceipt,
    ArtifactRecord,
    SourceContractError,
    SourceWorkOrder,
)

if TYPE_CHECKING:
    from .handoff_compiler import HandoffPublication
    from .production_policy import ProductionPolicyResult


def __getattr__(name: str):
    if name == "HandoffPublication":
        from .handoff_compiler import HandoffPublication

        return HandoffPublication
    if name == "ProductionPolicyResult":
        from .production_policy import ProductionPolicyResult

        return ProductionPolicyResult
    raise AttributeError(name)

__all__ = [
    "ActionPlan",
    "ApprovalReceipt",
    "ArtifactRecord",
    "HandoffPublication",
    "ProductionPolicyResult",
    "SourceContractError",
    "SourceWorkOrder",
    "WorkbenchPaths",
    "validate_public_handoff",
]
