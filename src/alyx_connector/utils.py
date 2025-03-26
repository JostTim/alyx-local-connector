from functools import wraps


class Singleton(type):
    _instances = {}

    # we are going to redefine (override) what it means to "call" a class
    # as in ....  x = MyClass(1,2,3)
    def __call__(cls, *args, reinstanciate=False, **kwargs):

        if cls not in cls._instances:
            # cls is instanciated for the first time
            cls._instances[cls] = super().__call__(*args, **kwargs)
            return cls._instances[cls]

        if reinstanciate:
            # cls is reinstanciated because it has been asked to
            if getattr(cls, "_singleton_allow_reinstanciation", False):
                # either self reinstanciating the same object if allowed by calling the init again
                cls._instances[cls].__init__(*args, **kwargs)
            else:
                # or creating a new one cleanly
                cls._instances[cls] = super().__call__(*args, **kwargs)

        elif getattr(cls, "_singleton_no_argument_only", False) and (args or kwargs):
            # cls is reinstanciated because it has been called with arguments
            # and __singleton_no_argument_only flag exists in the file
            cls._instances[cls] = super().__call__(*args, **kwargs)

        return cls._instances[cls]


class Bunch(dict):
    def __init__(self, *args, **kwargs):
        super(Bunch, self).__init__(*args, **kwargs)
        self.__dict__ = self
