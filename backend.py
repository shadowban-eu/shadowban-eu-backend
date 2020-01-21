import aiohttp
import argparse
import asyncio
import daemon
import json
import os
import re
import traceback
import urllib.parse
import sys
import time

from aiohttp import web
from bs4 import BeautifulSoup
from db import connect

routes = web.RouteTableDef()

class UnexpectedApiError(Exception):
    pass

def get_nested(obj, path, default=None):
    for p in path:
        if obj is None or not p in obj:
            return default
        obj = obj[p]
    return obj

def is_error(result, code=None):
    return isinstance(result.get("errors", None), list) and (len([x for x in result["errors"] if x.get("code", None) == code]) > 0 or code is None and len(result["errors"] > 0))

def is_another_error(result, codes):
    return isinstance(result.get("errors", None), list) and len([x for x in result["errors"] if x.get("code", None) not in codes]) > 0

account_sessions = []
account_index = 0
log_file = None
debug_file = None
guest_session_pool_size = 10
guest_sessions = []
test_index = 0

def next_session():
    def key(s):
        remaining_time = s.reset - time.time()
        if s.remaining <= 3 and remaining_time > 0:
            return 900
        return remaining_time
    sessions = sorted([s for s in account_sessions if not s.locked], key=key)
    if len(sessions) > 0:
        return sessions[0]

class TwitterSession:
    twitter_auth_key = None

    def __init__(self):
        self._guest_token = None
        self._csrf_token = None

        # aiohttp ClientSession
        self._session = None

        # rate limit monitoring
        self.limit = -1
        self.remaining = 180
        self.reset = -1
        self.overshot = -1
        self.locked = False
        self.next_refresh = None

        # session user's @username
        # this stays `None` for guest sessions
        self.username = None

        self._headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"
        }
        # sets self._headers
        self.reset_headers()

    def set_csrf_header(self):
        cookies = self._session.cookie_jar.filter_cookies('https://twitter.com/')
        for key, cookie in cookies.items():
            if cookie.key == 'ct0':
                self._headers['X-Csrf-Token'] = cookie.value

    async def get_guest_token(self):
        self._headers['Authorization'] = 'Bearer ' + self.twitter_auth_key
        async with self._session.post("https://api.twitter.com/1.1/guest/activate.json", headers=self._headers) as r:
            response = await r.json()
        guest_token = response.get("guest_token", None)
        if guest_token is None:
            debug("Failed to fetch guest token")
            debug(str(response))
            debug(str(self._headers))
        return guest_token

    def reset_headers(self):
        self._headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"
        }

    async def renew_session(self):
        await self.try_close()
        self._session = aiohttp.ClientSession()
        self.reset_headers()

    async def refresh_old_token(self):
        if self.username is not None or self.next_refresh is None or time.time() < self.next_refresh:
            return
        debug("Refreshing token: " + str(self._guest_token))
        await self.login_guest()
        debug("New token: " + str(self._guest_token))

    async def try_close(self):
        if self._session is not None:
            try:
                await self._session.close()
            except:
                pass

    async def login_guest(self):
        await self.renew_session()
        self.set_csrf_header()
        old_token = self._guest_token
        new_token = await self.get_guest_token()
        self._guest_token = new_token if new_token is not None else old_token
        if new_token is not None:
            self.next_refresh = time.time() + 3600
        self._headers['X-Guest-Token'] = self._guest_token

    async def login(self, username = None, password = None, email = None, cookie_dir=None):
        self._session = aiohttp.ClientSession()

        if password is not None:
            login_required = True
            cookie_file = None
            if cookie_dir is not None:
                cookie_file = os.path.join(cookie_dir, username)
                if os.path.isfile(cookie_file):
                    log("Use cookie file for %s" % username)
                    self._session.cookie_jar.load(cookie_file)
                    login_required = False

            store_cookies = True

            if login_required:
                async with self._session.get("https://twitter.com/login", headers=self._headers) as r:
                    login_page = await r.text()
                form_data = {}
                soup = BeautifulSoup(login_page, 'html.parser')
                form_data["authenticity_token"] = soup.find('input', {'name': 'authenticity_token'}).get('value')
                form_data["session[username_or_email]"] = email
                form_data["session[password]"] = password
                form_data["remember_me"] = "1"
                async with self._session.post('https://twitter.com/sessions', data=form_data, headers=self._headers) as r:
                    response = await r.text()
                    if str(r.url) == "https://twitter.com/":
                        log("Login of %s successful" % username)
                    else:
                        store_cookies = False
                        log("Error logging in %s (%s)" % (username, r.url))
                        debug("ERROR PAGE\n" + response)
            else:
                async with self._session.get('https://twitter.com', headers=self._headers) as r:
                    await r.text()

            self.set_csrf_header()
            self.username = username

            if cookie_file is not None and store_cookies:
                self._session.cookie_jar.save(cookie_file)

        else:
            await self.login_guest()

        self._headers['Authorization'] = 'Bearer ' + self.twitter_auth_key

    async def get(self, url, retries=0):
        self.set_csrf_header()
        await self.refresh_old_token()
        try:
            async with self._session.get(url, headers=self._headers) as r:
                result = await r.json()
        except Exception as e:
            debug("EXCEPTION: " + str(type(e)))
            if self.username is None:
                await self.login_guest()
            raise e
        self.monitor_rate_limit(r.headers)
        if self.username is None and self.remaining < 10 or is_error(result, 88) or is_error(result, 239):
            await self.login_guest()
        if retries > 0 and is_error(result, 353):
            return await self.get(url, retries - 1)
        if is_error(result, 326):
            self.locked = True
        return result

    async def search_raw(self, query, live=True):
        additional_query = ""
        if live:
            additional_query = "&tweet_search_mode=live"
        return await self.get("https://api.twitter.com/2/search/adaptive.json?q="+urllib.parse.quote(query)+"&count=20&spelling_corrections=0" + additional_query)

    async def typeahead_raw(self, query):
        return await self.get("https://api.twitter.com/1.1/search/typeahead.json?src=search_box&result_type=users&q=" + urllib.parse.quote(query))

    async def profile_raw(self, username):
        return await self.get("https://api.twitter.com/1.1/users/show.json?screen_name=" + urllib.parse.quote(username))

    async def get_profile_tweets_raw(self, user_id):
        return await self.get("https://api.twitter.com/2/timeline/profile/" + str(user_id) +".json?include_tweet_replies=1&include_want_retweets=0&include_reply_count=1&count=1000")

    async def tweet_raw(self, tweet_id, count=20, cursor=None, retry_csrf=True):
        if cursor is None:
            cursor = ""
        else:
            cursor = "&cursor=" + urllib.parse.quote(cursor)
        return await self.get("https://api.twitter.com/2/timeline/conversation/" + tweet_id + ".json?include_reply_count=1&send_error_codes=true&count="+str(count)+ cursor)

    def monitor_rate_limit(self, headers):
        # store last remaining count for reset detection
        last_remaining = self.remaining
        limit = headers.get('x-rate-limit-limit', None)
        remaining = headers.get('x-rate-limit-remaining', None)
        reset = headers.get('x-rate-limit-reset', None)
        if limit is not None:
            self.limit = int(limit)
        if remaining is not None:
            self.remaining = int(remaining)
        if reset is not None:
            self.reset = int(reset)

        # rate limit reset
        if last_remaining < self.remaining and self.overshot > 0 and self.username is not None:
            log('[rate-limit] Reset detected for ' + self.username + '. Saving overshoot count...')
            db.write_rate_limit({ 'screen_name': self.username, 'overshot': self.overshot })
            self.overshot = 0

        # count the requests that failed because of rate limiting
        if self.remaining is 0:
            log('[rate-limit] Limit hit by ' + str(self.username) + '.')
            self.overshot += 1

    @classmethod
    def flatten_timeline(cls, timeline_items):
        result = []
        for item in timeline_items:
            if get_nested(item, ["content", "item", "content", "tweet", "id"]) is not None:
                result.append(item["content"]["item"]["content"]["tweet"]["id"])
            elif get_nested(item, ["content", "timelineModule", "items"]) is not None:
                timeline_items = item["content"]["timelineModule"]["items"]
                titems = [get_nested(x, ["item", "content", "tweet", "id"]) for x in timeline_items]
                result += [x for x in titems if x is not None]
        return result

    @classmethod
    def get_ordered_tweet_ids(cls, obj, filtered=True):
        try:
            entries = [x for x in obj["timeline"]["instructions"] if "addEntries" in x][0]["addEntries"]["entries"]
        except (IndexError, KeyError):
            return []
        entries.sort(key=lambda x: -int(x["sortIndex"]))
        flat = cls.flatten_timeline(entries)
        return [x for x in flat if not filtered or x in obj["globalObjects"]["tweets"]]

    async def test_ghost_ban(self, user_id):
        try:
            tweets_replies = await self.get_profile_tweets_raw(user_id)
            tweet_ids = self.get_ordered_tweet_ids(tweets_replies)
            replied_ids = []
            for tid in tweet_ids:
                if tweets_replies["globalObjects"]["tweets"][tid]["reply_count"] > 0 and tweets_replies["globalObjects"]["tweets"][tid]["user_id_str"] == user_id:
                    replied_ids.append(tid)

            for tid in replied_ids:
                tweet = await self.tweet_raw(tid)
                for reply_id, reply_obj in tweet["globalObjects"]["tweets"].items():
                    if reply_id == tid or reply_obj.get("in_reply_to_status_id_str", None) != tid:
                        continue
                    reply_tweet = await self.tweet_raw(reply_id)
                    if reply_id not in reply_tweet["globalObjects"]["tweets"]:
                        continue
                    obj = {"tweet": tid, "reply": reply_id}
                    if tid in reply_tweet["globalObjects"]["tweets"]:
                        obj["ban"] = False
                    else:
                        obj["ban"] = True
                    return obj
        except:
            debug('Unexpected Exception:')
            debug(traceback.format_exc())

    async def test_barrier(self, user_id):
        try:
            tweets_replies = await self.get_profile_tweets_raw(user_id)
            tweet_ids = self.get_ordered_tweet_ids(tweets_replies)

            reply_tweet_ids = []

            for tid in tweet_ids:
                if "in_reply_to_status_id_str" not in tweets_replies["globalObjects"]["tweets"][tid] or tweets_replies["globalObjects"]["tweets"][tid]["user_id_str"] != user_id:
                    continue
                tweet = tweets_replies["globalObjects"]["tweets"][tid]
                conversation_tweet = get_nested(tweets_replies, ["globalObjects", "tweets", tweet["conversation_id_str"]])
                if conversation_tweet is not None and conversation_tweet.get("user_id_str") == user_id:
                    continue
                reply_tweet_ids.append(tid)

            # return error message, when user has not made any reply tweets
            if not reply_tweet_ids:
                return {"error": "ENOREPLIES"}

            for tid in reply_tweet_ids:
                replied_to_id = tweets_replies["globalObjects"]["tweets"][tid].get("in_reply_to_status_id_str", None)
                if replied_to_id is None:
                    continue
                replied_tweet_obj = await self.tweet_raw(replied_to_id, 50)
                if "globalObjects" not in replied_tweet_obj:
                    continue
                if replied_to_id not in replied_tweet_obj["globalObjects"]["tweets"]:
                    continue
                replied_tweet = replied_tweet_obj["globalObjects"]["tweets"][replied_to_id]
                if not replied_tweet["conversation_id_str"] in replied_tweet_obj["globalObjects"]["tweets"]:
                    continue
                conversation_tweet = replied_tweet_obj["globalObjects"]["tweets"][replied_tweet["conversation_id_str"]]
                if conversation_tweet["user_id_str"] == user_id:
                    continue
                if replied_tweet["reply_count"] > 500:
                    continue

                debug('Tban: ')
                debug('Found:' + tid + '\n')
                debug('In reply to:' + replied_to_id + '\n')

                reference_session = next_session()
                reference_session = self
                if reference_session is None:
                    debug('No reference session')
                    return

                global account_index
                account_index += 1

                before_barrier = await reference_session.tweet_raw(replied_to_id, 1000)
                if get_nested(before_barrier, ["globalObjects", "tweets"]) is None:
                    debug('notweets\n')
                    return

                if tid in self.get_ordered_tweet_ids(before_barrier):
                    return {"ban": False, "tweet": tid, "in_reply_to": replied_to_id}

                cursors = ["ShowMoreThreads", "ShowMoreThreadsPrompt"]
                last_result = before_barrier

                for stage in range(0, 2):
                    entries = [x for x in last_result["timeline"]["instructions"] if "addEntries" in x][0]["addEntries"]["entries"]

                    try:
                        cursor = [x["content"]["operation"]["cursor"]["value"] for x in entries if get_nested(x, ["content", "operation", "cursor", "cursorType"]) == cursors[stage]][0]
                    except (KeyError, IndexError):
                        continue

                    after_barrier = await reference_session.tweet_raw(replied_to_id, 1000, cursor=cursor)

                    if get_nested(after_barrier, ["globalObjects", "tweets"]) is None:
                        debug('retinloop\n')
                        return
                    ids_after_barrier = self.get_ordered_tweet_ids(after_barrier)
                    if tid in self.get_ordered_tweet_ids(after_barrier):
                        return {"ban": True, "tweet": tid, "stage": stage, "in_reply_to": replied_to_id}
                    last_result = after_barrier

                # happens when replied_to_id tweet has been deleted
                debug('outer loop return\n')
                return
        except:
            debug('Unexpected Exception in test_barrier:\n')
            debug(traceback.format_exc())

    async def test(self, username, more_replies_test=True):
        result = {"timestamp": time.time()}
        profile = {}
        profile_raw = await self.profile_raw(username)
        debug('Testing ' + str(username))
        if is_another_error(profile_raw, [50, 63]):
            debug("Other error:" + str(username))
            raise UnexpectedApiError

        try:
            user_id = str(profile_raw["id"])
        except KeyError:
            user_id = None

        try:
            profile["screen_name"] = profile_raw["screen_name"]
        except KeyError:
            profile["screen_name"] = username
        try:
            profile["restriction"] = profile_raw["profile_interstitial_type"]
        except KeyError:
            pass
        if profile.get("restriction", None) == "":
            del profile["restriction"]
        try:
            profile["protected"] = profile_raw["protected"]
        except KeyError:
            pass
        profile["exists"] = not is_error(profile_raw, 50)
        suspended = is_error(profile_raw, 63)
        if suspended:
            profile["suspended"] = suspended
        try:
            profile["has_tweets"] = int(profile_raw["statuses_count"]) > 0
        except KeyError:
            profile["has_tweets"] = False

        result["profile"] = profile

        if not profile["exists"] or profile.get("suspended", False) or profile.get("protected", False) or not profile.get('has_tweets'):
            return result

        result["tests"] = {}

        search_raw = await self.search_raw("from:@" + username)

        result["tests"]["search"] = False
        try:
            tweets = search_raw["globalObjects"]["tweets"]
            for tweet_id, tweet in sorted(tweets.items(), key=lambda t: t[1]["id"], reverse=True):
                result["tests"]["search"] = str(tweet_id)
                break

        except (KeyError, IndexError):
            pass

        typeahead_raw = await self.typeahead_raw("@" + username)
        result["tests"]["typeahead"] = False
        try:
            result["tests"]["typeahead"] = len([1 for user in typeahead_raw["users"] if user["screen_name"].lower() == username.lower()]) > 0
        except KeyError:
            pass

        if "search" in result["tests"] and result["tests"]["search"] == False:
            result["tests"]["ghost"] = await self.test_ghost_ban(user_id)
        else:
            result["tests"]["ghost"] = {"ban": False}

        if more_replies_test and not get_nested(result, ["tests", "ghost", "ban"], False):
            result["tests"]["more_replies"] = await self.test_barrier(user_id)

        debug('Writing result for ' + result['profile']['screen_name'] + ' to DB')
        db.write_result(result)
        return result


    async def close(self):
        await self._session.close()

def debug(message):
    if message.endswith('\n') is False:
        message = message + '\n'

    if debug_file is not None:
        debug_file.write(message)
        debug_file.flush()
    else:
        print(message)

def log(message):
    # ensure newline
    if message.endswith('\n') is False:
         message = message + '\n'

    if log_file is not None:
        log_file.write(message)
        log_file.flush()
    else:
        print(message)

def print_session_info(sessions):
    text = ""
    for session in sessions:
        text += "\n%6d %5d %9d %5d" % (int(session.locked), session.limit, session.remaining, session.reset - int(time.time()))
    return text

@routes.get('/.stats')
async def stats(request):
    text = "--- GUEST SESSIONS ---\n\nLocked Limit Remaining Reset"
    text += print_session_info(guest_sessions)
    text += "\n\n\n--- ACCOUNTS ---\n\nLocked Limit Remaining Reset"
    text += print_session_info(account_sessions)
    return web.Response(text=text)

@routes.get('/.unlocked/{screen_name}')
async def unlocked(request):
    screen_name = request.match_info['screen_name']
    text = "Not unlocked"
    for session in account_sessions:
        if session.username.lower() != screen_name.lower():
            continue
        session.locked = False
        text = "Unlocked"
    return web.Response(text=text)


@routes.get('/{screen_name}')
async def api(request):
    global test_index
    screen_name = request.match_info['screen_name']
    session = guest_sessions[test_index % len(guest_sessions)]
    test_index += 1
    result = await session.test(screen_name)
    log(json.dumps(result) + '\n')
    if (args.cors_allow is not None):
        return web.json_response(result, headers={"Access-Control-Allow-Origin": args.cors_allow})
    else:
        return web.json_response(result)

async def login_accounts(accounts, cookie_dir=None):
    if cookie_dir is not None and not os.path.isdir(cookie_dir):
        os.mkdir(cookie_dir, 0o700)
    coroutines = []
    for acc in accounts:
        session = TwitterSession()
        coroutines.append(session.login(*acc, cookie_dir=cookie_dir))
        account_sessions.append(session)
    await asyncio.gather(*coroutines)

async def login_guests():
    for i in range(0, guest_session_pool_size):
        session = TwitterSession()
        guest_sessions.append(session)
    await asyncio.gather(*[s.login() for s in guest_sessions])
    log("Guest sessions created")

def ensure_dir(path):
    if os.path.isdir(path) is False:
        print('Creating directory %s' % path)
        os.mkdir(path)

parser = argparse.ArgumentParser(description='Twitter Shadowban Tester')
parser.add_argument('--account-file', type=str, default='.htaccounts', help='json file with reference account credentials')
parser.add_argument('--cookie-dir', type=str, default=None, help='directory for session account storage')
parser.add_argument('--log', type=str, default=None, help='log file where test results are written to')
parser.add_argument('--daemon', action='store_true', help='run in background')
parser.add_argument('--debug', type=str, default=None, help='debug log file')
parser.add_argument('--port', type=int, default=8080, help='port which to listen on')
parser.add_argument('--host', type=str, default='127.0.0.1', help='hostname/ip which to listen on')
parser.add_argument('--mongo-host', type=str, default='localhost', help='hostname or IP of mongoDB service to connect to')
parser.add_argument('--mongo-port', type=int, default=27017, help='port of mongoDB service to connect to')
parser.add_argument('--mongo-db', type=str, default='tester', help='name of mongo database to use')
parser.add_argument('--twitter-auth-key', type=str, default=None, help='auth key for twitter guest session', required=True)
parser.add_argument('--cors-allow', type=str, default=None, help='value for Access-Control-Allow-Origin header')
args = parser.parse_args()

TwitterSession.twitter_auth_key = args.twitter_auth_key

if (args.cors_allow is None):
    debug('[CORS] Running without CORS headers')
else:
    debug('[CORS] Allowing requests from: ' + args.cors_allow)

ensure_dir(args.cookie_dir)

with open(args.account_file, "r") as f:
    accounts = json.loads(f.read())

if args.log is not None:
    print("Logging test results to %s" % args.log)
    log_dir = os.path.dirname(args.log)
    ensure_dir(log_dir)
    log_file = open(args.log, "a")

if args.debug is not None:
    print("Logging debug output to %s" % args.debug)
    debug_dir = os.path.dirname(args.debug)
    ensure_dir(debug_dir)
    debug_file = open(args.debug, "a")

def run():
    global db
    db = connect(host=args.mongo_host, port=args.mongo_port)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(login_accounts(accounts, args.cookie_dir))
    loop.run_until_complete(login_guests())
    app = web.Application()
    app.add_routes(routes)
    web.run_app(app, host=args.host, port=args.port)

if args.daemon:
    with daemon.DaemonContext():
        run()
else:
    run()
