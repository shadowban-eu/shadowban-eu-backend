#/usr/bin/env bash

echo "Starting server..."
echo "--account-file $ACCOUNT_FILE"
echo "--cookie-dir $COOKIE_DIR"
echo "--log $LOG_FILE"
echo "--debug $DEBUG_FILE"
echo "--port "$PORT""
echo "--host "$HOST""
echo "--mongo-host $MONGO_HOST"
echo "--mongo-port $MONGO_PORT"
echo "--mongo-db $MONGO_DB"
echo "--twitter-auth-key $TWITTER_AUTH_KEY"

python3 -u ./backend.py \
  --account-file $ACCOUNT_FILE \
  --cookie-dir $COOKIE_DIR \
  --log $LOG_FILE \
  --debug $DEBUG_FILE \
  --port "$PORT" \
  --host "$HOST" \
  --mongo-host $MONGO_HOST \
  --mongo-port $MONGO_PORT \
  --mongo-db $MONGO_DB \
  --twitter-auth-key $TWITTER_AUTH_KEY
