from .extract_transactions import load_source, SourceName, Source
from sys import argv

if __name__ == "__main__":
    source = Source(SourceName.BANQUE_POPULAIRE)
    source.load()