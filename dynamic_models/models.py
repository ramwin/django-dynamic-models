from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.db.utils import DEFAULT_DB_ALIAS
from django.utils.text import slugify

from dynamic_models import compat, config
from dynamic_models.exceptions import InvalidFieldNameError, NullFieldChangedError
from dynamic_models.factory import ModelFactory
from dynamic_models.schema import FieldSchemaEditor, ModelSchemaEditor
from dynamic_models.utils import ModelRegistry


class ModelSchema(models.Model):
    name = models.CharField(max_length=250, unique=True)
    db_name = models.CharField(max_length=32, default=DEFAULT_DB_ALIAS)
    managed = models.BooleanField(default=True)
    db_table_name = models.CharField(null=True, max_length=250)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._registry = ModelRegistry(self.app_label)
        self._initial_name = self.name
        initial_model = self.get_registered_model()
        self._schema_editor = (
            ModelSchemaEditor(initial_model=initial_model, db_name=self.db_name)
            if self.managed
            else None
        )

    def save(self, **kwargs):
        super().save(**kwargs)
        if self._schema_editor:
            self._schema_editor.update_table(self._factory.get_model())

        self._initial_name = self.name

    def delete(self, **kwargs):
        if self._schema_editor:
            self._schema_editor.drop_table(self.as_model())
        self._factory.destroy_model()
        super().delete(**kwargs)

    def get_registered_model(self):
        return self._registry.get_model(self.model_name)

    @property
    def _factory(self):
        return ModelFactory(self)

    @property
    def app_label(self):
        return config.dynamic_models_app_label()

    @property
    def model_name(self):
        return self.get_model_name(self.name)

    @property
    def initial_model_name(self):
        return self.get_model_name(self._initial_name)

    @classmethod
    def get_model_name(cls, name):
        return name.title().replace(" ", "")

    @property
    def db_table(self):
        return self.db_table_name if self.db_table_name else self._default_db_table_name()

    def _default_db_table_name(self):
        safe_name = slugify(self.name).replace("-", "_")
        return f"{self.app_label}_{safe_name}"

    def as_model(self):
        return self._factory.get_model()


class FieldKwargsJSON(compat.JSONField):
    description = "A field that handles storing models.Field kwargs as JSON"

    def to_python(self, value):
        raw_value = super().to_python(value)
        try:
            return self._convert_on_delete_to_function(raw_value)
        except AttributeError as err:
            raise ValidationError("Invalid value for 'on_delete'") from err

    def from_db_value(self, value, expression, connection):
        # django.contrib.postgres.fields.JSONField does not implement from_db_value
        # for some reason. In that version, value is already a dict
        try:
            db_value = super().from_db_value(value, expression, connection)
        except AttributeError:
            db_value = value
        return self._convert_on_delete_to_function(db_value)

    def get_prep_value(self, value):
        prep_value = self._convert_on_delete_to_string(value)
        return super().get_prep_value(prep_value)

    def _convert_on_delete_to_function(self, raw_value):
        if raw_value is None or "on_delete" not in raw_value:
            return raw_value

        raw_on_delete = raw_value["on_delete"]
        if isinstance(raw_on_delete, str):
            raw_value["on_delete"] = getattr(models, raw_on_delete)

        return raw_value

    def _convert_on_delete_to_string(self, raw_value):
        if raw_value is None or "on_delete" not in raw_value:
            return raw_value

        raw_on_delete = raw_value["on_delete"]
        if callable(raw_on_delete):
            raw_value["on_delete"] = raw_on_delete.__name__

        return raw_value


class FieldSchema(models.Model):
    _PROHIBITED_NAMES = ("__module__", "_declared")

    name = models.CharField(max_length=63)
    model_schema = models.ForeignKey(ModelSchema, on_delete=models.CASCADE, related_name="fields")
    class_name = models.TextField()
    kwargs = FieldKwargsJSON(default=dict)

    class Meta:
        unique_together = (("name", "model_schema"),)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initial_name = self.name
        self._initial_null = self.null
        self._initial_field = self.get_registered_model_field()
        self._schema_editor = (
            FieldSchemaEditor(initial_field=self._initial_field, db_name=self.model_schema.db_name)
            if self.model_schema.managed
            else None
        )

    def save(self, **kwargs):
        self.validate()
        super().save(**kwargs)
        model, field = self._get_model_with_field()
        if self._schema_editor:
            self._schema_editor.update_column(model, field)

    def delete(self, **kwargs):
        model, field = self._get_model_with_field()
        if self._schema_editor:
            self._schema_editor.drop_column(model, field)
        super().delete(**kwargs)

    def validate(self):
        if self._initial_null and not self.null:
            raise NullFieldChangedError(f"Cannot change NULL field '{self.name}' to NOT NULL")

        if self.name in self.get_prohibited_names():
            raise InvalidFieldNameError(f"{self.name} is not a valid field name")

    def get_registered_model_field(self):
        latest_model = self.model_schema.get_registered_model()
        if latest_model and self.name:
            try:
                return latest_model._meta.get_field(self.name)
            except FieldDoesNotExist:
                pass

    @classmethod
    def get_prohibited_names(cls):
        # TODO: return prohbited names based on backend
        return cls._PROHIBITED_NAMES

    @property
    def db_column(self):
        return slugify(self.name).replace("-", "_")

    @property
    def null(self):
        return self.kwargs.get("null", False)

    @null.setter
    def null(self, value):
        self.kwargs["null"] = value

    def get_options(self):
        return self.kwargs.copy()

    def _get_model_with_field(self):
        model = self.model_schema.as_model()
        try:
            field = model._meta.get_field(self.db_column)
        except FieldDoesNotExist:
            field = None
        return model, field
