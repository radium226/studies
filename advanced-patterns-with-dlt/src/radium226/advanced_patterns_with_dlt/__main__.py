from .sources import app
from .spi import Source

if __name__ == "__main__":

    source: Source[str] = app.Source()


    source.load(full={"crm": False, "marketing": True})