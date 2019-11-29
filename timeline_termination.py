from aiohttp import ClientSession


class TimelineTermination:
  endpoint = None
  session = ClientSession()

  async def requestTest(tweet_id, debug):
    async with TimelineTermination.session.get(TimelineTermination.endpoint + tweet_id) as response:
      result = await response.json()

    if result.get("name", None) == "APIError" and result["errors"][0]["code"] == "ENOREPLIES":
      debug('[TimelineTermination] ' + tweet_id + 'has no replies - can not test.')
      return None

    result["tweets"]["subject"] = result["tweets"]["subject"]["tweetId"]
    result["tweets"]["testedWith"] = result["tweets"]["testedWith"]["tweetId"]

    return result
