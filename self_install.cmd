set /p "env_name=Enter environment name: "
call conda activate %env_name%
pip uninstall one-api
pip install .
PAUSE