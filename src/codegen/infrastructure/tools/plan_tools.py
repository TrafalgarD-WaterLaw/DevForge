"""Planning tools — zero-execution tools for task tracking.

``todo_write`` carries no execution power — it cannot read or write
files, run code, or modify the project.  Its sole purpose is to let
the agent externalize its plan into the conversation and the blackboard.
"""

from codegen.infrastructure.tools.registry import register, runtime

@register(
    name="todo_write",
    description=(
        "Update your task list. Call this whenever you start, complete, "
        "or add tasks. Each entry has 'content' (description) and "
        "'status' (pending / in_progress / completed). The list is stored "
        "on the blackboard and surfaced to the frontend via todo_update "
        "events — it does NOT control the agent loop."
    ),
    parameters={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Task description"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Current status",
                        },
                    },
                    "additionalProperties": False,
                    "required": ["content", "status"],
                },
                "description": "Full task list with updated statuses",
            },
        },
        "additionalProperties": False,
        "required": ["todos"],
    },
)
def todo_write(todos: list[dict]):
    """Store the plan on the blackboard and fire a todo_update event.

    The stored list (``bb["_todos"]``) is display-only; completing all
    tasks does NOT exit the agent loop.
    """
    rt = runtime()
    bb = rt.blackboard
    if bb is None:
        return "Error: no blackboard available"

    bb["_todos"] = todos
    done = sum(1 for t in todos if t.get("status") == "completed")
    total = len(todos)

    from core.events import Events, HookRegistry
    HookRegistry.trigger(Events.TODO_UPDATE, agent=rt.current_agent,
                         todos=todos, done=done, total=total)

    return f"{done}/{total} tasks done"
