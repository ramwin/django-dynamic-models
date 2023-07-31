from contextlib import contextmanager

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import connection


def db_table_exists(table_name):
    with _db_cursor() as c:
        table_names = connection.introspection.table_names(c)
        return table_name in table_names


def db_table_has_field(table_name, field_name):
    table = _get_table_description(table_name)
    return field_name in [field.name for field in table]


def db_field_allows_null(table_name, field_name):
    table_description = _get_table_description(table_name)
    for field in table_description:
        if field.name == field_name:
            return field.null_ok
    raise FieldDoesNotExist(f"field {field_name} does not exist on table {table_name}")


def _get_table_description(table_name):
    with _db_cursor() as c:
        return connection.introspection.get_table_description(c, table_name)


@contextmanager
def _db_cursor():
    cursor = connection.cursor()
    yield cursor
    cursor.close()


class ModelRegistry:
    def __init__(self, app_label):
        self.app_label = app_label

    def is_registered(self, model_name):
        return model_name.lower() in apps.all_models[self.app_label]

    def get_model(self, model_name):
        try:
            return apps.get_model(self.app_label, model_name)
        except LookupError:
            return None

    def unregister_model(self, model_name):
        try:
            del apps.all_models[self.app_label][model_name.lower()]
        except KeyError as err:
            raise LookupError("'{}' not found.".format(model_name)) from err
