#/usr/bin/env bash

if [ "$PYTHON_ENV" != 'production' ] && [ "$PYTHON_ENV" != 'development' ]; then
  echo "Please provide a PYTHON_ENV value of 'production' or 'development'"
  echo "e.g. $ PYTHON_ENV=development $0"
  exit
fi


EXPECTED_ENV_FILE="./.env.$PYTHON_ENV"
if [ ! -f $EXPECTED_ENV_FILE ]; then
  echo "Please provide a configuration file {$EXPECTED_ENV_FILE}!"
fi
echo "Using configuration from: $EXPECTED_ENV_FILE"
source $EXPECTED_ENV_FILE

echo "Starting server..."
python3 ./backend.py \
  --account-file $ACCOUNT_FILE \
  --log $LOG_FILE \
  --debug $DEBUG_FILE \
  --port "$PORT" \
  --mongo-host $MONGO_HOST \
  --mongo-port $MONGO_PORT \
  --mongo-db $MONGO_DB
