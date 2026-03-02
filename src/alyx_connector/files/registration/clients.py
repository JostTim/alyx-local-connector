


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...connector.core import Connector

class RegistrationClient:

    def __init__(self, connector:Connector):
        self.parent_connector = connector

    def add_files(self, files_list):
        self.parent_connector.remote.create(
            endpoint="datasets",
            data={
                "session_pk": "dc5a61db-cbb6-48dc-bcb9-310211c8512c",
                "dataset_type": "trials.choice",
                "collection": "DELETE_LATER_JUST_A_TEST",
                "data_format": ".txt",
                "set_file_records": [
                    {"extra": "001", "exists": True},
                    {"extra": "002", "exists": True},
                    {"extra": "003", "exists": True},
                ],
            },
        )
