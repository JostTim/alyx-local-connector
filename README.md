# Open Neurophysiology Environment - HaissLab flavour

The Open Neurophysiology Environment is a scheme for accessing neurophysiology data from an alyx database server, in a standardized manner. For information on how to manage file names when registering data with ONE to an alyx server, please [click here](https://github.com/int-brain-lab/ONE/blob/main/docs/Open_Neurophysiology_Environment_Filename_Convention.pdf). This github page contains an API for searching and loading ONE-standardized data, stored either on a user’s local machine or on a remote server. Please [Click here](https://int-brain-lab.github.io/ONE/) for the main documentation page.

**NB**: The API and backend database are still under active development, for the best experience please regularly update the package by running :  
`pip install --force-reinstall --no-deps git+https://gitlab.pasteur.fr/haisslab/data-management/ONE.git`.  
This  will force the reinstallation of the package, without the need to do a `pip uninstall ONE-api` first, and without reinstalling the dependancies like numpy etc (hence faster).

## Requirements
ONE runs on Python 3.7 or later, and is tested on the latest Ubuntu and Windows (3.7 and 3.8 only).

## Installing
Installing the package via pip typically takes a few seconds.  To install, activate your developpement environment :
```
conda activate <myenvironment>
```
Then run the One-api install using :
```
pip install git+https://gitlab.pasteur.fr/haisslab/data-management/ONE.git
```

## Set up

For setting up ONE for a given database e.g. our local version of Alyx at HaissLab:
```python
from one import ONE
one = ONE(base_url='http://157.99.138.172:8080')
```

Once you've setup the server, subsequent calls will use the same parameters:
```python
from one import ONE
one = ONE() #uses the same parameters entered the first time and stored by default in C:\Users\<myusername>\AppData\Roaming\.one\.157.99.138.172_8080: 

```
For using ONE with a local cache directory (not recommanded for now):
```python
from one import One
one = One(cache_dir='/home/user/downlaods/ONE/behavior_paper')
```

## Using ONE
To search for sessions:
```python
from one import ONE
one = ONE()
print(one.search_terms())  # A list of search keyword arguments

# Search session with wheel timestamps from January 2021 onward
eids = one.search(date_range=['2021-01-01',], dataset='wheel.timestamps')
['d3372b15-f696-4279-9be5-98f15783b5bb'] # this is a list of unique ids of sessions returned. Here only one has been found with given parameters

# Search for project sessions with two probes
eids = one.search(data=['probe00', 'probe01'], project='brainwide')
```

Further examples and tutorials can be found in the main IBL documentation [documentation](https://int-brain-lab.github.io/ONE/).


(Not currentely supported :)

To load data:
```python
from one.api import ONE
one = ONE()

# Load an ALF object
eid = 'a7540211-2c60-40b7-88c6-b081b2213b21'
wheel = one.load_object(eid, 'wheel')

# Load a specific dataset
eid = 'a7540211-2c60-40b7-88c6-b081b2213b21'
ts = one.load_dataset(eid, 'wheel.timestamps', collection='alf')

# Download, but not load, a dataset
filename = one.load_dataset(eid, 'wheel.timestamps', download_only=True)
```


