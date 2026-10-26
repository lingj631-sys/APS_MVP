"""Fixed rule kernel (Phase 1 / ACT-20/21).

The kernel is the stable half of "fixed algorithm + configurable rules":

* ``registry``  -- the closed set of 19 operators and their parameter schemas;
* ``types``     -- RuleSet / RuleInstance / Scope / Nature / Group;
* ``scope``     -- site -> line -> part resolution with replace/add inheritance;
* ``compiler``  -- turns a resolved rule into CP-SAT constraints or penalties.

Nothing here imports ``src.scheduler``; a scheduling model supplies a small
``CompileContext`` surface instead.
"""
from src.kernel.compiler import (
    CompiledRules,
    CompileContext,
    PenaltyBook,
    PenaltyTerm,
    compile_rules,
)
from src.kernel.registry import (
    IMPLEMENTED_TEMPLATES,
    TEMPLATES,
    ParamSpec,
    TemplateSpec,
    get_template,
    validate_params,
)
from src.kernel.scope import (
    resolve,
    resolve_for_targets,
    resolve_for_tasks,
    targets_from_tasks,
    validate_rule_set,
)
from src.kernel.solve import (
    GroupResult,
    LexicographicResult,
    solve_lexicographic,
)
from src.kernel.types import (
    GroupMode,
    Nature,
    RuleGroup,
    RuleInstance,
    RuleSet,
    Scope,
    Target,
)

__all__ = [
    "CompiledRules",
    "CompileContext",
    "PenaltyBook",
    "PenaltyTerm",
    "compile_rules",
    "IMPLEMENTED_TEMPLATES",
    "TEMPLATES",
    "ParamSpec",
    "TemplateSpec",
    "get_template",
    "validate_params",
    "resolve",
    "resolve_for_targets",
    "resolve_for_tasks",
    "targets_from_tasks",
    "validate_rule_set",
    "GroupResult",
    "LexicographicResult",
    "solve_lexicographic",
    "GroupMode",
    "Nature",
    "RuleGroup",
    "RuleInstance",
    "RuleSet",
    "Scope",
    "Target",
]
