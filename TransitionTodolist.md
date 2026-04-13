# Transitionning from one to alyx-connector

## List of features to support for complete retrocompatibility :

- [ ] from one import ONE
- [ ] from one.api import ONE
- [ ] connector = one.ONE(base_url=alyx_url or "http://haiss-alyx.local",data_access_mode="remote",mode="remote")
- [ ] connector.search(
      subject=["wm40", "wm41", "wm42"],
      exclude_procedures=["Imaging neuropil functionnal mapping"],
      date_range=["2025-03-01", "2025-10-01"],
      details=True,
      no_cache=refresh,
      )
- [ ] sessions = connector.search(subject="mouse12", date_range="2023-05-12")
- [ ] connector.list_datasets(
      session_details.name,
      object="imaging_intrinsic",
      attribute="vessels_reference",
      )[0]
- [ ] connector.list_datasets(session, object="imaging", attribute="fieldOfView")
- [ ] tiff_files_pathes = cnx.list_datasets(
      session.name,
      object="imaging",
      attribute="frames",
      as_mode=data_access_mode,
      query_type=query_type,
      )
- [ ] from one.api import MultiSessionPlaceholder, ONE
- [ ] MultiSessionPlaceholder(
      project="Adaptation",
      analysis_group="good_enough_sessions_05_2025",
      data_repository="Cajal2Adaptation",
      date="2025-05-01",
      )
- [ ] connector.alyx.rest("sessions", "partial_update", id=session_id, data={"extended_qc": session.extended_qc})
- [ ] session.alyx.session.local_mode()
- [ ] session.alyx.session.remote_mode()
- [ ] connector.alyx.json_field_update("sessions", session.name, "json", data=data)
- [ ] session_id = connector.to_eid(session_label)
- [ ] session_details = connector.search(id=session_id, details=True)
- [ ] from one.alf.spec import to_alf
- [ ] filename = to_alf(
      "mask",
      alf_identifier,
      extension="bmp",
      namespace=None,
      timescale=None,
      extra=extra,
      )
- [ ] session_path_str: str = connector.search(id=session_name, details=True, no_cache=True).path # type: ignore
- [ ] one.alf.spec.to_full_path(\*\*alf_info)
- [ ] one.alf.files.get_session_path(filepath)
- [ ] one.alf.spec.path_pattern()
- [ ] alf_info = one.alf.files.full_path_parts(filepath, as_dict=True, absolute=True, assert_valid=False)
- [ ] one.alf.spec.is_valid(filepath, one.alf.spec.FULL_ABSOLUTE_SPEC)
- [ ] connector.path2ref(session_id, as_dict = False)
- [ ] session_details = cnx.to_session_details(
      cnx.alyx.rest("sessions", "read", session_eid, no_cache=True), as_mode="remote"
      )
- [ ] parts = one.alf.files.full_path_parts(file, as_dict=True, absolute=True)
- [ ] alf_name = one.alf.spec.to_full_path(\*\*parts, dromedarize=False)
- [ ] alf_type = one.alf.files.rel_path_parts(alf_name.replace("\\", "/"), as_dict=True)
- [ ] d = {
      "created_by": one.params.get().ALYX_LOGIN,
      "dataset_type": common_alf_type["object"] + "." + common_alf_type["attribute"],
      "data_format": "." + common_alf_type["extension"],
      "collection": common_alf_type["collection"],
      "session_pk": session_details.name,
      "data_repository": repository_name,
      }
- [ ] existing_files = cnx.alyx.rest("datasets", "read", dataset_dict["id"], no_cache=True)["file_records"]
- [ ] new_file_record = cnx.alyx.rest("files", "create", data=d)
- [ ] path_parts = one.alf.files.session_path_parts(file, as_dict=True, absolute=True)
- [ ] cnx.alyx.rest("data-repository", "list", no_cache=True)
- [ ] one.alf.spec.to_full_path(\*\*row)
- [ ] session = connector.search(id=self["session"], no_cache=True, details=True) -[ ] task_dict = connector.alyx.rest("tasks", "create", data=data)
- [ ] from one.alf.files import add_uuid_string
- [ ] destination_path = add_uuid_string(destination_path, source_fr.dataset.pk).as_posix()
- [ ] from one.alf.files import filename_parts
- [ ] from one.alf.spec import is_valid
- [ ] if is_valid(filename):
- [ ] obj_attr = ".".join(filename_parts(filename)[1:3])
- [ ] cnx = ONE(base_url="127.0.0.1", data_access_mode="local", regen=True)
- [ ] session = cnx.search(id=uuid, details=True)
- [ ] connector.register.files(session, files_list)

## Alyx-Connector Migration Checklist

| ✓   | Original Code Example                                                                 | New Syntax                                                                    | Comments                                                                 |
| --- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
|     | **Import Surface**                                                                    |
| ✅  | `from one import ONE`                                                                 | `from alyx_connector import Connector`                                        | Renamed but equivalent                                                   |
| ✅  | `from one.api import ONE`                                                             | `from alyx_connector import Connector`                                        | Renamed but equivalent                                                   |
|     | **Connector Construction**                                                            |
| ✅  | `connector = one.ONE(base_url=..., data_access_mode=..., mode=...)`                   | `connector = Connector(url=..., cache_mode=...)`                              | Map `base_url→host`, `mode→cache_mode`                                   |
|     | **Session Search**                                                                    |
| ✅  | `connector.search(subject=..., exclude_procedures=..., date_range=..., details=True)` | Same syntax                                                                   | Requires filter translation layer                                        |
| ✅  | `sessions = connector.search(subject="mouse12", date_range="2023-05-12")`             | Same syntax                                                                   | Implicit details=True                                                    |
| ◻️  | `session_id = connector.to_eid(session_label)`                                        | ❌​                                                                           | Deprecated (not needed since name can be used too as a retrieving field) |
|     | **Dataset Operations**                                                                |
| ◻️  | `connector.list_datasets(session, object="imaging", attribute="fieldOfView")`         | `connector.datasets.list(session, object="imaging", attribute="fieldOfView")` | Namespaced under `datasets`                                              |
| ◻️  | `tiff_files = cnx.list_datasets(..., as_mode=..., query_type=...)`                    | `tiff_files = cnx.datasets.list(..., access_mode=..., query=...)`             | Parameter rename                                                         |
|     | **ALF Path Utilities**                                                                |
| ◻️  | `from one.alf.spec import to_alf`<br>`filename = to_alf("mask", ...)`                 | `from alyx_connector.alf import to_alf`<br>Same syntax                        | Copied to new package                                                    |
| ◻️  | `one.alf.spec.is_valid(filepath)`                                                     | `alyx_connector.alf.is_valid(filepath)`                                       | Copied verbatim                                                          |
| ◻️  | `one.alf.files.get_session_path(filepath)`                                            | `alyx_connector.paths.session_path(filepath)`                                 | Renamed for clarity                                                      |
|     | **REST Operations**                                                                   |
| ◻️  | `connector.alyx.rest("sessions", "partial_update", id=...)`                           | `connector.alyx.patch(f"sessions/{id}", data=...)`                            | HTTP verb methods                                                        |
| ◻️  | `connector.alyx.json_field_update(...)`                                               | `connector.sessions.update_json_field(...)`                                   | Higher-level helper                                                      |
| ◻️  | `connector.alyx.rest("tasks", "create", data=data)`                                   | `connector.tasks.create(data)`                                                | Namespaced under `tasks`                                                 |
|     | **Session Helpers**                                                                   |
| ◻️  | `from one.api import MultiSessionPlaceholder`                                         | `from project_utils import MultiSessionPlaceholder`                           | Moved to project-specific utils                                          |
| ◻️  | `session.alyx.session.local_mode()`                                                   | _Deprecated_                                                                  | Replaced by cache configuration                                          |
|     | **File Registration**                                                                 |
| ◻️  | `connector.register.files(session, files_list)`                                       | `connector.files.register(session, paths=files_list)`                         | Namespaced under `files`                                                 |
| ◻️  | `destination_path = add_uuid_string(...)`                                             | Same syntax                                                                   | Utility copied verbatim                                                  |
|     | **Metadata Helpers**                                                                  |
| ◻️  | `connector.path2ref(session_id)`                                                      | `connector.sessions.ref_from_id(session_id)`                                  | More explicit naming                                                     |
| ◻️  | `session_details = cnx.to_session_details(...)`                                       | `session_details = SessionDetail(...)`                                        | Now returns dataclass                                                    |

### Key Changes Summary:

1. **Namespace Organization**:

   - `list_datasets` → `connector.datasets.list()`
   - REST operations → `connector.alyx.get()/post()/patch()`
   - Session helpers → `connector.sessions.*`

2. **Parameter Renames**:

   - `mode` → `cache_mode`
   - `data_access_mode` → `files_mode`
   - `as_mode` → `files_mode`
   - `query_type` → `query`
   - `base_url` → `host`

3. **Deprecations**:

   - `local_mode()`/`remote_mode()` removed (control via cache config)
   - `MultiSessionPlaceholder` moved out of core

4. **Utility Preservation**:
   - All ALF path utilities (`to_alf`, `is_valid`, etc.) maintain same syntax
   - `add_uuid_string` behavior preserved

### Recommended Implementation Order:

1. **Shim Layer & Core Connector**

   - [ ] Import shims (`one.__init__`, `one.api`)
   - [ ] `ONE()` constructor with legacy kwargs
   - [ ] Basic `search()` method

2. **Read Operations**

   - [ ] `list_datasets` → `datasets.list()`
   - [ ] ALF path utilities package
   - [ ] `to_eid` → `sessions.id_from_label()`

3. **Write Operations**

   - [ ] REST verb methods (`alyx.get/post/patch`)
   - [ ] `register.files` → `files.register()`
   - [ ] `json_field_update` helper

4. **Session Helpers**
   - [ ] `path2ref` → `sessions.ref_from_id()`
   - [ ] `to_session_details` dataclass

### Migration Tips:

1. **Parameter Mapping Table**:

```python
# Legacy → New
"no_cache" → "cache=False"
"as_mode" → "access_mode"
"query_type" → "query"
```

2. **Test Strategy**:

```python
# Compatibility test pattern
def test_search_compatibility():
    legacy_result = old_ONE().search(...)
    new_result = new_ONE().search(...)
    assert convert_legacy(legacy_result) == new_result
```

3. **Deprecation Warnings**:

```python
import warnings
warnings.warn("one.ONE is deprecated, use alyx_connector.ONE",
              DeprecationWarning, stacklevel=2)
```

Other things to do :

- Check all filters accuracy for sessions (server side + standardisation of how to pass lists, dict, etc, in url format)
- Implement standardized all_of, any_of, none_of, etc, for field filtering (server side)
- Reduce number of items sent by dataset related to session listing, keeping only what makes sense (server side)
- Keep sending all info when retrieving datasets
- Verify push / pull files still works correctly
- Verify registration works correctly
- Implement registration rules table or just add it as a "note" in the notes table, in the json field
- Lorenzo wanted actions to be done as rules (like link dataset types, for them to match length, etc). Probably too long / complex for short term
- Implement local side parsing of the schema.json file (for the connector's disconnected mode)
- Implement, using the schema.json, and pickle / parquet tables, local data cache (for fast access, and for disconnected mode)
- The cache is updated simply by downloading the tables from the endpoint, or when making a request, by inserting newest data into the files (as the tables are updated only daily, server side)
- Using an url-api cache too ? (for less url requests ?)
