#!/usr/bin/env bash

echo -n "Looking for Python3: "
if ! hash python3; then
  echo -n "\nPlease install Python3 to use this program!"
fi
echo "OK"

echo "Installing dependencies..."
pip3 install -r requirements.txt --no-cache-dir

echo -e "\n----------------------------"
echo "All done! \o/"
echo "Run 'PYTON_ENV=[development|prodcution] ./run.sh' to start the server!"
