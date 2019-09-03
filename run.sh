#/usr/bin/env bash

if [ "$1" != 'production' ] && [ "$1" != 'development' ]; then
  echo "Please provide 'production' or 'development' as first argument"
  echo "e.g. $ $0 development"
  exit
fi


EXPECTED_ENV_FILE="./.env.$1"
if [ ! -f $EXPECTED_ENV_FILE ]; then
  echo "Please provide a configuration file {$EXPECTED_ENV_FILE}!"
fi
echo "Using configuration from: $EXPECTED_ENV_FILE"
source $EXPECTED_ENV_FILE
echo "Listening on: $PORT"

PORT=$PORT EXPECTED_ENV_FILE=$EXPECTED_ENV_FILE docker-compose -f docker-compose.yml up
