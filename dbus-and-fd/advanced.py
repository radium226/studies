import asyncio
import os

async def writer_task(write_fd):
    """Task that writes data to the pipe"""
    loop = asyncio.get_event_loop()
    
    try:
        i = 0
        while True:
            i += 1
            data = f"Message {i}\n"
            # Use run_in_executor for the blocking write operation
            await loop.run_in_executor(None, os.write, write_fd, data.encode())
            print(f"Wrote: {data.strip()}")
            # await asyncio.sleep(1)  # Simulate some work
            
    except Exception as e:
        print(f"Writer error: {e}")
    finally:
        os.close(write_fd)
        print("Writer closed")

async def reader_task(read_fd):
    """Task that reads data from the pipe"""
    loop = asyncio.get_event_loop()
    
    try:
        while True:
            # Use run_in_executor for the blocking read operation
            data = await loop.run_in_executor(None, os.read, read_fd, 1024)
            
            if not data:  # EOF - writer closed
                break
                
            message = data.decode().strip()
            print(f"Read: {message}")
            
    except Exception as e:
        print(f"Reader error: {e}")
    finally:
        os.close(read_fd)
        print("Reader closed")

async def main():
    # Create the pipe
    read_fd, write_fd = os.pipe()
    
    # Run both tasks concurrently
    await asyncio.gather(
        writer_task(write_fd),
        reader_task(read_fd)
    )

if __name__ == "__main__":
    # Run the advanced main function
    asyncio.run(main())