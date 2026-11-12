# Xbox Live Presence & Clip Overlay Script

A third-party Python script for OBS Studio. It displays Xbox Live presence, achievement unlocks, and the latest Xbox game clip. It can also automatically create and activate Move Action scene filters when it detects the active Xbox game.

This is not an official OBS Project, Microsoft, Xbox, or OpenXBL resource.

## Features

- Shows Xbox online status and the currently active game
- Shows achievement unlock alerts
- Updates a browser source with the latest Xbox game clip
- Automatically creates missing XboxStatus, XboxAchievement, XboxClips, and XboxGameArt sources
- Adds missing sources to the selected scene
- Automatically creates game-specific Move Action filters
- Activates a matching Move Action filter when a game is detected
- Pulls game artwork from Twitch
- Uses a shared rolling limit of 120 OpenXBL requests per hour
- Does not disable, reset, or overwrite existing Move Action filters

## Requirements

- OBS Studio with Python scripting enabled
- Python 3.11 or 3.12
- An OpenXBL account and Personal API Key: https://xbl.io
- A Twitch Developer application
- The Move plugin: https://obsproject.com/forum/resources/move.913/
- A scene for the overlay sources and Move Action filters

## OpenXBL setup

This script uses OpenXBL, a third-party Xbox Live API service.

1. Go to https://xbl.io.
2. Sign in with the Microsoft account connected to your Xbox profile.
3. Open the OpenXBL dashboard.
4. Create a **Personal API Key**.
5. Copy the key and paste it into the script's **OpenXBL API key** field in OBS.

**Important:** Do not share your OpenXBL API key publicly. Every OpenXBL request must include it in the `X-Authorization` header. OpenXBL and Microsoft both apply rate limits, so avoid setting polling intervals too aggressively.

## Twitch Developer setup

The script uses Twitch artwork for the active Xbox game. It uses the Twitch Client Credentials flow, so you do **not** need to log in to Twitch or provide a user OAuth token.

1. Go to the Twitch Developer Console: https://dev.twitch.tv/console
2. Sign in with your Twitch account.
3. Open **Applications**.
4. Click **Register Your Application**.
5. Enter a name, such as `OBS Xbox Overlay`.
6. Enter these OAuth Redirect URLs:

```text
http://localhost
```

7. Select **Application Integration** as the category.
8. Click **Create**.
9. Open the application and copy the **Client ID**.
10. Click **New Secret**, then copy the **Client Secret**.
11. In OBS, open **Tools > Scripts > xbox.py**.
12. Paste the Client ID into **Twitch Client ID**.
13. Paste the Client Secret into **Twitch Client Secret**.
14. Click **Reload Scripts**.

The script automatically requests and refreshes a Twitch app access token. It uses that token to search Twitch for the active game and retrieve its box art.

### Twitch artwork behavior

The script tries the following in order:

1. The exact Xbox game name
2. A cleaned version of the game name
3. Twitch Search Categories

If Twitch cannot find artwork, the script leaves the current `XboxGameArt` browser source unchanged.

Twitch box art is portrait-oriented cover art sourced from IGDB. It may not fill a 16:9 background perfectly. You can change the requested dimensions in the script settings, but the source image itself remains portrait-oriented.

## Setup

1. Download `xbox.py` from the GitHub repository.
2. In OBS, open **Tools > Scripts**.
3. Click **+** and add `xbox.py`.
4. Get an OpenXBL Personal API Key from https://xbl.io/dashboard, then paste it into the **OpenXBL API key** field.
5. Complete the Twitch Developer setup above and enter your Twitch Client ID and Client Secret.
6. Set **Scene containing sources and filters** to the scene where you want the overlays and filters, for example: `gaming`.
7. Optionally enter an XUID. Leave it blank to use the account associated with your OpenXBL API key.
8. Let the script create `XboxStatus`, `XboxAchievement`, `XboxClips`, and `XboxGameArt`.
9. Position and style the sources in OBS.
10. Create a disabled Move Action filter on the selected scene named: `Xbox Move Template`.
11. Enter `Xbox Move Template` in the Move Action template filter name setting.

## Sources created

| Source | Type | Purpose |
|---|---|---|
| `XboxStatus` | Text (GDI+) | Xbox status and current game |
| `XboxAchievement` | Text (GDI+) | Temporary achievement alert |
| `XboxClips` | Browser | Latest Xbox game clip |
| `XboxGameArt` | Browser | Twitch artwork for the active game |

Example XboxStatus output:

```text
XBOX: Online
DEVICE: Xbox Series X|S
NOW PLAYING: Battlefield™ 6
Main Menu
```

## Move Action filters

The script uses a manually created Move Action filter as a template to identify the installed Move Action filter type. Create one disabled Move Action filter on the selected scene and name it:

```text
Xbox Move Template
```

When the script detects a game, it creates a filter using this format:

```text
xbox <game name>
```

Examples:

```text
xbox ark
xbox battlefield 6
xbox call of duty: black ops 6
xbox forza horizon 5
```

The script activates the matching filter when that game is detected. You must configure the movement or action for each newly created filter in the scene's Filters window. The script does not automatically know what movement you want each game filter to perform.

Existing Move Action filters are never disabled or reset. The script only activates a matching filter.

## Rate limiting

All OpenXBL requests share a rolling limit of 120 requests per hour. This includes presence checks, achievement checks, game-clip checks, and XUID lookups.

With the default settings, the script uses approximately:

- 60 requests per hour for presence
- 12 requests per hour for achievements
- 12 requests per hour for clips
- 84 requests per hour total

You can change the request limit in the script settings. If the limit is reached, the script waits before making another request.

OpenXBL responses include rate-limit headers, and Microsoft may apply additional Xbox Live service limits. If you receive an HTTP 429 response, reduce your polling frequency or wait before retrying.

## AI usage disclosure

This script was developed with assistance from an AI coding assistant for debugging, documentation, and code organization. I reviewed, tested, and maintain the final code.

## License

MIT License. See `LICENSE`.

## Support

Report issues in this repository's Issues tab.
