import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import obspython as obs


DEFAULTS = {
    "api_key": "",
    "xuid": "",
    "filter_target_scene": "gaming",
    "status_source": "XboxStatus",
    "achievement_source": "XboxAchievement",
    "clip_browser_source": "XboxClips",
    "game_art_browser_source": "XboxGameArt",
    "poll_seconds": 60,
    "media_poll_seconds": 300,
    "achievement_alert_seconds": 8,
    "requests_per_hour": 120,
    "debug_presence": False,
    "enable_achievements": True,
    "enable_clips": True,
    "enable_rich_presence": True,
    "move_template_filter_name": "Xbox Move Template",
    "profile_path": "/v2/account",
    "presence_path": "/v2/presence/{xuid}",
    "achievements_path": "/v2/achievements",
    "clips_path": "/v2/dvr/gameclips",
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "twitch_art_width": 1920,
    "twitch_art_height": 1080,
}

SETTINGS = dict(DEFAULTS)
RESULTS = queue.Queue()
STOP = threading.Event()

WORKER = None
LAST_GAME = None
LAST_TRIGGER = None
SEEN_ACHIEVEMENTS = None
LAST_CLIP_URL = None
LAST_GAME_ART_URL = None
ALERT_QUEUE = []
ALERT_UNTIL = 0.0
TWITCH_ART_CACHE = {}
ARTWORK_REQUESTS = set()
TWITCH_TOKEN = None
TWITCH_TOKEN_EXPIRES = 0.0


# ---------------------------------------------------------------------------
# Shared rate limiter: maximum OpenXBL requests per rolling hour
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, requests_per_hour):
        self.limit = max(1, int(requests_per_hour))
        self.window_seconds = 3600.0
        self.requests = deque()
        self.lock = threading.Lock()

    def update_limit(self, requests_per_hour):
        self.limit = max(1, int(requests_per_hour))

    def acquire(self):
        while not STOP.is_set():
            now = time.monotonic()

            with self.lock:
                while (
                    self.requests
                    and now - self.requests[0] >= self.window_seconds
                ):
                    self.requests.popleft()

                if len(self.requests) < self.limit:
                    self.requests.append(now)
                    return

                wait_time = (
                    self.requests[0]
                    + self.window_seconds
                    - now
                )

            if wait_time > 0:
                STOP.wait(min(wait_time, 5.0))


RATE_LIMITER = RateLimiter(DEFAULTS["requests_per_hour"])


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get_json(url, headers=None, timeout=12):
    request_headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "User-Agent": "ViralisTV-OBS-Xbox/2.0",
    }

    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url,
        headers=request_headers,
    )

    print("HTTP request: {}".format(url))

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        print(
            "HTTP error {} for {}".format(
                error.code,
                url
            )
        )
        raise RuntimeError(
            "HTTP {} for {}".format(
                error.code,
                url
            )
        ) from error


def http_post_form(url, data, timeout=12):
    encoded = urllib.parse.urlencode(data).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Origin": "https://id.twitch.tv",
        "Referer": "https://id.twitch.tv/",
    }

    request = urllib.request.Request(
        url,
        data=encoded,
        headers=headers,
    )

    print("HTTP request: {}".format(url))

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        error_body = ""

        try:
            error_body = error.read().decode("utf-8", "ignore")
        except Exception:
            pass

        print(
            "HTTP error {} for {}\n{}".format(
                error.code,
                url,
                error_body
            )
        )

        raise RuntimeError(
            "HTTP {} for {}: {}".format(
                error.code,
                url,
                error_body
            )
        ) from error


# ---------------------------------------------------------------------------
# OpenXBL API helpers — every request uses the shared limiter
# ---------------------------------------------------------------------------

def api_get(path, config):
    key = config["api_key"].strip()

    if not key:
        raise ValueError("Enter your OpenXBL API key in script settings.")

    RATE_LIMITER.acquire()

    if STOP.is_set():
        raise RuntimeError("Script is shutting down.")

    return http_get_json(
        "https://api.xbl.io/" + path.lstrip("/"),
        headers={
            "X-Authorization": key,
        }
    )


def dictionaries(value):
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from dictionaries(child)

    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def resolve_xuid(config):
    if config["xuid"]:
        return config["xuid"]

    account = api_get(
        config["profile_path"],
        config
    )

    for item in dictionaries(account):
        value = item.get("xuid") or item.get("XUID")

        if value:
            return str(value)

    users = account.get("profileUsers", [])

    if users and users[0].get("id"):
        return str(users[0]["id"])

    raise ValueError(
        "Could not resolve XUID. Enter it in script settings."
    )


def worker_loop():
    cached_identity = None
    cached_xuid = None
    next_presence = 0.0
    next_media = 0.0

    while not STOP.is_set():
        config = dict(SETTINGS)
        now = time.monotonic()

        identity = (
            config["api_key"],
            config["xuid"],
        )

        if identity != cached_identity:
            cached_identity = identity
            cached_xuid = None
            next_presence = 0.0
            next_media = 0.0

        if not config["api_key"]:
            STOP.wait(1)
            continue

        if now >= next_presence:
            next_presence = now + config["poll_seconds"]

            try:
                if not cached_xuid:
                    cached_xuid = resolve_xuid(config)

                path = config["presence_path"].replace(
                    "{xuid}",
                    urllib.parse.quote(cached_xuid, safe="")
                )

                payload = api_get(path, config)
                RESULTS.put(("presence", payload))

            except Exception as error:
                RESULTS.put((
                    "error",
                    "Presence: {}".format(error)
                ))

        if STOP.is_set():
            break

        if now >= next_media:
            next_media = now + config["media_poll_seconds"]

            tasks = (
                (
                    "achievements",
                    "enable_achievements",
                    "achievements_path",
                ),
                (
                    "clips",
                    "enable_clips",
                    "clips_path",
                ),
            )

            for kind, enabled_key, path_key in tasks:
                if STOP.is_set():
                    break

                if not config[enabled_key]:
                    continue

                try:
                    path = config[path_key].replace(
                        "{xuid}",
                        urllib.parse.quote(cached_xuid or "", safe="")
                    )

                    RESULTS.put((
                        kind,
                        api_get(path, config)
                    ))

                except Exception as error:
                    RESULTS.put((
                        "error",
                        "{}: {}".format(kind, error)
                    ))

        STOP.wait(0.25)


# ---------------------------------------------------------------------------
# Twitch artwork
# ---------------------------------------------------------------------------

def get_twitch_token(config):
    """Get or refresh a Twitch app access token."""
    global TWITCH_TOKEN, TWITCH_TOKEN_EXPIRES

    client_id = config["twitch_client_id"].strip()
    client_secret = config["twitch_client_secret"].strip()

    if not client_id or not client_secret:
        raise ValueError(
            "Enter your Twitch Client ID and Client Secret."
        )

    now = time.monotonic()

    if TWITCH_TOKEN and now < TWITCH_TOKEN_EXPIRES:
        return TWITCH_TOKEN

    token_url = "https://id.twitch.tv/oauth2/token"
    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }

    print("Requesting Twitch app access token.")

    try:
        import requests

        response = requests.post(
            token_url,
            data=token_data,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            },
            timeout=12,
        )

        print(
            "Twitch token response: {} {}".format(
                response.status_code,
                response.text
            )
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Twitch token request failed: {} {}".format(
                    response.status_code,
                    response.text
                )
            )

        payload = response.json()

    except ImportError:
        print(
            "The requests module is unavailable; using urllib."
        )
        payload = http_post_form(
            token_url,
            token_data
        )

    token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)

    if not token:
        raise ValueError(
            "Twitch did not return an access token."
        )

    TWITCH_TOKEN = token
    TWITCH_TOKEN_EXPIRES = now + max(
        60,
        int(expires_in) - 60
    )

    print("Obtained Twitch app access token.")

    return TWITCH_TOKEN


def clean_twitch_search_name(game):
    """Create a clean Twitch game search name."""
    name = normalize_game_name(game)

    for character in (":", "-", "!", "?", ",", ".", "(", ")", "[", "]"):
        name = name.replace(character, " ")

    return " ".join(name.split())


def normalize_game_name(game):
    name = game.casefold()

    for symbol in ("™", "®", "©"):
        name = name.replace(symbol, "")

    return " ".join(name.split())


def request_twitch_artwork(game, title_id):
    """Get Twitch box art for the active Xbox game."""
    global ARTWORK_REQUESTS

    cache_key = title_id or normalize_game_name(game)

    if cache_key in TWITCH_ART_CACHE:
        return TWITCH_ART_CACHE[cache_key]

    if cache_key in ARTWORK_REQUESTS:
        return None

    ARTWORK_REQUESTS.add(cache_key)

    client_id = SETTINGS["twitch_client_id"].strip()

    if not client_id:
        print(
            "Twitch Client ID is not set; game art will not update."
        )
        return None

    try:
        token = get_twitch_token(SETTINGS)

        original_name = game.strip()
        cleaned_name = clean_twitch_search_name(game)
        wanted = normalize_game_name(game)

        width = max(
            1,
            int(SETTINGS["twitch_art_width"])
        )
        height = max(
            1,
            int(SETTINGS["twitch_art_height"])
        )

        # 1. Try the exact Xbox game name first.
        candidates = [original_name]

        # 2. Try the cleaned name, such as "ark survival ascended".
        if cleaned_name.casefold() != original_name.casefold():
            candidates.append(cleaned_name)

        for search_name in candidates:
            encoded_name = urllib.parse.quote(search_name, safe="")

            url = (
                "https://api.twitch.tv/helix/games?name={}".format(
                    encoded_name
                )
            )

            payload = http_get_json(
                url,
                headers={
                    "Client-ID": client_id,
                    "Authorization": "Bearer {}".format(token),
                }
            )

            games = payload.get("data", []) or []

            if games:
                box_art = games[0].get("box_art_url")

                if box_art:
                    art_url = box_art.replace(
                        "{width}x{height}",
                        "{}x{}".format(width, height)
                    )

                    TWITCH_ART_CACHE[cache_key] = art_url

                    print(
                        "Found Twitch artwork for: {}".format(
                            game
                        )
                    )
                    print(
                        "Twitch artwork URL: {}".format(art_url)
                    )

                    return art_url

        # 3. Use Twitch Search Categories for partial matching.
        encoded_query = urllib.parse.quote(cleaned_name, safe="")

        search_url = (
            "https://api.twitch.tv/helix/search/categories?query={}".format(
                encoded_query
            )
        )

        payload = http_get_json(
            search_url,
            headers={
                "Client-ID": client_id,
                "Authorization": "Bearer {}".format(token),
            }
        )

        results = payload.get("data", []) or []

        if not results:
            print(
                "No Twitch category found for: {}".format(
                    cleaned_name
                )
            )
            return None

        selected = None
        best_score = -1

        for result in results:
            twitch_name = normalize_game_name(
                str(result.get("name", ""))
            )

            score = 0

            if twitch_name == wanted:
                score = 100
            elif wanted in twitch_name:
                score = 75
            elif twitch_name in wanted:
                score = 50
            else:
                wanted_words = set(wanted.split())
                twitch_words = set(twitch_name.split())

                overlap = len(
                    wanted_words & twitch_words
                )

                if overlap:
                    score = overlap * 5

            if score > best_score:
                best_score = score
                selected = result

        if not selected:
            print(
                "No matching Twitch category for: {}".format(
                    cleaned_name
                )
            )
            return None

        box_art = selected.get("box_art_url")

        if not box_art:
            print(
                "Twitch category has no box art: {}".format(
                    selected.get("name")
                )
            )
            return None

        art_url = box_art.replace(
            "{width}x{height}",
            "{}x{}".format(width, height)
        )

        TWITCH_ART_CACHE[cache_key] = art_url

        print(
            "Found Twitch artwork for: {}".format(
                game
            )
        )
        print(
            "Twitch category match: {}".format(
                selected.get("name")
            )
        )
        print(
            "Twitch artwork URL: {}".format(art_url)
        )

        return art_url

    except Exception as error:
        print(
            "Could not load Twitch artwork for {}: {}".format(
                game,
                error
            )
        )
        return None


# ---------------------------------------------------------------------------
# OBS source creation and updates
# ---------------------------------------------------------------------------

def ensure_source(name, source_type, settings_data=None):
    """Return an existing OBS source, or create it and add it to the target scene."""
    if not name:
        return None

    existing = obs.obs_get_source_by_name(name)

    if existing:
        return existing

    data = obs.obs_data_create()

    try:
        if settings_data:
            obs.obs_data_apply(data, settings_data)

        source = obs.obs_source_create(
            source_type,
            name,
            data,
            None
        )
    finally:
        obs.obs_data_release(data)

    if not source:
        print(
            "Failed to create {} source: {}".format(
                source_type,
                name
            )
        )
        return None

    scene_name = SETTINGS["filter_target_scene"]
    scene_source = obs.obs_get_source_by_name(scene_name)

    if not scene_source:
        print(
            "Created source '{}', but scene '{}' was not found.".format(
                name,
                scene_name
            )
        )
        obs.obs_source_release(source)
        return source

    try:
        scene = obs.obs_scene_from_source(scene_source)

        if not scene:
            print(
                "'{}' is not a scene; source '{}' was created "
                "but not added.".format(
                    scene_name,
                    name
                )
            )
            obs.obs_source_release(source)
            return source

        item = obs.obs_scene_add(scene, source)

        if item:
            print(
                "Created and added source '{}' to scene '{}'.".format(
                    name,
                    scene_name
                )
            )
        else:
            print(
                "Created source '{}', but could not add it to scene '{}'.".format(
                    name,
                    scene_name
                )
            )

    finally:
        obs.obs_source_release(scene_source)
        obs.obs_source_release(source)

    return obs.obs_get_source_by_name(name)


def ensure_text_source(name):
    """Create a Text (GDI+) source if it does not already exist."""
    settings_data = obs.obs_data_create()

    try:
        obs.obs_data_set_string(settings_data, "text", "")
        obs.obs_data_set_bool(settings_data, "outline", True)
        obs.obs_data_set_int(settings_data, "outline_size", 3)
        obs.obs_data_set_int(settings_data, "outline_color", 4278190080)
        obs.obs_data_set_int(settings_data, "color", 4294967295)
    finally:
        obs.obs_data_release(settings_data)

    return ensure_source(
        name,
        "text_gdiplus",
        settings_data
    )


def ensure_browser_source(name):
    """Create a Browser source if it does not already exist."""
    settings_data = obs.obs_data_create()

    try:
        obs.obs_data_set_bool(settings_data, "is_local_file", False)
        obs.obs_data_set_string(settings_data, "url", "about:blank")
        obs.obs_data_set_int(settings_data, "width", 1920)
        obs.obs_data_set_int(settings_data, "height", 1080)
    finally:
        obs.obs_data_release(settings_data)

    return ensure_source(
        name,
        "browser_source",
        settings_data
    )


def set_text(name, text):
    """Update a text source, creating it first if necessary."""
    if not name:
        return False

    source = ensure_text_source(name)

    if not source:
        return False

    data = obs.obs_data_create()

    try:
        obs.obs_data_set_string(data, "text", text)
        obs.obs_source_update(source, data)
        return True

    finally:
        obs.obs_data_release(data)
        obs.obs_source_release(source)


def set_browser_url(name, url):
    """Update a browser source, creating it first if necessary."""
    if not name:
        return False

    source = ensure_browser_source(name)

    if not source:
        return False

    data = obs.obs_data_create()

    try:
        obs.obs_data_set_bool(data, "is_local_file", False)
        obs.obs_data_set_string(data, "url", url)
        obs.obs_source_update(source, data)
        return True

    finally:
        obs.obs_data_release(data)
        obs.obs_source_release(source)


# ---------------------------------------------------------------------------
# Move Action scene filters
# ---------------------------------------------------------------------------

def resolve_move_action_type():
    """Read the installed filter type from the manually created template."""
    scene_name = SETTINGS["filter_target_scene"]
    template_name = SETTINGS["move_template_filter_name"]

    scene_source = obs.obs_get_source_by_name(scene_name)

    if not scene_source:
        print("Scene not found: '{}'".format(scene_name))
        return None

    try:
        template = obs.obs_source_get_filter_by_name(
            scene_source,
            template_name
        )

        if not template:
            print(
                "Add a Move Action filter named '{}' "
                "to scene '{}'.".format(
                    template_name,
                    scene_name
                )
            )
            return None

        try:
            type_id = obs.obs_source_get_id(template)

            if not type_id:
                print("Could not read the Move Action filter type.")
                return None

            type_id = str(type_id)

            print(
                "Using Move Action filter type: {}".format(type_id)
            )

            return type_id

        finally:
            obs.obs_source_release(template)

    finally:
        obs.obs_source_release(scene_source)


def ensure_filter(scene_source, filter_name):
    """Return an existing filter or create a new Move Action filter."""
    existing = obs.obs_source_get_filter_by_name(
        scene_source,
        filter_name
    )

    if existing:
        return existing

    type_id = resolve_move_action_type()

    if not type_id:
        return None

    data = obs.obs_data_create()

    try:
        new_filter = obs.obs_source_create_private(
            type_id,
            filter_name,
            data
        )
    finally:
        obs.obs_data_release(data)

    if not new_filter:
        print("Failed to create filter: {}".format(filter_name))
        return None

    try:
        # Initialize only a NEW filter inactive before attaching it.
        # Existing filters are never disabled or reset.
        obs.obs_source_set_enabled(new_filter, False)
        obs.obs_source_filter_add(scene_source, new_filter)
    finally:
        obs.obs_source_release(new_filter)

    result = obs.obs_source_get_filter_by_name(
        scene_source,
        filter_name
    )

    if result:
        print(
            "Created Move Action scene filter: {}".format(
                filter_name
            )
        )
        print(
            "Configure its action in the scene's Filters window. "
            "A new filter has no game-specific action configured."
        )
    else:
        print(
            "Could not attach filter: {}".format(filter_name)
        )

    return result


def activate_filter(filter_name):
    scene_name = SETTINGS["filter_target_scene"]
    scene_source = obs.obs_get_source_by_name(scene_name)

    if not scene_source:
        print("Scene not found: '{}'".format(scene_name))
        return False

    try:
        if not obs.obs_scene_from_source(scene_source):
            print("'{}' is not a scene.".format(scene_name))
            return False

        filter_source = ensure_filter(scene_source, filter_name)

        if not filter_source:
            return False

        try:
            obs.obs_source_set_enabled(filter_source, True)
        finally:
            obs.obs_source_release(filter_source)

        print(
            "Activated Move Action scene filter: {}".format(
                filter_name
            )
        )
        return True

    finally:
        obs.obs_source_release(scene_source)


# ---------------------------------------------------------------------------
# Presence parsing and game-trigger logic
# ---------------------------------------------------------------------------

def parse_presence(payload):
    content = payload.get("content", payload)

    if not isinstance(content, dict):
        raise ValueError("Unexpected presence response structure.")

    state = str(content.get("state", "Unknown"))

    if state.casefold() == "offline":
        return state, None, "", "", None

    device_names = {
        "scarlett": "Xbox Series X|S",
        "xboxone": "Xbox One",
        "xbox one": "Xbox One",
        "windowsonecore": "Windows",
        "win32": "Windows",
        "android": "Android",
        "ios": "iOS",
    }

    candidates = []
    device_name = ""

    for device in content.get("devices", []):
        raw_device = str(
            device.get("name", "")
            or device.get("deviceType", "")
            or device.get("type", "")
        ).strip()

        if raw_device and not device_name:
            device_name = device_names.get(
                raw_device.casefold(),
                raw_device
            )

        for title in device.get("titles", []):
            name = str(title.get("name", "")).strip()

            if not name:
                continue

            if name.casefold() in {
                "home",
                "online",
                "dashboard",
                "xbox dashboard",
            }:
                continue

            if str(title.get("state", "")).casefold() != "active":
                continue

            foreground = (
                str(title.get("placement", "")).casefold() == "full"
            )

            title_id = (
                title.get("id")
                or title.get("titleId")
                or title.get("titleID")
            )

            candidates.append((
                foreground,
                str(title.get("lastModified", "")),
                title,
                title_id,
            ))

    if not candidates:
        return state, None, "", device_name, None

    candidates.sort(
        key=lambda item: (item[0], item[1]),
        reverse=True
    )

    title = candidates[0][2]
    title_id = candidates[0][3]

    activity = title.get("activity", {})

    if not isinstance(activity, dict):
        activity = {}

    return (
        state,
        str(title["name"]).strip(),
        str(activity.get("richPresence", "")).strip(),
        device_name,
        str(title_id) if title_id else None,
    )


def filter_name_for_game(game):
    name = game.casefold()

    for symbol in ("™", "®", "©"):
        name = name.replace(symbol, "")

    name = " ".join(name.split())

    if name.startswith("ark"):
        name = "ark"

    return "xbox " + name


def handle_presence(payload):
    global LAST_GAME, LAST_TRIGGER, LAST_GAME_ART_URL

    if SETTINGS["debug_presence"]:
        print(
            "OpenXBL presence JSON: "
            + json.dumps(payload, ensure_ascii=False)
        )

    state, game, activity, device, title_id = parse_presence(payload)

    text = "XBOX: {}".format(state)

    if device:
        text += "\nDEVICE: {}".format(device)

    text += "\nNOW PLAYING: {}".format(
        game or "Not playing"
    )

    if activity:
        text += "\n{}".format(activity)

    set_text(SETTINGS["status_source"], text)

    if not game:
        LAST_GAME = None
        LAST_TRIGGER = None
        LAST_GAME_ART_URL = None

        if SETTINGS["enable_rich_presence"]:
            set_browser_url(
                SETTINGS["game_art_browser_source"],
                "about:blank"
            )

        print("No game detected; scene filters untouched.")
        return

    art_url = None

    if SETTINGS["enable_rich_presence"]:
        cache_key = title_id or normalize_game_name(game)

        if cache_key in TWITCH_ART_CACHE:
            art_url = TWITCH_ART_CACHE[cache_key]
        else:
            art_url = request_twitch_artwork(
                game,
                title_id
            )

        if art_url and art_url != LAST_GAME_ART_URL:
            if set_browser_url(
                SETTINGS["game_art_browser_source"],
                art_url
            ):
                LAST_GAME_ART_URL = art_url
                print(
                    "Updated Xbox game art: {}".format(
                        game
                    )
                )

    filter_name = filter_name_for_game(game)
    game_key = game.casefold()

    if game_key != LAST_GAME:
        LAST_TRIGGER = None

    if LAST_TRIGGER != filter_name:
        print(
            "Detected game: {} -> {}".format(
                game,
                filter_name
            )
        )

        if activate_filter(filter_name):
            LAST_TRIGGER = filter_name

    LAST_GAME = game_key


# ---------------------------------------------------------------------------
# Achievement alerts
# ---------------------------------------------------------------------------

def handle_achievements(payload):
    global SEEN_ACHIEVEMENTS

    unlocked = {}

    for item in dictionaries(payload):
        state = str(item.get("progressState", "")).casefold()

        earned = (
            state in {"achieved", "unlocked", "completed"}
            or item.get("isUnlocked") is True
            or item.get("unlocked") is True
        )

        achievement_id = item.get("achievementId") or item.get("id")
        name = item.get("name") or item.get("achievementName")

        if not earned or not achievement_id or not name:
            continue

        associations = item.get("titleAssociations") or []
        title_id = str(item.get("titleId", ""))

        if associations and isinstance(associations[0], dict):
            title_id = str(associations[0].get("id", title_id))

        key = (
            title_id,
            str(achievement_id),
            str(name),
        )

        points = item.get("gamerscore") or item.get("points")

        for reward in item.get("rewards", []) or []:
            if str(reward.get("type", "")).casefold() == "gamerscore":
                points = reward.get("value")

        score = " — {}G".format(points) if points else ""

        unlocked[key] = (
            "ACHIEVEMENT UNLOCKED\n{}{}".format(
                name,
                score
            )
        )

    # First successful response is a baseline, not an unlock alert.
    if SEEN_ACHIEVEMENTS is None:
        SEEN_ACHIEVEMENTS = set(unlocked)
        print("Achievement baseline initialized.")
        return

    for key, text in unlocked.items():
        if key not in SEEN_ACHIEVEMENTS:
            ALERT_QUEUE.append(text)

    SEEN_ACHIEVEMENTS.update(unlocked)


def update_alert():
    global ALERT_UNTIL

    now = time.monotonic()

    if ALERT_UNTIL and now >= ALERT_UNTIL:
        set_text(SETTINGS["achievement_source"], "")
        ALERT_UNTIL = 0.0

    if not ALERT_UNTIL and ALERT_QUEUE:
        set_text(
            SETTINGS["achievement_source"],
            ALERT_QUEUE.pop(0)
        )
        ALERT_UNTIL = now + SETTINGS["achievement_alert_seconds"]


# ---------------------------------------------------------------------------
# Latest clip
# ---------------------------------------------------------------------------

def handle_clips(payload):
    global LAST_CLIP_URL

    clips = [
        item for item in dictionaries(payload)
        if item.get("gameClipId") and item.get("gameClipUris")
    ]

    if not clips:
        return

    clips.sort(
        key=lambda item: str(
            item.get("dateRecorded")
            or item.get("datePublished")
            or ""
        ),
        reverse=True
    )

    for uri in clips[0].get("gameClipUris", []):
        url = uri.get("uri")

        if not isinstance(url, str) or not url.startswith("https://"):
            continue

        if url != LAST_CLIP_URL:
            if set_browser_url(
                SETTINGS["clip_browser_source"],
                url
            ):
                LAST_CLIP_URL = url
                print("Updated latest Xbox clip.")

        break


# ---------------------------------------------------------------------------
# OBS callbacks
# ---------------------------------------------------------------------------

def tick():
    while True:
        try:
            kind, payload = RESULTS.get_nowait()
        except queue.Empty:
            break

        try:
            if kind == "presence":
                handle_presence(payload)
            elif kind == "achievements":
                handle_achievements(payload)
            elif kind == "clips":
                handle_clips(payload)
            else:
                print(
                    "OpenXBL error: {}".format(payload)
                )

        except Exception as error:
            print(
                "OpenXBL {} processing error: {}".format(
                    kind,
                    error
                )
            )

    update_alert()


def script_description():
    return (
        "Xbox Live presence, achievements, clips, Twitch game art, "
        "and Move Action scene filters.\n\n"
        "Automatically creates missing XboxStatus, XboxAchievement, "
        "XboxClips and XboxGameArt sources and adds them to the selected "
        "scene.\n\n"
        "Pulls game artwork from Twitch only.\n\n"
        "Creates missing Move Action filters on your selected scene.\n"
        "Examples: xbox ark, xbox battlefield 6.\n"
        "Activates once per game change; no reset/disable timer.\n\n"
        "Create one disabled Move Action filter named:\n"
        "'Xbox Move Template'\n\n"
        "All OpenXBL requests share a limit of 120 requests per hour."
    )


def script_defaults(settings):
    for key, value in DEFAULTS.items():
        if isinstance(value, bool):
            obs.obs_data_set_default_bool(settings, key, value)
        elif isinstance(value, int):
            obs.obs_data_set_default_int(settings, key, value)
        else:
            obs.obs_data_set_default_string(settings, key, value)


def script_properties():
    props = obs.obs_properties_create()

    text_fields = [
        (
            "api_key",
            "OpenXBL API key",
            obs.OBS_TEXT_PASSWORD
        ),
        (
            "xuid",
            "XUID (blank = own account lookup)",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "filter_target_scene",
            "Scene containing sources and filters",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "status_source",
            "Now-playing text source",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "achievement_source",
            "Achievement text source",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "clip_browser_source",
            "Clip browser source",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "game_art_browser_source",
            "Game art browser source",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "move_template_filter_name",
            "Move Action template filter name",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "profile_path",
            "Account endpoint",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "presence_path",
            "Presence endpoint",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "achievements_path",
            "Achievements endpoint",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "clips_path",
            "Clips endpoint",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "twitch_client_id",
            "Twitch Client ID",
            obs.OBS_TEXT_DEFAULT
        ),
        (
            "twitch_client_secret",
            "Twitch Client Secret",
            obs.OBS_TEXT_PASSWORD
        ),
    ]

    for key, label, text_type in text_fields:
        obs.obs_properties_add_text(
            props,
            key,
            label,
            text_type
        )

    obs.obs_properties_add_int(
        props,
        "poll_seconds",
        "Presence interval (seconds)",
        30,
        3600,
        10
    )

    obs.obs_properties_add_int(
        props,
        "media_poll_seconds",
        "Achievements/clips interval (seconds)",
        120,
        3600,
        30
    )

    obs.obs_properties_add_int(
        props,
        "achievement_alert_seconds",
        "Achievement alert duration (seconds)",
        3,
        60,
        1
    )

    obs.obs_properties_add_int(
        props,
        "requests_per_hour",
        "Maximum OpenXBL requests per hour",
        1,
        10000,
        1
    )

    obs.obs_properties_add_int(
        props,
        "twitch_art_width",
        "Twitch artwork width",
        100,
        5000,
        10
    )

    obs.obs_properties_add_int(
        props,
        "twitch_art_height",
        "Twitch artwork height",
        100,
        5000,
        10
    )

    obs.obs_properties_add_bool(
        props,
        "debug_presence",
        "Log full presence JSON"
    )

    obs.obs_properties_add_bool(
        props,
        "enable_achievements",
        "Poll achievements"
    )

    obs.obs_properties_add_bool(
        props,
        "enable_clips",
        "Poll clips"
    )

    obs.obs_properties_add_bool(
        props,
        "enable_rich_presence",
        "Enable Twitch game art"
    )

    return props


def script_update(settings):
    global SETTINGS, LAST_GAME, LAST_TRIGGER, SEEN_ACHIEVEMENTS
    global TWITCH_ART_CACHE, ARTWORK_REQUESTS
    global TWITCH_TOKEN, TWITCH_TOKEN_EXPIRES

    updated = {}

    for key, default in DEFAULTS.items():
        if isinstance(default, bool):
            updated[key] = obs.obs_data_get_bool(settings, key)
        elif isinstance(default, int):
            updated[key] = obs.obs_data_get_int(settings, key)
        else:
            updated[key] = obs.obs_data_get_string(
                settings,
                key
            ).strip()

    updated["poll_seconds"] = max(
        30,
        updated["poll_seconds"]
    )

    updated["media_poll_seconds"] = max(
        120,
        updated["media_poll_seconds"]
    )

    updated["achievement_alert_seconds"] = max(
        3,
        updated["achievement_alert_seconds"]
    )

    updated["requests_per_hour"] = max(
        1,
        updated["requests_per_hour"]
    )

    updated["twitch_art_width"] = max(
        100,
        updated["twitch_art_width"]
    )

    updated["twitch_art_height"] = max(
        100,
        updated["twitch_art_height"]
    )

    if any(
        updated[key] != SETTINGS[key]
        for key in (
            "api_key",
            "xuid",
            "filter_target_scene",
            "move_template_filter_name",
            "twitch_client_id",
            "twitch_client_secret",
            "twitch_art_width",
            "twitch_art_height",
        )
    ):
        LAST_GAME = None
        LAST_TRIGGER = None
        SEEN_ACHIEVEMENTS = None
        TWITCH_ART_CACHE = {}
        ARTWORK_REQUESTS = set()
        TWITCH_TOKEN = None
        TWITCH_TOKEN_EXPIRES = 0.0

    SETTINGS = updated

    RATE_LIMITER.update_limit(
        SETTINGS["requests_per_hour"]
    )


def script_load(settings):
    global WORKER

    script_update(settings)
    STOP.clear()

    obs.timer_add(tick, 200)

    WORKER = threading.Thread(
        target=worker_loop,
        name="OpenXBL-Worker",
        daemon=True
    )
    WORKER.start()

    print(
        "Xbox script loaded. Scene: {}".format(
            SETTINGS["filter_target_scene"]
        )
    )


def script_unload():
    global WORKER

    STOP.set()
    obs.timer_remove(tick)

    if WORKER:
        WORKER.join(timeout=15)
        WORKER = None
