class FieldValidator:
    def __init__(self, parent, name, raising=False):
        self.name = name
        self.parent = parent
        self.raising = raising

    def __call__(self):
        status = self.name in self.parent.fields
        if not status and self.raising:
            raise ValueError(
                f"Name {self.name} is not a valid field for model {self.parent.__name__}. "
                f"Valid fields are : {','.join(self.parent.fields)}"
            )
        return status

    def value(self, value):
        if not self():
            return False
        model = self.parent[self.name]
        status = self.validate_against(value, model)
        if not status and self.raising:
            raise ValueError(
                f"Value {type(value)} : {value} for field {self.parent.__name__}.{self.name} is invalid. It must follow"
                f" the types : {model}"
            )
        return status

    def validate_against(self, value, model):
        # if several models, we go through them recursively
        if isinstance(model, list):
            for type_item in model:
                if self.validate_against(value, type_item):
                    return True
            return False

        # first case, we test test_value against a type
        if isinstance(model, type):
            return self.validate_type(value, model)
        # second case, we test test_value against a value
        else:
            return self.validate_type(value, model)

    def validate_type(self, value, type_model):
        if isinstance(value, type_model):
            return True
        return False

    def validate_value(self, value, value_model):
        if value == value_model:
            return True
        return False


class Validator:
    def __init__(self, parent, raising):
        self.parent = parent
        self.raising = raising

    def field(self, name):
        return FieldValidator(self.parent, name, self.raising)

    def __call__(self, name):
        return FieldValidator(self.parent, name, self.raising)()

    def dictionnary(self, dict):
        for key, value in dict.items():
            if not self.field(key).value(value):
                return False
        return True


class ModelMeta(type):
    def __init__(cls, name, bases, attrs):
        super().__init__(name, bases, attrs)

        fields = [
            attr
            for attr, obj in attrs.items()  # cls.__dict__.items()
            if not attr.startswith("__") and (not callable(obj) or isinstance(obj, type))
        ]
        cls.fields = fields
        cls.validate = Validator(cls, raising=False)
        cls.assert_valid = Validator(cls, raising=True)

    def __getitem__(cls, name):
        return getattr(cls, name)


class Model(object, metaclass=ModelMeta):
    pass


class Session(Model):
    default_data_repository_pk = str
    json = dict
    location = str
    subject = str
    number = int


class Dataset(Model):
    version = str
    collection = str
    data_repository_pk = str
    data_repository = str
    dataset_type = str
    data_format = str
    default_dataset = bool
    session_pk = str
    created_by = str


class File(Model):
    exists = bool
    extra = str
    dataset = str
