from typing import Type

type Variant = str


class Using():
    
    def __init__(self, variant: Variant):
        self.variant = variant



class Given():

    def __init__(self, type: Type):
        self.type = type