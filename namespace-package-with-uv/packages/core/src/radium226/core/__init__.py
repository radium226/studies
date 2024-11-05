from importlib.metadata import entry_points
from importlib import import_module

def say_messages(name: str):
    print("Saying messages: ")
    for entry_point in entry_points(group="radium226.messages"):
        reference = entry_point.value
        [module_name, function_name] = reference.split(":")
        module = import_module(module_name)
        function = getattr(module, function_name)
        message = function(name)
        print(f" - {message}")


__all__ = [
    "say_messages",
]