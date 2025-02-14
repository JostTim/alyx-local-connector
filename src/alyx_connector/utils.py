from functools import wraps


# def singleton(cls):
#     instances = {}

#     @wraps(cls)
#     def getinstance(*args, **kwargs):
#         if cls not in instances or kwargs.get("regen", False) is True:
#             kwargs.pop("regen", None)
#             instances[cls] = cls(*args, **kwargs)
#         return instances[cls]

#     return getinstance


class Singleton(type):
    _instances = {}

    # we are going to redefine (override) what it means to "call" a class
    # as in ....  x = MyClass(1,2,3)
    def __call__(cls, *args, reinstanciate=False, **kwargs):
        if cls not in cls._instances:
            # we have not every built an instance before.  Build one now.
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        else:
            instance = cls._instances[cls]
            # here we are going to call the __init__ and maybe reinitialize.
            if getattr(cls, "__allow_reinstanciation", False) and reinstanciate:
                # if the class allows reinitialization, then do it
                instance.__init__(*args, **kwargs)  # call the init again

        return instance
