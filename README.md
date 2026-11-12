# Xbox Live Presence & Clip Overlay Script

A third-party Python script for OBS Studio that displays Xbox Live presence, achievement unlocks, and the latest Xbox game clip. It can automatically create and activate [Move Action](https://github.com/AndreasLehrberg/obs-move-filter) scene filters based on the active Xbox game.

This project is not affiliated with or endorsed by the OBS Project, Microsoft, Xbox, or OpenXBL.

## Features

- Xbox online status and current game
- Achievement unlock alerts
- Latest Xbox game clip in a browser source
- Automatic creation of missing overlay sources
- Automatic creation of game-specific Move Action filters
- Shared rolling rate limit of 120 requests per hour
- Existing Move Action filters are never disabled or reset

## Requirements

- OBS Studio with Python scripting enabled
- Python 3.11 or 3.12
- An [OpenXBL](https://xbl.io) account and Personal API Key
- [Move Action](https://github.com/AndreasLehrberg/obs-move-filter) plugin
- A scene for the overlay sources and Move Action filters

## Get an OpenXBL API key

This script uses [OpenXBL](https://xbl.io), a third-party Xbox Live API service.

1. Go to [https://xbl.io](https://xbl.io).
2. Sign in with the Microsoft account connected to your Xbox profile.
3. Open the [OpenXBL dashboard](https://xbl.io/dashboard).
4. Create a **Personal API Key**.
5. Copy the key and paste it into the script's **OpenXBL API key** field in OBS.

**Important:** Do not share your OpenXBL API key publicly. Every OpenXBL request must include it in the `X-Authorization` header. OpenXBL and Microsoft both apply rate limits, so avoid setting polling intervals too aggressively.

## Setup

1. Download `xbox.py`.
2. In OBS, open **Tools > Scripts**.
3. Click **+** and add `xbox.py`.
4. Get an OpenXBL Personal API Key from the [OpenXBL dashboard](https://xbl.io/dashboard), then paste it into the **OpenXBL API key** field.
5. Set **Scene containing sources and filters** to the scene where you want the overlays and filters, for example: `gaming`.
6. Optionally enter an XUID. Leave it blank to use the account associated with your OpenXBL API key.
7. Let the script create `XboxStatus`, `XboxAchievement`, and `XboxClips`.
8. Position and style the sources in OBS.
9. Create a disabled Move Action filter on the selected scene named: `Xbox Move Template`.
10. Enter `Xbox Move Template` in the **Move Action template filter name** setting.

## Sources created

| Source | Type | Purpose |
|---|---|---|
| `XboxStatus` | Text (GDI+) | Xbox status and current game |
| `XboxAchievement` | Text (GDI+) | Temporary achievement alert |
| `XboxClips` | Browser | Latest Xbox game clip |

Example XboxStatus output:

```text
XBOX: Online
NOW PLAYING: ARK: Survival Ascended
Currently playing ARK: Survival Ascended
```

## Move Action filters

The script uses a manually created Move Action filter as a template to identify the installed Move Action filter type. Create one disabled Move Action filter on the selected scene and name it:

```text
Xbox Move Template
```

The script uses this template only to identify the installed Move Action filter type. When a game is detected, it creates a filter such as:

```text
xbox ark: survival ascended
```

Then it activates that filter. Configure the desired movement for each newly created filter in the scene's Filters window.

Examples:

```text
xbox ark: survival ascended
xbox battlefield 6
xbox call of duty: black ops 6
xbox forza horizon 5
```

Existing Move Action filters are never disabled or reset. The script only activates a matching filter.

## Rate limiting

All OpenXBL requests share a rolling limit of 120 requests per hour. This includes presence checks, achievement checks, game-clip checks, and XUID lookups.

With the default 60-second presence interval and 300-second achievements/clips interval, the script uses approximately:

- 60 requests per hour for presence
- 12 requests per hour for achievements
- 12 requests per hour for clips
- 84 requests per hour total

You can change the request limit in the script settings. If the limit is reached, the script waits before making another request.

OpenXBL responses include rate-limit headers, and Microsoft may apply additional Xbox Live service limits. If you receive an HTTP 429 response, reduce your polling frequency or wait before retrying.

## AI usage disclosure

This script was developed with assistance from an AI coding assistant for debugging, documentation, and code organization. The repository owner reviewed, tested, and maintains the final code.

## License

MIT License. See `LICENSE`.

## Support

Report issues in this repository's Issues tab.