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

echo "Starting server..."
python3 ./backend.py \
  --account-file $ACCOUNT_FILE \
  --cookie-dir $COOKIE_DIR \
  --log $LOG_FILE \
  --debug $DEBUG_FILE \
  --port "$PORT" \
  --mongo-host $MONGO_HOST \
  --mongo-port $MONGO_PORT \
  --mongo-db $MONGO_DB \
  --twitter-auth-key $TWITTER_AUTH_KEY
