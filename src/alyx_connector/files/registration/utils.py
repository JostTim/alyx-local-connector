from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alyx_connector import Connector


def get_existing_datasets(one_connector: "Connector"):
    existing_dst = one_connector.search("dataset-types", details=False)
    existing_types = [dst["name"] for _, dst in existing_dst.iterrows() if dst["attribute"]]
    return existing_types
