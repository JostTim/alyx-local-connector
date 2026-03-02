from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alyx_connector import Connector


def get_dataset_types(connector: "Connector"):
    existing_dst = connector.remote.search("dataset-types", details=False)
    existing_types = [dst["name"] for _, dst in existing_dst.iterrows() if dst["attribute"]]
    return existing_types
