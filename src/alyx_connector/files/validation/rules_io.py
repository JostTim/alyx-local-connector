from backrefs.bre import S
from yaml.composer import Composer
from yaml.constructor import SafeConstructor
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import Resolver
from yaml.scanner import Scanner


# Create custom safe constructor class that inherits from SafeConstructor
class BooleanSafeConstructor(SafeConstructor):
    # Create new method handle boolean logic
    def add_bool(self, node):
        return self.construct_scalar(node)


# Inject the above boolean logic into the custom constuctor
BooleanSafeConstructor.add_constructor("tag:yaml.org,2002:bool", BooleanSafeConstructor.add_bool)


class SafeLoader(Reader, Scanner, Parser, Composer, BooleanSafeConstructor, Resolver):
    def __init__(self, stream):
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        BooleanSafeConstructor.__init__(self)
        Resolver.__init__(self)
