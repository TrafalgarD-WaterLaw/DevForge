"""Phase registry — class-based storage + standalone decorator."""

class PhaseRegistry:
    """Name → Phase class mapping.

        PhaseRegistry.register("DemandAnalysis", DemandAnalysis)
        PhaseRegistry.get("DemandAnalysis")  → DemandAnalysis class
    """
    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, phase_cls: type):
        cls._registry[name] = phase_cls

    @classmethod
    def get(cls, name: str) -> type:
        return cls._registry[name]

def register_phase(_cls=None):
    """Decorator: register a Phase subclass by name.

        @register_phase
        class DemandAnalysis(Phase): ...
    """
    def wrap(cls):
        PhaseRegistry.register(cls.__name__, cls)
        return cls

    if _cls is None:
        return wrap
    return wrap(_cls)
