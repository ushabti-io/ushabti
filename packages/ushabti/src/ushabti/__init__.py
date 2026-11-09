"""Ushabti SDK: the shared vocabulary and building blocks of the workforce."""

from importlib.metadata import version

from ushabti.config import LogFormat, Mode, Settings, configure_logging
from ushabti.errors import (
    MissingExtraError,
    ModelError,
    SpellError,
    UnknownImplementError,
    UnresolvedEnvError,
    UnweighedWriteError,
    UshabtiError,
)
from ushabti.field import Field, FieldKind
from ushabti.here_am_i import HereAmI
from ushabti.implements import (
    Implement,
    ImplementKind,
    ImplementRegistry,
    ImplementSpec,
    default_registry,
)
from ushabti.maat import (
    Comparison,
    Condition,
    Isfet,
    Maat,
    MaatAssertion,
    MaatOutcome,
    MaatStatus,
    Observe,
)
from ushabti.models import (
    Completion,
    Message,
    ModelProvider,
    Role,
    StopReason,
    ToolCall,
    Usage,
    build_provider,
)
from ushabti.omen import Omen
from ushabti.papyrus import JsonlPapyrus, Papyrus, PapyrusEntry, PapyrusEvent
from ushabti.spell import (
    ModelProviderName,
    ModelSpec,
    PapyrusSpec,
    ResolvedSpell,
    Spell,
    SpellMetadata,
    SpellSpec,
    VigilSpec,
    load_spell,
    parse_spell,
    resolve_spell,
)
from ushabti.summons import Summons, SummonsSource, SummonsState
from ushabti.weighing import (
    DefaultWeighing,
    Verdict,
    Weighing,
    WeighingPolicy,
    WeighingRequest,
    WeighingResult,
    WeighingRule,
)

__version__ = version("ushabti")

__all__ = [
    "Comparison",
    "Completion",
    "Condition",
    "DefaultWeighing",
    "Field",
    "FieldKind",
    "HereAmI",
    "Implement",
    "ImplementKind",
    "ImplementRegistry",
    "ImplementSpec",
    "Isfet",
    "JsonlPapyrus",
    "LogFormat",
    "Maat",
    "MaatAssertion",
    "MaatOutcome",
    "MaatStatus",
    "Message",
    "MissingExtraError",
    "Mode",
    "ModelError",
    "ModelProvider",
    "ModelProviderName",
    "ModelSpec",
    "Observe",
    "Omen",
    "Papyrus",
    "PapyrusEntry",
    "PapyrusEvent",
    "PapyrusSpec",
    "ResolvedSpell",
    "Role",
    "Settings",
    "Spell",
    "SpellError",
    "SpellMetadata",
    "SpellSpec",
    "StopReason",
    "Summons",
    "SummonsSource",
    "SummonsState",
    "ToolCall",
    "UnknownImplementError",
    "UnresolvedEnvError",
    "UnweighedWriteError",
    "Usage",
    "UshabtiError",
    "Verdict",
    "VigilSpec",
    "Weighing",
    "WeighingPolicy",
    "WeighingRequest",
    "WeighingResult",
    "WeighingRule",
    "__version__",
    "build_provider",
    "configure_logging",
    "default_registry",
    "load_spell",
    "parse_spell",
    "resolve_spell",
]
