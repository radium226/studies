from typing import (
    Callable, 
    Coroutine, 
    Any,
)

from dataclasses import dataclass



@dataclass
class Execution():
    
    wait_for: Callable[[], Coroutine[Any, Any, int]]
    send_signal: Callable[[int], Coroutine[Any, Any, None]]  



