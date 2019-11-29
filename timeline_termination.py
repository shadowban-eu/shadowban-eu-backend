from aiohttp import ClientSession


class TimelineTermination:
  endpoint = None
  session = ClientSession()

  async def requestTest(tweet_id):
    async with TimelineTermination.session.get(TimelineTermination.endpoint + tweet_id) as response:
      result = await response.json()

    if result["name"] == "APIError" and result["errors"][0]["code"] == "ENOREPLIES":
      result = None

    return result
