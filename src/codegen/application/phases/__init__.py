"""Built-in DevForge pipeline phases."""
from codegen.application.phases.coding import Coding  # noqa: F401
from codegen.application.phases.requirements_discussion import RequirementsDiscussion  # noqa: F401
from codegen.application.phases.design import Design  # noqa: F401
from codegen.application.phases.documentation import Documentation  # noqa: F401
from codegen.application.phases.iterate import Iterate  # noqa: F401
from codegen.application.phases.quality_gate import QualityGate  # noqa: F401
from codegen.application.phases.verification import Verification  # noqa: F401
