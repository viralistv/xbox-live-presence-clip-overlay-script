# Xbox Live Presence & Clip Overlay Script

A third-party Python script for OBS Studio that displays Xbox Live presence, achievement unlocks, and the latest Xbox game clip. It can automatically create and activate Move Action scene filters based on the active Xbox game.

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
- OpenXBL API key
- Move Action plugin
- A scene for the sources and filters

## Setup

1. Download `xbox.py`.
2. In OBS, open **Tools > Scripts**.
3. Click **+** and add `xbox.py`.
4. Enter your OpenXBL API key.
5. Set the scene name, for example `gaming`.
6. Let the script create `XboxStatus`, `XboxAchievement`, and `XboxClips`.
7. Create a disabled Move Action filter named `Xbox Move Template` on the selected scene.
8. Configure the movement for each automatically created game filter.

## Sources created

| Source | Type | Purpose |
|---|---|---|
| `XboxStatus` | Text (GDI+) | Xbox status and current game |
| `XboxAchievement` | Text (GDI+) | Temporary achievement alert |
| `XboxClips` | Browser | Latest Xbox game clip |

## Move Action filters

Create one disabled Move Action filter on the target scene named:

```text
Xbox Move Template
```

The script uses this template only to identify the installed Move Action filter type. When a game is detected, it creates a filter such as:

```text
xbox ark: survival ascended
```

Then it activates that filter. Configure the desired movement for each newly created filter in the scene's Filters window.

## Rate limiting

All OpenXBL requests share a rolling limit of 120 requests per hour. With the default 60-second presence interval and 300-second achievements/clips interval, the script uses approximately 84 requests per hour.

## AI usage disclosure

This script was developed with assistance from an AI coding assistant for debugging, documentation, and code organization. The repository owner reviewed, tested, and maintains the final code.

## License

MIT License. See `LICENSE`.

## Support

Report issues in this repository's Issues tab.