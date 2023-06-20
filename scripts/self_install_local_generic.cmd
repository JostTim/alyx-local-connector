set /p "env_name=Enter environment name: "
call conda activate %env_name%
cd ..
pip install . 
PAUSE