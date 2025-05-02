# Python Connector to Alyx-Local instance(s)

## Install 

Install with

```bash
pip install alyx-connector
```

or

```bash
pip install git+https://github.com/JostTim/alyx-local-connector.git@docker-compatible
```

## First use

You need to create a connector object to the alyx_instance that you are using.
The easyest way to create one at the beginning, or for any other new connection with a different username or to a different instance, you can use the `Connector.setup()` class function :

```python
from alyx_connector import Connector

connector = Connector.setup()
```

You will be prompted to enter informations for setting the new connection.

First, the address of the server : 
![Enter address](.assets/images/first_connection/Capture%20d'écran%202025-04-28%20100132.png)

Second, the username to use : 
![Enter username](.assets/images/first_connection/Capture%20d'écran%202025-04-28%20100400.png)

Optionally, decide wether to make this the default address and username combo, when you call `Connector()` without arguments, at later times :
![Enter set default](.assets/images/first_connection/Capture%20d'écran%202025-04-28%20100309.png)

Lastly, the password to use with that address / username combo : (if successfull, the server will provide a token that will be saved by the connector for that adress/username combo, so that you don't need to retype the password again, but the pasword itself is not saved for security reasons)
![Enter password](.assets/images/first_connection/Capture%20d'écran%202025-04-28%20103000.png)

If the connection succeeds, you will get a connector object :
![connector obtained](.assets/images/first_connection/Capture%20d'écran%202025-04-28%20100459.png)


## Subsequent uses

After setting up at least a first connection, you can use the connector at later times (even after restarting python) using several ways :


### Using the default server / username :
```python
from alyx_connector import Connector
connector = Connector()
```

### Using a specific server, but the default username for that server :
```python
from alyx_connector import Connector
connector = Connector(url="127.0.0.1")
```

### Using a specific server and username :
```python
from alyx_connector import Connector
connector = Connector(url="127.0.0.1", username="myname")
```

At any time, you can check the server and username used by the connect, by typing `connector.url` or `connector.username`


## Accessing experimental data using the connector


### Searching for a session 

The main use of the connector is usually to search experimental sessions data, based on some filter.

To get a table of session when the animal used is for example named ``ea04``, you can type :

```python
sessions = connector.search(subject = "ea04")
```

you will get a ``pandas.DataFrame`` where each row is corresponding to a session, and each column to some information relative to that session, as available on the alyx-local website.

Example :
![session_table](.assets/images/search_sessions/Capture%20d'écran%202025-04-28%20104158.png)


You can get the session infos ``narrative`` for example, for the third session that you obtained here, by doing : 

```python
session = sessions.iloc[3] # get the third session in the table, by index order
print(session.narrative) # print the narrative
```

![session_narrative](.assets/images/search_sessions/Capture%20d'écran%202025-04-28%20115001.png)

You can also access the json based fields : `session.json` and `session.extended_qc`, which are standard python dictionnaries. (containing potentially nested data that you entered)

```python
session.json
```

![session_json](.assets/images/search_sessions/Capture%20d'écran%202025-04-28%20114948.png)
![session_json_getitem](.assets/images/search_sessions/Capture%20d'écran%202025-04-28%20114932.png)

The ``json`` field is meant as a way to store additionnal metadata that you cannot enter inside the regular session table fields. (as different experiments may need different fields)

The ``extended_qc`` field is meant as a way to store detailed ``Quality Check`` information, that you can use to get only the sessions that passed particular criterias after checking the data. (non exhaustive examples : a noise level in the electrophy recordings, etc...)


