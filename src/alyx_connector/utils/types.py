class Singleton(type):
    """Singleton metaclass that ensures a class has only one instance and provides a global point of access to it.

    This metaclass can be configured to allow reinstantiation of the singleton instance or to restrict
    instantiation with arguments.

    Attributes:
        _instances (dict): A dictionary that holds the instances of the classes that use this metaclass.

    Methods:
        __call__(cls, *args, reinstanciate=False, **kwargs):
            Creates or retrieves the singleton instance of the class. If reinstanciate is True,
            it allows reinitialization of the instance if the class permits it.
            If the class has the attribute _singleton_no_argument_only set to True, it prevents
            instantiation with arguments.

    Args:
        cls (type): The class being instantiated.
        *args: Positional arguments to pass to the class constructor.
        reinstanciate (bool): Flag indicating whether to allow reinstantiation of the singleton instance.
        **kwargs: Keyword arguments to pass to the class constructor.

    Returns:
        object: The singleton instance of the class.
    """

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
    """A Bunch is a subclass of the built-in dict that allows for attribute-style access to its keys.

    This class enables users to access dictionary keys as attributes, providing a more
    convenient way to work with dictionary-like data.

    Attributes:
        __dict__ (dict): A reference to the instance itself, allowing attribute-style access.

    Methods:
        __init__(*args, **kwargs): Initializes the Bunch instance, populating it with the provided key-value pairs.
    """

    # def __init__(self, *args, **kwargs):
    #     super(Bunch, self).__init__(*args, **kwargs)
    #     self.__dict__ = self

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value
