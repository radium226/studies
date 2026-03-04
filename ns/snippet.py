import asyncio
import os
import functools
import concurrent.futures
from typing import Callable, Any

def in_namespace(ns_path: str, timeout: float = 30.0):
    """
    Decorator to run an async function inside a specific Linux namespace.
    Spawns a dedicated thread and a fresh event loop.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            loop = asyncio.get_running_loop()
            
            # Internal worker to be offloaded to a thread
            def thread_worker():
                try:
                    # Enter namespace
                    with open(ns_path) as f:
                        os.setns(f.fileno(), 0)
                    
                    # Create and set thread-local loop
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    
                    try:
                        # Run the target function with a timeout
                        coro = func(*args, **kwargs)
                        return new_loop.run_until_complete(
                            asyncio.wait_for(coro, timeout=timeout)
                        )
                    finally:
                        # Cleanup tasks before closing
                        for task in asyncio.all_tasks(new_loop):
                            task.cancel()
                        new_loop.close()
                except Exception as e:
                    return e

            # Execute in the default ThreadPoolExecutor
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(executor, thread_worker)
                
                # If the result is an exception, raise it in the main loop
                if isinstance(result, Exception):
                    raise result
                return result
                
        return wrapper
    return decorator

# --- Usage Example ---

@in_namespace("/var/run/netns/web_isolated", timeout=5.0)
async def fetch_isolated_data(url: str):
    # This runs in a private network namespace!
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.status_code

async def main():
    try:
        status = await fetch_isolated_data("http://10.0.0.5")
        print(f"Success! Status: {status}")
    except asyncio.TimeoutError:
        print("The namespaced task hung and was killed.")
    except Exception as e:
        print(f"Caught error: {e}")

if __name__ == "__main__":
    asyncio.run(main())